package com.orbit.launcher

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.lifecycle.lifecycleScope
import com.orbit.core.RotaryEffect
import com.orbit.core.RoundScreen
import com.orbit.launcher.apps.AppGridScreen
import com.orbit.launcher.apps.AppListScreen
import com.orbit.launcher.data.LauncherPrefs
import com.orbit.launcher.data.Notifs
import com.orbit.launcher.data.WeatherRepo
import com.orbit.launcher.data.batteryLevel
import com.orbit.launcher.data.onWifi
import com.orbit.launcher.face.FaceData
import com.orbit.launcher.face.FaceEntry
import com.orbit.launcher.face.FaceRepo
import com.orbit.launcher.face.FaceView
import com.orbit.launcher.faces.FaceCustomizeScreen
import com.orbit.launcher.faces.FacePickerScreen
import com.orbit.launcher.notifs.NotificationsScreen
import com.orbit.launcher.quick.QuickSettingsScreen
import com.orbit.launcher.tiles.TilesScreen
import com.orbit.voice.InboxActivity
import com.orbit.voice.RecorderActivity
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import java.util.Calendar
import kotlin.math.abs

/**
 * Orbit HOME. The face is home; panels open by swipe, BACK (F9) on the face opens
 * the app grid, the rotary on the face slides in the audio diary. POWER is the
 * system's: its short press brings us back via [onNewIntent] → face.
 */
class LauncherActivity : ComponentActivity(), LauncherNav {

    private var screen by mutableStateOf(Screen.FACE)
    override var editingFaceId: String? = null
    private lateinit var env: LauncherEnv
    private val data by lazy { MutableStateFlow(snapshot()) }
    private var ticker: Job? = null
    private var facePoll: Job? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Debug builds only, for adb-driven checks while the watch is locked:
        // am start -n com.orbit/com.orbit.launcher.LauncherActivity --ez overLock true
        val debuggable = applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE != 0
        if (debuggable && intent.getBooleanExtra("overLock", false)) setShowWhenLocked(true)
        com.orbit.launcher.apps.AppCache.warm(this) // first BACK into the grid already has icons
        env = LauncherEnv(FaceRepo(this), LauncherPrefs(this), data)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = back()
        })
        setContent {
            RoundScreen {
                AnimatedContent(screen, transitionSpec = { fadeIn() togetherWith fadeOut() }, label = "screen") { s ->
                    when (s) {
                        Screen.FACE -> FaceScreen()
                        Screen.APPS -> AppGridScreen(this@LauncherActivity, env)
                        Screen.APP_LIST -> AppListScreen(this@LauncherActivity, env)
                        Screen.QUICK -> QuickSettingsScreen(this@LauncherActivity, env)
                        Screen.TILES -> TilesScreen(this@LauncherActivity, env)
                        Screen.NOTIFS -> NotificationsScreen(this@LauncherActivity, env)
                        Screen.PICKER -> FacePickerScreen(this@LauncherActivity, env)
                        Screen.CUSTOMIZE -> FaceCustomizeScreen(this@LauncherActivity, env)
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // HOME pressed (POWER short press) while we are already home → back to the face.
        if (intent.hasCategory(Intent.CATEGORY_HOME)) screen = Screen.FACE
    }

    override fun onStart() {
        super.onStart()
        env.faces.start()
        // The face came on: a cheap moment to check the Orbit Inbox (at most every 2 min; docs/inbox.md).
        com.orbit.voice.inbox.InboxSync.maybeKick(this)
        lifecycleScope.launch { WeatherRepo.refresh(this@LauncherActivity) }
        // The system's own bind fails while we are not running (vendor launch gate), e.g. after
        // install or at boot; give a pending bind a moment, then make it bind us now.
        lifecycleScope.launch(kotlinx.coroutines.Dispatchers.IO) {
            delay(2_000)
            com.orbit.launcher.notifs.NotifListener.ensureBound(this@LauncherActivity)
        }
        facePoll = lifecycleScope.launch(kotlinx.coroutines.Dispatchers.IO) {
            while (true) {
                env.faces.pollForChanges()
                delay(3_000)
            }
        }
        ticker = lifecycleScope.launch {
            while (true) {
                data.value = snapshot()
                val faceUsesSeconds = currentFace()?.let { FaceData.usesSeconds(it) } ?: false
                delay(if (faceUsesSeconds && screen == Screen.FACE) 1_000 else 60_000 - System.currentTimeMillis() % 60_000)
            }
        }
    }

    override fun onStop() {
        ticker?.cancel()
        facePoll?.cancel()
        env.faces.stop()
        super.onStop()
    }

    private fun snapshot() = FaceData(
        now = Calendar.getInstance(),
        batteryLevel = batteryLevel(this),
        weatherTemp = WeatherRepo.weather.value?.temp,
        weatherCode = WeatherRepo.weather.value?.code,
        notifUnread = if (Notifs.connected.value) Notifs.items.value.size else null,
        wifi = onWifi(this),
        lte = false, // no SIM in this watch yet
    )

    private fun currentFace() = env.faces.faces.value.let { list ->
        (list.firstOrNull { it.id == env.prefs.faceId } ?: list.firstOrNull()) as? FaceEntry.Ok
    }?.face

    @Composable
    private fun FaceScreen() {
        val faces by env.faces.faces.collectAsState()
        val d by data.collectAsState()
        val entry = faces.firstOrNull { it.id == env.prefs.faceId } ?: faces.firstOrNull() ?: return
        val face = when (entry) {
            is FaceEntry.Ok -> entry.face
            is FaceEntry.Broken -> FaceRepo.errorFace(entry)
        }
        RotaryEffect { openDiary() }
        Box(
            Modifier
                .fillMaxSize()
                .pointerInput(Unit) { detectTapGestures(onLongPress = { go(Screen.PICKER) }) }
                .pointerInput(Unit) { faceSwipes() },
        ) {
            FaceView(
                face = face,
                data = d,
                accentOverride = env.prefs.accent(face.id),
                slotType = { slot, default -> env.prefs.slot(face.id, slot) ?: default },
                onComplication = ::onComplication,
            )
        }
    }

    /** Right → quick settings, left → tiles, up → notifications. Ignores drags from the top 44 units (system shade). */
    private suspend fun androidx.compose.ui.input.pointer.PointerInputScope.faceSwipes() = awaitEachGesture {
        val down = awaitFirstDown(requireUnconsumed = false)
        if (down.position.y < 44 * size.height / 400f) return@awaitEachGesture
        var dx = 0f
        var dy = 0f
        val threshold = 60 * size.width / 400f
        while (true) {
            val event = awaitPointerEvent()
            val change = event.changes.firstOrNull() ?: break
            if (!change.pressed) break
            val delta = change.positionChange()
            dx += delta.x
            dy += delta.y
            if (abs(dx) > threshold || abs(dy) > threshold) {
                change.consume()
                when {
                    abs(dx) > abs(dy) && dx > 0 -> go(Screen.QUICK)
                    abs(dx) > abs(dy) -> go(Screen.TILES)
                    dy < 0 -> go(Screen.NOTIFS)
                }
                break
            }
        }
    }

    private fun onComplication(type: String) = when (type) {
        "voice_note" -> recordNow()
        "weather" -> go(Screen.TILES)
        "notif" -> go(Screen.NOTIFS)
        "battery", "date" -> go(Screen.QUICK)
        else -> Unit
    }

    // --- LauncherNav ---------------------------------------------------------

    override fun back() {
        screen = when (screen) {
            Screen.FACE -> Screen.APPS // BACK on the face opens the app grid (Raiz, Phase 0)
            Screen.APP_LIST -> Screen.APPS
            Screen.CUSTOMIZE -> Screen.PICKER
            else -> Screen.FACE
        }
    }

    override fun go(screen: Screen) {
        this.screen = screen
    }

    override fun openDiary() {
        startActivity(Intent(this, RecorderActivity::class.java))
        @Suppress("DEPRECATION")
        overridePendingTransition(R.anim.orbit_slide_in_right, R.anim.orbit_hold)
    }

    override fun recordNow() {
        startActivity(Intent(this, RecorderActivity::class.java).setAction(RecorderActivity.ACTION_RECORD))
        @Suppress("DEPRECATION")
        overridePendingTransition(R.anim.orbit_slide_in_right, R.anim.orbit_hold)
    }

    override fun openInbox() {
        startActivity(Intent(this, InboxActivity::class.java))
    }

    override fun launch(intent: Intent) {
        runCatching { startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
    }
}
