package com.orbit.voice.upload

import android.content.Context
import com.orbit.voice.NoteMeta
import com.orbit.voice.NoteStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File

enum class NoteState { WAITING, SENDING, SENT, FAILED }

/** One note as the inbox sees it. `stem` = file name without extension. */
data class Note(
    val stem: String,
    val audio: File,
    val meta: NoteMeta?,
    val state: NoteState,
    val error: String?,
    val sentAtMs: Long?,
)

@Serializable
private data class QueueEntry(val state: NoteState, val error: String? = null, val sentAtMs: Long? = null)

/**
 * Upload state per note, kept in `files/queue/<stem>.json` (internal storage,
 * main process only). The notes folder is the source of truth: a note with no
 * entry is WAITING. Audio is never deleted here except by [deleteSentOlderThan].
 */
class Queue(context: Context) {
    private val store = NoteStore(context)
    private val dir = File(context.filesDir, "queue").apply { mkdirs() }

    fun notes(): List<Note> =
        (store.dir.listFiles { f -> f.name.endsWith(NoteStore.AUDIO_EXT) } ?: emptyArray())
            .sortedByDescending { it.name }
            .map { audio ->
                val stem = audio.name.removeSuffix(NoteStore.AUDIO_EXT)
                val entry = entry(stem)
                val live = Progress.sending.value[stem]
                Note(
                    stem = stem,
                    audio = audio,
                    meta = store.meta(stem),
                    state = if (live != null) NoteState.SENDING else entry?.state?.takeIf { it != NoteState.SENDING } ?: NoteState.WAITING,
                    error = entry?.error,
                    sentAtMs = entry?.sentAtMs,
                )
            }

    fun unsent(): List<Note> = notes().filter { it.state != NoteState.SENT }.sortedBy { it.stem }

    fun markSent(stem: String) = write(stem, QueueEntry(NoteState.SENT, sentAtMs = System.currentTimeMillis()))

    fun markFailed(stem: String, error: String) = write(stem, QueueEntry(NoteState.FAILED, error = error))

    /** The only deletion path: notes the server confirmed, older than [ageMs]. */
    fun deleteSentOlderThan(ageMs: Long, now: Long = System.currentTimeMillis()): Int {
        var n = 0
        notes().filter { it.state == NoteState.SENT && (it.sentAtMs ?: now) < now - ageMs }.forEach {
            it.audio.delete()
            File(store.dir, it.stem + ".json").delete()
            File(dir, it.stem + ".json").delete()
            n++
        }
        return n
    }

    private fun entry(stem: String): QueueEntry? =
        runCatching { JSON.decodeFromString(QueueEntry.serializer(), File(dir, "$stem.json").readText()) }.getOrNull()

    private fun write(stem: String, e: QueueEntry) {
        val tmp = File(dir, "$stem.json.tmp")
        tmp.writeText(JSON.encodeToString(QueueEntry.serializer(), e))
        tmp.renameTo(File(dir, "$stem.json"))
        Progress.changed()
    }

    private companion object {
        val JSON = Json { ignoreUnknownKeys = true }
    }
}

/** In-memory upload progress (worker and UI share the main process). */
object Progress {
    private val _sending = MutableStateFlow<Map<String, Float>>(emptyMap())
    val sending: StateFlow<Map<String, Float>> = _sending.asStateFlow()

    /** Bumped whenever queue files change, so screens reload. */
    private val _version = MutableStateFlow(0)
    val version: StateFlow<Int> = _version.asStateFlow()

    fun set(stem: String, fraction: Float) = _sending.update { it + (stem to fraction) }
    fun done(stem: String) = _sending.update { it - stem }
    fun changed() = _version.update { it + 1 }
}
