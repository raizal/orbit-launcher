package com.orbit.launcher.quick

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.view.WindowManager
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.PointerInputScope
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.orbit.core.OrbitColors
import com.orbit.core.OrbitFonts
import com.orbit.core.RotaryEffect
import com.orbit.core.SvgIcon
import com.orbit.launcher.LauncherEnv
import com.orbit.launcher.LauncherNav
import com.orbit.launcher.Screen
import com.orbit.launcher.face.MIC
import com.orbit.launcher.face.SUN
import com.orbit.voice.upload.Queue
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.abs

private val ToggleOff = Color(0xFF232321)
private val ArcTrack = Color(0xFF262624)
private val Accent = OrbitColors.Accent

/**
 * Quick settings (design/QuickSettings.dc.html): six toggles on a ring of r 124, battery in the
 * middle, brightness arc on the bottom rim (rotary changes it), a Diary pill with today's count.
 * Swipe left closes (it opened with a right swipe). BACK is the host's (→ face).
 */
@Composable
fun QuickSettingsScreen(nav: LauncherNav, env: LauncherEnv) {
    val context = LocalContext.current
    val controls = remember { QuickControls(context.applicationContext) }
    var state by remember { mutableStateOf<QuickState?>(null) }
    var todayCount by remember { mutableIntStateOf(0) }
    var hint by remember { mutableStateOf<String?>(null) }
    var light by remember { mutableStateOf(false) }
    var askedWrite by remember { mutableStateOf(false) }
    val refresh = remember { MutableStateFlow(0) }

    // Re-read on every change signal; collectLatest drops stale reads (Settings reads are IPC → IO).
    LaunchedEffect(Unit) {
        refresh.collectLatest { state = withContext(Dispatchers.IO) { controls.read() } }
    }

    // Listen to system broadcasts only while visible and resumed; re-read on return from Settings.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        var unwatch: (() -> Unit)? = null
        fun start() {
            if (unwatch == null) unwatch = controls.watch { refresh.value++ }
            refresh.value++
        }
        fun stop() { unwatch?.invoke(); unwatch = null }
        val observer = LifecycleEventObserver { _, e ->
            when (e) {
                Lifecycle.Event.ON_RESUME -> start()
                Lifecycle.Event.ON_PAUSE -> stop()
                else -> Unit
            }
        }
        lifecycle.addObserver(observer) // replays ON_RESUME if already resumed
        onDispose {
            lifecycle.removeObserver(observer)
            stop()
        }
    }

    // Today's diary notes (disk → IO), refreshed whenever the panel is shown / resumed.
    LaunchedEffect(Unit) {
        refresh.collectLatest {
            todayCount = withContext(Dispatchers.IO) {
                val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
                runCatching { QuickLogic.countToday(Queue(context.applicationContext).notes().map { it.stem }, today) }.getOrDefault(0)
            }
        }
    }

    LaunchedEffect(hint) {
        if (hint != null) {
            delay(3_000)
            hint = null
        }
    }

    ScreenLight(light)

    RotaryEffect { step ->
        val s = state ?: return@RotaryEffect
        if (light) return@RotaryEffect
        val next = QuickLogic.nextBrightness(s.brightness, step)
        if (next == s.brightness) return@RotaryEffect
        when (controls.setBrightness(next) { if (!askedWrite) nav.launch(it) }) {
            ToggleResult.OK -> state = s.copy(brightness = next)
            ToggleResult.OPENED_SETTINGS -> {
                if (askedWrite) hint = "ALLOW MODIFY SETTINGS"
                askedWrite = true
            }
            else -> hint = "BRIGHTNESS FAILED"
        }
    }

    fun flip(t: Toggle) {
        val s = state ?: return
        val result = when (t) {
            Toggle.WIFI -> controls.setWifi(!s.wifi)
            Toggle.BT -> controls.setBluetooth(!s.bt)
            Toggle.DND -> controls.setDnd(!s.dnd, nav::launch)
            Toggle.LIGHT -> ToggleResult.OK.also { light = true }
            Toggle.GPS -> controls.setLocation(!s.location)
            Toggle.SOUND -> controls.setSound(!s.sound, nav::launch)
        }
        hint = when (result) {
            ToggleResult.OK, ToggleResult.OPENED_SETTINGS -> null
            ToggleResult.NEEDS_ADB_GRANT -> "LOCATION NEEDS ADB GRANT"
            ToggleResult.UNAVAILABLE -> "NO ${t.label.uppercase()}"
            ToggleResult.FAILED -> "${t.label.uppercase()} FAILED"
        }
        refresh.value++
    }

    val s = state
    val brightness = QuickLogic.brightnessFraction(s?.brightness ?: 0)

    Box(
        Modifier
            .fillMaxSize()
            .pointerInput(Unit) { swipeLeftToClose { nav.go(Screen.FACE) } }
            .drawWithCache {
                val u = size.width / 400f
                val r = QuickLogic.ARC_RADIUS * u
                val topLeft = Offset(200 * u - r, 200 * u - r)
                val arcSize = Size(2 * r, 2 * r)
                val stroke = Stroke(QuickLogic.ARC_STROKE * u, cap = StrokeCap.Round)
                onDrawBehind {
                    drawArc(ArcTrack, QuickLogic.ARC_START_DEG, QuickLogic.ARC_SWEEP_DEG, false, topLeft, arcSize, style = stroke)
                    if (s != null && brightness > 0f) {
                        drawArc(Ink, QuickLogic.ARC_START_DEG, QuickLogic.ARC_SWEEP_DEG * brightness, false, topLeft, arcSize, style = stroke)
                    }
                }
            },
    ) {
        SvgIcon(SUN, 14.dp, OrbitColors.Muted, strokeWidth = 2.4f, modifier = Modifier.offset(82.dp, 338.dp))

        Toggle.entries.forEachIndexed { i, t ->
            val (left, top) = QuickLogic.togglePos(i)
            val on = when (t) {
                Toggle.WIFI -> s?.wifi
                Toggle.BT -> s?.bt
                Toggle.DND -> s?.dnd
                Toggle.LIGHT -> light
                Toggle.GPS -> s?.location
                Toggle.SOUND -> s?.sound
            } == true
            Box(
                Modifier
                    .offset(left.dp, top.dp)
                    .size(QuickLogic.TOGGLE_SIZE.dp)
                    .clip(CircleShape)
                    .background(if (on) Accent else ToggleOff)
                    .clickable { flip(t) },
                contentAlignment = Alignment.Center,
            ) {
                SvgIcon(t.icon, 26.dp, if (on) OrbitColors.OnAccent else Ink)
            }
        }

        Column(
            Modifier.offset(130.dp, 152.dp).width(140.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            BasicText(
                s?.battery?.let { "$it%" } ?: "--%",
                style = text(44, FontWeight.SemiBold, Ink, OrbitFonts.Grotesk, lineHeight = 44),
                maxLines = 1,
            )
            BasicText(s?.remaining ?: " ", style = text(13, FontWeight.Normal, OrbitColors.Muted, lineHeight = 16), maxLines = 1)
            Spacer(Modifier.height(6.dp))
            val h = hint
            BasicText(
                h ?: s?.let { QuickLogic.status(it.dnd, it.wifiConnected, it.lte, it.simAbsent) } ?: " ",
                style = text(12, FontWeight.SemiBold, if (h != null) OrbitColors.Rec else Accent, lineHeight = 15)
                    .copy(letterSpacing = 0.06.em, textAlign = TextAlign.Center),
                maxLines = 2,
            )
        }

        DiaryPill(todayCount, onClick = nav::openInbox)

        if (light) {
            Box(
                Modifier
                    .fillMaxSize()
                    .background(Color.White)
                    .pointerInput(Unit) { detectTapGestures { light = false } },
            )
        }
    }
}

private val Ink = OrbitColors.Ink

/**
 * Not in the mockup: the Diary shortcut (brief). Sits in the free band between the battery block
 * (ends ~237) and the bottom toggle (starts 288), clear of the 30°/150° toggles (x 129..271).
 */
@Composable
private fun DiaryPill(count: Int, onClick: () -> Unit) {
    Row(
        Modifier
            .offset(136.dp, 242.dp)
            .size(128.dp, 40.dp)
            .clip(RoundedCornerShape(50))
            .background(ToggleOff)
            .clickable(onClick = onClick),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SvgIcon(MIC, 16.dp, Accent, strokeWidth = 2.2f)
        Spacer(Modifier.width(6.dp))
        BasicText("Diary", style = text(13, FontWeight.Medium, Ink, lineHeight = 16), maxLines = 1)
        Spacer(Modifier.width(8.dp))
        BasicText("$count", style = text(15, FontWeight.SemiBold, Accent, OrbitFonts.Grotesk, lineHeight = 16), maxLines = 1)
    }
}

/** Full-white screen at full window brightness (kept on) while [on]; restored when off or the panel closes. */
@Composable
private fun ScreenLight(on: Boolean) {
    val activity = LocalContext.current.findActivity() ?: return
    DisposableEffect(on) {
        if (!on) return@DisposableEffect onDispose { }
        val window = activity.window
        window.attributes = window.attributes.apply { screenBrightness = WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_FULL }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        onDispose {
            window.attributes = window.attributes.apply { screenBrightness = WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE }
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
    }
}

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

/**
 * Closes on a leftward swipe (opposite of the face's right swipe that opened us). Watches the
 * Initial pass so a swipe that starts on a toggle is consumed before the toggle sees a tap.
 */
private suspend fun PointerInputScope.swipeLeftToClose(onClose: () -> Unit) = awaitEachGesture {
    val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
    if (down.position.y < 44 * size.height / 400f) return@awaitEachGesture // system shade zone
    var dx = 0f
    var dy = 0f
    val threshold = 60 * size.width / 400f
    while (true) {
        val event = awaitPointerEvent(PointerEventPass.Initial)
        val change = event.changes.firstOrNull { it.id == down.id } ?: break
        if (!change.pressed) break
        val delta = change.positionChange()
        dx += delta.x
        dy += delta.y
        if (abs(dx) > threshold || abs(dy) > threshold) {
            if (dx < 0 && abs(dx) > abs(dy)) {
                change.consume()
                onClose()
            }
            break
        }
    }
}

private fun text(
    size: Int,
    weight: FontWeight,
    color: Color,
    font: androidx.compose.ui.text.font.FontFamily = OrbitFonts.Inter,
    lineHeight: Int,
) = TextStyle(
    fontSize = size.sp,
    fontWeight = weight,
    fontFamily = font,
    color = color,
    lineHeight = lineHeight.sp,
    lineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None),
)
