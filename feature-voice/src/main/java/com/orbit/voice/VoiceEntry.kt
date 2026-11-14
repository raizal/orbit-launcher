package com.orbit.voice

import android.content.Context
import android.content.Intent
import android.os.VibrationEffect
import android.os.Vibrator

/** The one entry point every Orbit shortcut uses to start/stop a voice note. */
object VoiceEntry {

    /**
     * Starts the recorder service and buzzes. Caller must hold RECORD_AUDIO.
     * Returns the press time, which names the file. [mode] goes into the sidecar
     * (`"talk"` for a Talk turn); null = normal note.
     */
    fun startRecording(context: Context, mode: String? = null): Long {
        val pressedAt = System.currentTimeMillis()
        context.startForegroundService(
            Intent(context, RecorderService::class.java)
                .setAction(RecorderService.ACTION_START)
                .putExtra(RecorderService.EXTRA_PRESSED_AT, pressedAt)
                .apply { if (mode != null) putExtra(RecorderService.EXTRA_MODE, mode) }
        )
        context.getSystemService(Vibrator::class.java)
            ?.vibrate(VibrationEffect.createOneShot(40, VibrationEffect.DEFAULT_AMPLITUDE))
        return pressedAt
    }

    fun stopRecording(context: Context) {
        context.startService(Intent(context, RecorderService::class.java).setAction(RecorderService.ACTION_STOP))
    }
}
