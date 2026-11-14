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
     * (`"talk"` for a Talk turn); null = normal note. [conversation] = `"new"` on
     * the first Talk turn after New; null = continue the latest conversation.
     */
    fun startRecording(context: Context, mode: String? = null, conversation: String? = null): Long {
        val pressedAt = System.currentTimeMillis()
        context.startForegroundService(
            Intent(context, RecorderService::class.java)
                .setAction(RecorderService.ACTION_START)
                .putExtra(RecorderService.EXTRA_PRESSED_AT, pressedAt)
                .apply { if (mode != null) putExtra(RecorderService.EXTRA_MODE, mode) }
                .apply { if (conversation != null) putExtra(RecorderService.EXTRA_CONVERSATION, conversation) }
        )
        context.getSystemService(Vibrator::class.java)
            ?.vibrate(VibrationEffect.createOneShot(40, VibrationEffect.DEFAULT_AMPLITUDE))
        return pressedAt
    }

    fun stopRecording(context: Context) {
        context.startService(Intent(context, RecorderService::class.java).setAction(RecorderService.ACTION_STOP))
    }
}
