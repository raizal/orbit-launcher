package com.orbit.voice

import java.io.File

/**
 * Cancelled recordings. Cancel is the one user action that deletes unsent audio,
 * so it is made durable before anything else happens: the UI writes
 * `<stamp>.aac.cancel` next to the temp file `<stamp>.aac.rec`, *then* asks the
 * service to stop. Every path that would turn a temp file into a note (service
 * stop, orphan recovery after a crash) checks the marker first, so a cancelled
 * recording can never come back as a normal note — even if `:voice` dies mid-cancel.
 *
 * Pure file logic (no Android) so it is unit-tested on the JVM.
 */
object Discards {
    const val SUFFIX = ".cancel"

    /** [audioName] = the final note name, `<stamp>.aac`. */
    fun marker(dir: File, audioName: String) = File(dir, audioName + SUFFIX)

    fun mark(dir: File, audioName: String) {
        marker(dir, audioName).createNewFile()
    }

    /** True if the temp file `<stamp>.aac.rec` was cancelled. */
    fun isCancelled(temp: File): Boolean =
        marker(temp.parentFile!!, temp.name.removeSuffix(NoteStore.TEMP_SUFFIX)).exists()

    /** Deletes a cancelled temp file, then its marker (marker last: a crash in between is retried). */
    fun discard(temp: File): Boolean {
        val gone = !temp.exists() || temp.delete()
        if (gone) marker(temp.parentFile!!, temp.name.removeSuffix(NoteStore.TEMP_SUFFIX)).delete()
        return gone
    }

    /**
     * Deletes cancelled temp files and markers left without a temp file, both
     * only if untouched for [minIdleMs] (a recording in progress is always fresh).
     * Never touches finalized notes or temp files without a marker.
     */
    fun sweep(dir: File, minIdleMs: Long, now: Long = System.currentTimeMillis()): Int {
        var n = 0
        dir.listFiles { f -> f.name.endsWith(NoteStore.TEMP_SUFFIX) && now - f.lastModified() >= minIdleMs }
            ?.filter(::isCancelled)
            ?.forEach { if (discard(it)) n++ }
        dir.listFiles { f -> f.name.endsWith(SUFFIX) && now - f.lastModified() >= minIdleMs }?.forEach { m ->
            if (!File(dir, m.name.removeSuffix(SUFFIX) + NoteStore.TEMP_SUFFIX).exists()) m.delete()
        }
        return n
    }
}
