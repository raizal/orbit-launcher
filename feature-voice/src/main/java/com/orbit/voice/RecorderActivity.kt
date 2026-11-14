package com.orbit.voice

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import com.orbit.core.RoundScreen
import com.orbit.voice.talk.TalkActivity
import com.orbit.voice.upload.Queue

/**
 * Recorder screen. Opened by the app icon, or by `com.orbit.action.RECORD`
 * (app shortcut, adb), which starts recording before the UI is drawn so the
 * vibration confirms the press even while the screen is still loading.
 */
class RecorderActivity : ComponentActivity() {

    private lateinit var store: NoteStore
    private var ui by mutableStateOf<RecorderUi>(RecorderUi.Idle)
    private var waiting by mutableIntStateOf(0)
    private var levels by mutableStateOf(FloatArray(TICKS))
    private var startWhenPermitted = false
    private var lastTick = 0L
    private val handler = Handler(Looper.getMainLooper())

    /** If the service died mid-recording no "saved" ever arrives; notice it and recover. */
    private val watchdog = object : Runnable {
        override fun run() {
            if (ui is RecorderUi.Recording && SystemClock.elapsedRealtime() - lastTick > 2_000) syncWithService()
            handler.postDelayed(this, 1_000)
        }
    }

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val state = RecorderState.parse(intent) ?: return
            if (state is RecorderUi.Recording) {
                lastTick = SystemClock.elapsedRealtime()
                pushLevel(state.level)
            }
            if (state is RecorderUi.Saved || state is RecorderUi.Error) waiting = waitingCount()
            ui = state
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Record without unlocking (like a lock-screen camera). Nothing private is shown here.
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        store = NoteStore(this)
        if (intent?.action == ACTION_RECORD) record()
        setContent {
            RoundScreen {
                RecorderScreen(
                    state = ui,
                    waiting = waiting,
                    levels = levels,
                    onStart = ::record,
                    onStop = { VoiceEntry.stopRecording(this) },
                    onNewNote = { ui = RecorderUi.Idle },
                    onInbox = { startActivity(Intent(this, InboxActivity::class.java)) },
                    onTalk = { startActivity(Intent(this, TalkActivity::class.java)) },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.action == ACTION_RECORD && ui !is RecorderUi.Recording) record()
    }

    override fun onStart() {
        super.onStart()
        ContextCompat.registerReceiver(this, receiver, IntentFilter(RecorderState.ACTION), ContextCompat.RECEIVER_NOT_EXPORTED)
        syncWithService()
        handler.post(watchdog)
    }

    private fun syncWithService() {
        // Catch up with the service: it may have started or stopped while we were away.
        val marker = RecorderState.readMarker(this)
        val current = ui
        ui = when {
            marker != null -> marker
            // Just pressed: the service hasn't written its marker yet.
            current is RecorderUi.Recording && SystemClock.elapsedRealtime() - current.startElapsed < 3_000 -> current
            current is RecorderUi.Recording -> RecorderUi.Idle
            else -> current
        }
        if (marker == null) store.recoverOrphans(minIdleMs = 10_000)
        waiting = waitingCount()
    }

    override fun onStop() {
        handler.removeCallbacks(watchdog)
        unregisterReceiver(receiver)
        super.onStop()
    }

    private fun record() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            startWhenPermitted = true
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            return
        }
        val pressedAt = VoiceEntry.startRecording(this)
        // Show "recording" right away; the service's first tick (≈100 ms) confirms it.
        levels = FloatArray(TICKS)
        lastTick = SystemClock.elapsedRealtime()
        ui = RecorderUi.Recording(store.stampFor(pressedAt) + NoteStore.AUDIO_EXT, SystemClock.elapsedRealtime(), 0f)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_MIC) return
        val granted = grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED
        if (granted && startWhenPermitted) record()
        if (!granted) ui = RecorderUi.Error("Microphone permission needed")
        startWhenPermitted = false
    }

    private fun waitingCount() = Queue(this).unsent().size + store.tempCount()

    /** Newest level at index 0 (12 o'clock); older ones move clockwise. */
    private fun pushLevel(level: Float) {
        val next = FloatArray(TICKS)
        System.arraycopy(levels, 0, next, 1, TICKS - 1)
        next[0] = level
        levels = next
    }

    companion object {
        const val ACTION_RECORD = "com.orbit.action.RECORD"
        private const val REQ_MIC = 1
    }
}
