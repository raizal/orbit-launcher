package com.orbit.voice.talk

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.orbit.core.RoundScreen
import com.orbit.voice.NoteStore
import com.orbit.voice.RecorderState
import com.orbit.voice.RecorderUi
import com.orbit.voice.TICKS
import com.orbit.voice.VoiceEntry
import com.orbit.voice.upload.NoteState
import com.orbit.voice.upload.OrbitClient
import com.orbit.voice.upload.Progress
import com.orbit.voice.upload.Queue
import com.orbit.voice.upload.ServerPrefs
import com.orbit.voice.upload.Uploads
import com.orbit.voice.upload.WifiNow
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.io.File
import kotlin.coroutines.resume

/**
 * Talk — journaling with spoken feedback (docs/talk.md). One turn = a normal
 * voice note recorded by [com.orbit.voice.RecorderService] with sidecar
 * `mode: "talk"`; after it is uploaded we poll the companion for its reply,
 * then play the reply audio and show the text.
 *
 * The note is safe the moment the service saves it; everything after that is
 * best effort and can be abandoned (back, timeout) without losing anything.
 */
class TalkActivity : ComponentActivity() {

    private lateinit var store: NoteStore
    private var ui by mutableStateOf<TalkUi>(TalkUi.Idle())
    private var levels by mutableStateOf(FloatArray(TICKS))
    private var serverName by mutableStateOf<String?>(null)
    private var startWhenPermitted = false
    private var lastTick = 0L
    private var turn: Job? = null
    private var player: MediaPlayer? = null
    private val handler = Handler(Looper.getMainLooper())
    private val releaseScreen = Runnable { window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }

    /** If the service died mid-recording no "saved" ever arrives; don't wait forever. */
    private val watchdog = object : Runnable {
        override fun run() {
            if (ui is TalkUi.Listening && SystemClock.elapsedRealtime() - lastTick > 3_000 &&
                RecorderState.readMarker(this@TalkActivity) == null
            ) {
                dispatch(TalkEvent.RecError("Recording stopped — kept as a note"))
            }
            handler.postDelayed(this, 1_000)
        }
    }

    // Registered for the whole activity lifetime (not just while visible) so a turn
    // stopped from the notification while the screen is off still continues.
    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (val state = RecorderState.parse(intent) ?: return) {
                is RecorderUi.Recording -> {
                    lastTick = SystemClock.elapsedRealtime()
                    if (ui !is TalkUi.Listening) dispatch(TalkEvent.RecStarted(state.startElapsed))
                    pushLevel(state.level)
                    dispatch(TalkEvent.Level(state.level))
                }
                is RecorderUi.Saved -> if (ui is TalkUi.Listening) onSaved(state.fileName.removeSuffix(NoteStore.AUDIO_EXT))
                is RecorderUi.Error -> dispatch(TalkEvent.RecError(state.message))
                is RecorderUi.Idle -> Unit
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Same as the recorder: usable without unlocking. Only the reply to what was just said is shown.
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        store = NoteStore(this)
        ContextCompat.registerReceiver(this, receiver, IntentFilter(RecorderState.ACTION), ContextCompat.RECEIVER_NOT_EXPORTED)
        setContent {
            RoundScreen {
                TalkScreen(
                    state = ui,
                    levels = levels,
                    serverName = serverName,
                    onStart = ::record,
                    onStop = { VoiceEntry.stopRecording(this) },
                    onSkip = {
                        stopPlayback()
                        dispatch(TalkEvent.PlaybackEnded) // also if the audio is still downloading
                    },
                    onAgain = {
                        stopPlayback()
                        dispatch(TalkEvent.Reset)
                        record()
                    },
                )
            }
        }
    }

    override fun onStart() {
        super.onStart()
        serverName = ServerPrefs(this).pairing?.name
        // A recording already running (e.g. reopened mid-turn): show it as listening.
        RecorderState.readMarker(this)?.let {
            lastTick = SystemClock.elapsedRealtime()
            dispatch(TalkEvent.RecStarted(it.startElapsed))
        }
        handler.post(watchdog)
    }

    override fun onStop() {
        handler.removeCallbacks(watchdog)
        super.onStop()
    }

    override fun onDestroy() {
        unregisterReceiver(receiver)
        handler.removeCallbacks(releaseScreen)
        releasePlayer()
        super.onDestroy()
    }

    private fun dispatch(e: TalkEvent) {
        val next = TalkMachine.reduce(ui, e)
        if (next == ui) return
        ui = next
        handler.removeCallbacks(releaseScreen)
        if (TalkMachine.keepScreenOn(next)) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            // Done: give time to read, then let the normal screen timeout apply.
            if (next is TalkUi.Done) handler.postDelayed(releaseScreen, READ_MS)
        } else {
            releaseScreen.run()
        }
    }

    private fun record() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            startWhenPermitted = true
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            return
        }
        if (ui is TalkUi.Listening || ui is TalkUi.Waiting) return
        turn?.cancel()
        VoiceEntry.startRecording(this, MODE_TALK)
        levels = FloatArray(TICKS)
        lastTick = SystemClock.elapsedRealtime()
        dispatch(TalkEvent.RecStarted(SystemClock.elapsedRealtime()))
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_MIC) return
        val granted = grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED
        if (granted && startWhenPermitted) record()
        if (!granted) ui = TalkUi.Idle("Microphone permission needed")
        startWhenPermitted = false
    }

    private fun onSaved(stem: String) {
        dispatch(TalkEvent.Saved)
        turn?.cancel()
        turn = lifecycleScope.launch { runTurn(stem) }
    }

    /** Upload → poll the reply → play it. Every exit leaves the note queued as usual. */
    private suspend fun runTurn(stem: String) {
        val meta = withContext(Dispatchers.IO) { store.meta(stem) }
        if (meta == null || meta.mode != MODE_TALK) {
            // A normal recording that was already running when Talk opened.
            dispatch(TalkEvent.ReplyUpdate(Reply.Failed("Saved as a normal note")))
            return
        }
        Uploads.kick(this, fresh = true)
        val prefs = ServerPrefs(this)
        val pairing = prefs.pairing ?: return dispatch(TalkEvent.Offline("Not paired"))
        val wifi = WifiNow.read(this) ?: return dispatch(TalkEvent.Offline("No Wi-Fi"))
        val home = prefs.home
        if (home == null || !wifi.matches(home)) return dispatch(TalkEvent.Offline("Not on home Wi-Fi"))

        awaitSent(stem)?.let { return dispatch(TalkEvent.Offline(it)) }
        dispatch(TalkEvent.Uploaded)

        val ip = prefs.lastIp ?: return dispatch(TalkEvent.Offline("Companion not found"))
        val client = OrbitClient(wifi.network.socketFactory)
        val deadline = SystemClock.elapsedRealtime() + REPLY_GIVE_UP_MS
        var done: Reply.Done? = null
        while (done == null) {
            val r = withContext(Dispatchers.IO) {
                runCatching { client.reply(ip, pairing.port, pairing.token, meta.id) }
                    .onFailure { Log.w(TAG, "reply poll failed", it) }
                    .getOrNull()
            }
            if (r != null) {
                dispatch(TalkEvent.ReplyUpdate(r))
                when (r) {
                    is Reply.Done -> done = r
                    is Reply.Failed, Reply.NotFound -> return
                    else -> Unit
                }
            }
            if (done == null) {
                if (SystemClock.elapsedRealtime() > deadline) return dispatch(TalkEvent.TimedOut)
                delay(REPLY_POLL_MS)
            }
        }
        play(client, ip, pairing.port, pairing.token, done)
        dispatch(TalkEvent.PlaybackEnded)
    }

    /**
     * Waits until the queue worker has sent [stem]. Returns null when sent, or a
     * reason when it is not happening now (the note just stays queued).
     */
    private suspend fun awaitSent(stem: String): String? {
        val queue = Queue(this)
        val start = SystemClock.elapsedRealtime()
        var lastActivity = start
        while (true) {
            val (state, error) = withContext(Dispatchers.IO) { queue.status(stem) }
            val now = SystemClock.elapsedRealtime()
            if (state == NoteState.SENT) return null
            if (Progress.sending.value.isNotEmpty()) lastActivity = now
            // FAILED can be a cancelled run that the fresh kick is about to redo: wait a bit.
            if (state == NoteState.FAILED && now - lastActivity > FAILED_GRACE_MS) return error ?: "Upload failed"
            if (now - lastActivity > IDLE_GIVE_UP_MS || now - start > REPLY_GIVE_UP_MS) return "Companion not found"
            delay(400)
        }
    }

    /**
     * Downloads the reply WAV over the Wi-Fi-bound client, then plays the local
     * file. (MediaPlayer streams from the system media server, which would not use
     * our Wi-Fi binding, so we fetch it ourselves.) Text stays on screen either way.
     */
    private suspend fun play(client: OrbitClient, ip: String, port: Int, token: String, reply: Reply.Done) {
        val file = File(cacheDir, "talk-reply.wav")
        val ok = withContext(Dispatchers.IO) {
            runCatching { client.download(ip, port, token, reply.audioPath, file) }
                .onFailure { Log.w(TAG, "reply audio download failed", it) }
                .isSuccess
        }
        if (!ok || ui !is TalkUi.Speaking) return
        suspendCancellableCoroutine { cont ->
            val mp = MediaPlayer()
            player = mp
            val finish = {
                if (player === mp) releasePlayer()
                if (cont.isActive) cont.resume(Unit)
            }
            try {
                mp.setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                mp.setDataSource(file.path)
                mp.setOnCompletionListener { finish() }
                mp.setOnErrorListener { _, what, extra -> Log.w(TAG, "playback error $what/$extra"); finish(); true }
                mp.prepare()
                mp.start()
            } catch (e: Exception) {
                Log.w(TAG, "playback failed", e)
                finish()
            }
            onStopped = finish
            cont.invokeOnCancellation { handler.post { if (player === mp) releasePlayer() } }
        }
        onStopped = null
    }

    private var onStopped: (() -> Unit)? = null

    /** "Skip": stop the reply audio; the turn moves on to done. */
    private fun stopPlayback() {
        onStopped?.invoke() ?: releasePlayer()
    }

    private fun releasePlayer() {
        player?.let { runCatching { it.stop() }; it.release() }
        player = null
    }

    /** Newest level at index 0 (12 o'clock); older ones move clockwise. */
    private fun pushLevel(level: Float) {
        val next = FloatArray(TICKS)
        System.arraycopy(levels, 0, next, 1, TICKS - 1)
        next[0] = level
        levels = next
    }

    companion object {
        const val ACTION_TALK = "com.orbit.action.TALK"
        private const val TAG = "OrbitTalk"
        private const val REQ_MIC = 2
        private const val IDLE_GIVE_UP_MS = 30_000L
        private const val FAILED_GRACE_MS = 8_000L
        private const val READ_MS = 60_000L
    }
}
