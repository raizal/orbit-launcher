package com.orbit.voice

import android.content.Context
import android.content.Intent
import android.os.SystemClock
import java.io.File

/** What the recorder screen shows. */
sealed interface RecorderUi {
    data object Idle : RecorderUi
    data class Recording(val fileName: String, val startElapsed: Long, val level: Float) : RecorderUi
    data class Saved(val fileName: String, val durationMs: Long) : RecorderUi
    data class Error(val message: String) : RecorderUi
    /** The recording [fileName] was cancelled and deleted; nothing was saved. */
    data class Cancelled(val fileName: String) : RecorderUi
}

/**
 * Cross-process contract between [RecorderService] (`:voice`) and the UI.
 * Live updates are broadcasts; the marker file lets a freshly opened UI show
 * "recording" on its first frame. `elapsedRealtime` is system-wide, so it is
 * valid in both processes.
 */
object RecorderState {
    const val ACTION = "com.orbit.voice.STATE"
    private const val KIND = "kind"
    private const val NAME = "name"
    private const val START = "start"
    private const val LEVEL = "level"
    private const val DURATION = "duration"
    private const val MESSAGE = "message"

    fun recording(name: String, startElapsed: Long, level: Float) = Intent(ACTION)
        .putExtra(KIND, "rec").putExtra(NAME, name).putExtra(START, startElapsed).putExtra(LEVEL, level)

    fun saved(name: String, durationMs: Long) = Intent(ACTION)
        .putExtra(KIND, "saved").putExtra(NAME, name).putExtra(DURATION, durationMs)

    fun error(message: String) = Intent(ACTION).putExtra(KIND, "error").putExtra(MESSAGE, message)

    fun cancelled(name: String) = Intent(ACTION).putExtra(KIND, "cancelled").putExtra(NAME, name)

    fun parse(intent: Intent): RecorderUi? = when (intent.getStringExtra(KIND)) {
        "rec" -> RecorderUi.Recording(
            intent.getStringExtra(NAME).orEmpty().removeSuffix(NoteStore.TEMP_SUFFIX),
            intent.getLongExtra(START, 0),
            intent.getFloatExtra(LEVEL, 0f),
        )
        "saved" -> RecorderUi.Saved(intent.getStringExtra(NAME).orEmpty(), intent.getLongExtra(DURATION, 0))
        "error" -> RecorderUi.Error(intent.getStringExtra(MESSAGE).orEmpty())
        "cancelled" -> RecorderUi.Cancelled(intent.getStringExtra(NAME).orEmpty())
        else -> null
    }

    private fun marker(context: Context) = File(context.filesDir, "recording.marker")

    fun writeMarker(context: Context, tempName: String, startElapsed: Long) =
        marker(context).writeText("$tempName\n$startElapsed")

    fun clearMarker(context: Context) {
        marker(context).delete()
    }

    /** Recording in progress per the marker, if its temp file is still being written. */
    fun readMarker(context: Context): RecorderUi.Recording? {
        val lines = runCatching { marker(context).readLines() }.getOrNull() ?: return null
        val name = lines.getOrNull(0) ?: return null
        val start = lines.getOrNull(1)?.toLongOrNull() ?: return null
        if (start > SystemClock.elapsedRealtime()) return null // from before a reboot
        val temp = File(NoteStore(context).dir, name)
        val fresh = temp.exists() && System.currentTimeMillis() - temp.lastModified() < 5_000
        return if (fresh) RecorderUi.Recording(name.removeSuffix(NoteStore.TEMP_SUFFIX), start, 0f) else null
    }
}
