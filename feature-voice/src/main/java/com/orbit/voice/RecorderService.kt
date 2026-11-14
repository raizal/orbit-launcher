package com.orbit.voice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.media.MediaRecorder
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import com.orbit.voice.upload.NoteSavedReceiver
import java.io.File
import kotlin.math.log10

/**
 * Foreground service that owns the microphone. Runs in its own `:voice` process
 * so a UI/launcher crash never stops a recording. Talks to the UI with
 * package-scoped broadcasts ([RecorderState]) and a marker file for the UI's
 * first frame.
 */
class RecorderService : Service() {

    private lateinit var store: NoteStore
    private var recorder: MediaRecorder? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var temp: File? = null
    private var startedAtMs = 0L
    private var startElapsed = 0L
    private val handler = Handler(Looper.getMainLooper())

    private val ticker = object : Runnable {
        override fun run() {
            val r = recorder ?: return
            broadcast(RecorderState.recording(temp!!.name, startElapsed, level(r.maxAmplitude)))
            handler.postDelayed(this, TICK_MS)
        }
    }

    override fun onCreate() {
        super.onCreate()
        store = NoteStore(this)
        // A fresh service instance means nothing is recording: safe to finalize leftovers.
        RecorderState.clearMarker(this)
        store.recoverOrphans()
        NoteSavedReceiver.notify(this)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> start(intent.getLongExtra(EXTRA_PRESSED_AT, System.currentTimeMillis()))
            ACTION_STOP -> stop()
            else -> if (recorder == null) stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun start(pressedAtMs: Long) {
        if (recorder != null) return
        startForeground(NOTIFICATION_ID, notification())

        val stamp = store.stampFor(pressedAtMs)
        val file = store.tempFile(stamp)
        val r = MediaRecorder()
        try {
            r.setAudioSource(MediaRecorder.AudioSource.MIC)
            r.setOutputFormat(MediaRecorder.OutputFormat.AAC_ADTS)
            r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            r.setAudioSamplingRate(16_000)
            r.setAudioChannels(1)
            r.setAudioEncodingBitRate(NoteStore.BITRATE)
            r.setOutputFile(file.absolutePath)
            r.prepare()
            r.start()
        } catch (e: Exception) {
            Log.e(TAG, "start failed", e)
            r.release()
            broadcast(RecorderState.error("Mic unavailable"))
            stopForeground(true)
            stopSelf()
            return
        }
        recorder = r
        temp = file
        startedAtMs = pressedAtMs
        startElapsed = SystemClock.elapsedRealtime()
        wakeLock = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "orbit:voice")
            .apply { acquire(MAX_RECORDING_MS) }
        RecorderState.writeMarker(this, file.name, startElapsed)
        handler.post(ticker)
        Log.i(TAG, "recording ${file.name}")
    }

    private fun stop() {
        val r = recorder ?: run { stopSelf(); return }
        handler.removeCallbacks(ticker)
        val durationMs = SystemClock.elapsedRealtime() - startElapsed
        val stopped = runCatching { r.stop() }
            .onFailure { Log.e(TAG, "stop failed; temp file kept for recovery", it) }
            .isSuccess
        r.release()
        recorder = null
        wakeLock?.takeIf { it.isHeld }?.release()
        RecorderState.clearMarker(this)

        val file = temp!!
        if (stopped) {
            runCatching { store.finalize(file, startedAtMs, durationMs) }
                .onSuccess {
                    broadcast(RecorderState.saved(it.name, durationMs))
                    NoteSavedReceiver.notify(this)
                }
                .onFailure {
                    Log.e(TAG, "finalize failed; temp file kept", it)
                    broadcast(RecorderState.error("Saved for recovery"))
                }
        } else {
            // Too short or encoder error: leave the temp file; recoverOrphans() decides later.
            broadcast(RecorderState.error("Too short"))
        }
        stopForeground(true)
        stopSelf()
    }

    override fun onDestroy() {
        if (recorder != null) stop()
        super.onDestroy()
    }

    private fun broadcast(state: Intent) = sendBroadcast(state.setPackage(packageName))

    /** maxAmplitude (0..32767) → 0..1 on a −50 dB..0 dB scale. */
    private fun level(amp: Int): Float {
        if (amp <= 0) return 0f
        val db = 20 * log10(amp / 32767f)
        return ((db + 50f) / 50f).coerceIn(0f, 1f)
    }

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Recording", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, RecorderActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, RecorderService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle("Recording voice note")
            .setUsesChronometer(true)
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(Notification.Action.Builder(null, "Stop", stop).build())
            .build()
    }

    companion object {
        const val ACTION_START = "com.orbit.voice.START"
        const val ACTION_STOP = "com.orbit.voice.STOP"
        const val EXTRA_PRESSED_AT = "pressed_at"
        private const val TAG = "OrbitVoice"
        private const val CHANNEL = "recording"
        private const val NOTIFICATION_ID = 7
        private const val TICK_MS = 100L
        private const val MAX_RECORDING_MS = 4 * 60 * 60 * 1000L
    }
}
