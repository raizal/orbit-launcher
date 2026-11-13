package com.orbit.probe

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.media.MediaRecorder
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Phase 0 probe: records with the planned settings (AAC-LC, 16 kHz, mono, 32 kbps)
 * in either MP4 (.m4a) or ADTS (.aac) container, and logs a heartbeat every 30 s
 * so we can see in logcat whether the vendor power manager kills it with the screen off.
 */
class ProbeRecordService : Service() {

    private var recorder: MediaRecorder? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var startedAt = 0L
    private val handler = Handler(Looper.getMainLooper())
    private val heartbeat = object : Runnable {
        override fun run() {
            val sec = (SystemClock.elapsedRealtime() - startedAt) / 1000
            Log.i(TAG, "heartbeat recording ${sec}s pid=${android.os.Process.myPid()} amp=${recorder?.maxAmplitude}")
            handler.postDelayed(this, 30_000)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> start(intent.getStringExtra(EXTRA_FORMAT) ?: FORMAT_M4A)
            ACTION_STOP -> stop()
        }
        return START_NOT_STICKY
    }

    private fun start(format: String) {
        if (recorder != null) {
            Log.w(TAG, "already recording")
            return
        }
        startForeground(NOTIFICATION_ID, notification("Recording $format"))

        val dir = File(getExternalFilesDir(null), "probe").apply { mkdirs() }
        val stamp = SimpleDateFormat("yyyy-MM-dd_HH-mm-ssZ", Locale.US).format(Date())
        val file = File(dir, "probe-$stamp.$format")

        val r = MediaRecorder()
        try {
            r.setAudioSource(MediaRecorder.AudioSource.MIC)
            r.setOutputFormat(
                if (format == FORMAT_AAC) MediaRecorder.OutputFormat.AAC_ADTS
                else MediaRecorder.OutputFormat.MPEG_4
            )
            r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            r.setAudioSamplingRate(16_000)
            r.setAudioChannels(1)
            r.setAudioEncodingBitRate(32_000)
            r.setOutputFile(file.absolutePath)
            r.prepare()
            r.start()
        } catch (e: Exception) {
            Log.e(TAG, "start failed for $format", e)
            r.release()
            stopForeground(true)
            stopSelf()
            return
        }
        recorder = r
        wakeLock = (getSystemService(Context.POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "orbit:probe-record")
            .apply { acquire(2 * 60 * 60 * 1000L) }
        startedAt = SystemClock.elapsedRealtime()
        handler.post(heartbeat)
        Log.i(TAG, "started $format -> ${file.absolutePath} pid=${android.os.Process.myPid()}")
    }

    private fun stop() {
        handler.removeCallbacks(heartbeat)
        recorder?.let {
            try {
                it.stop()
                Log.i(TAG, "stopped ok after ${(SystemClock.elapsedRealtime() - startedAt) / 1000}s")
            } catch (e: RuntimeException) {
                Log.e(TAG, "stop failed (file may be empty)", e)
            }
            it.release()
        }
        recorder = null
        wakeLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
        stopForeground(true)
        stopSelf()
    }

    override fun onDestroy() {
        Log.w(TAG, "onDestroy (recording=${recorder != null})")
        if (recorder != null) stop()
        super.onDestroy()
    }

    private fun notification(text: String): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "Recording", NotificationManager.IMPORTANCE_LOW)
        )
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Orbit probe")
            .setContentText(text)
            .setOngoing(true)
            .build()
    }

    companion object {
        const val TAG = "OrbitProbe"
        const val ACTION_START = "com.orbit.probe.START"
        const val ACTION_STOP = "com.orbit.probe.STOP"
        const val EXTRA_FORMAT = "format"
        const val FORMAT_M4A = "m4a"
        const val FORMAT_AAC = "aac"
        private const val CHANNEL = "probe-record"
        private const val NOTIFICATION_ID = 1
    }
}
