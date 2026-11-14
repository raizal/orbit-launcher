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

/**
 * One `files/queue/<stem>.json`. "Sent" is per companion: [sentTo] maps a pairing
 * fingerprint ([Pairing.fingerprint]) to when that companion confirmed the note.
 * A note is SENT only for the companion in [sentTo] that is paired *now*.
 *
 * [state] is only WAITING or FAILED in entries we write. SENT in [state] (with
 * [sentAtMs]) is the legacy format — "sent somewhere, server unknown" — which
 * builds before per-server tracking write; we never write it, so such a build
 * reading our files treats them as unsent (re-checks, never deletes).
 */
@Serializable
internal data class QueueEntry(
    val state: NoteState = NoteState.WAITING,
    val error: String? = null,
    val sentAtMs: Long? = null,
    val sentTo: Map<String, Long> = emptyMap(),
)

/** The per-companion "sent" rules, free of Android so they are unit-tested. */
internal object QueueLogic {
    data class View(val state: NoteState, val error: String?, val sentAtMs: Long?)

    /** What [e] means while [server] (fingerprint, null = unpaired) is paired. */
    fun view(e: QueueEntry?, server: String?): View {
        if (e == null) return View(NoteState.WAITING, null, null)
        val at = server?.let { e.sentTo[it] }
        return when {
            at != null -> View(NoteState.SENT, null, at)
            e.state == NoteState.FAILED -> View(NoteState.FAILED, e.error, null)
            else -> View(NoteState.WAITING, null, null) // incl. sent to another companion, or legacy
        }
    }

    fun sent(e: QueueEntry?, server: String, now: Long): QueueEntry =
        QueueEntry(NoteState.WAITING, sentTo = (e?.sentTo ?: emptyMap()) + (server to now))

    fun failed(e: QueueEntry?, error: String): QueueEntry =
        QueueEntry(NoteState.FAILED, error = error, sentTo = e?.sentTo ?: emptyMap())

    /** Legacy SENT entry → sent to [server] at its old time; null if [e] is not legacy. */
    fun migrate(e: QueueEntry, server: String, now: Long): QueueEntry? =
        if (e.state == NoteState.SENT && e.sentTo.isEmpty()) QueueEntry(NoteState.WAITING, sentTo = mapOf(server to (e.sentAtMs ?: now)))
        else null

    /** Cleanup may delete only a note the *current* companion confirmed [ageMs]+ ago. */
    fun deletable(e: QueueEntry?, server: String?, now: Long, ageMs: Long): Boolean {
        val at = server?.let { e?.sentTo?.get(it) } ?: return false
        return at < now - ageMs
    }
}

/**
 * Upload state per note, kept in `files/queue/<stem>.json` (internal storage,
 * main process only). The notes folder is the source of truth: a note with no
 * entry is WAITING. "Sent" means sent to the companion paired now ([server]).
 * Audio is never deleted here except by [deleteSentOlderThan].
 */
class Queue internal constructor(
    private val notesDir: File,
    private val dir: File,
    private val metaOf: (String) -> NoteMeta?,
    /** Fingerprint of the current pairing, null when unpaired. */
    private val server: () -> String?,
) {
    constructor(context: Context) : this(NoteStore(context), context)

    private constructor(store: NoteStore, context: Context) : this(
        store.dir, File(context.filesDir, "queue"), store::meta,
        { ServerPrefs(context).pairing?.fingerprint },
    )

    init {
        dir.mkdirs()
    }

    fun notes(): List<Note> {
        val current = currentServer()
        return (notesDir.listFiles { f -> f.name.endsWith(NoteStore.AUDIO_EXT) } ?: emptyArray())
            .sortedByDescending { it.name }
            .map { audio ->
                val stem = audio.name.removeSuffix(NoteStore.AUDIO_EXT)
                val v = QueueLogic.view(entry(stem), current)
                Note(
                    stem = stem,
                    audio = audio,
                    meta = metaOf(stem),
                    state = if (Progress.sending.value.containsKey(stem)) NoteState.SENDING else v.state,
                    error = v.error,
                    sentAtMs = v.sentAtMs,
                )
            }
    }

    fun unsent(): List<Note> = notes().filter { it.state != NoteState.SENT }.sortedBy { it.stem }

    /** Upload state of one note and its last error, without reading every sidecar. */
    fun status(stem: String): Pair<NoteState, String?> {
        if (Progress.sending.value.containsKey(stem)) return NoteState.SENDING to null
        val current = currentServer() // first: it may migrate this entry
        val v = QueueLogic.view(entry(stem), current)
        return v.state to v.error
    }

    /** [server] confirmed [stem]: the fingerprint of the pairing the upload used. */
    fun markSent(stem: String, server: String, now: Long = System.currentTimeMillis()) =
        write(stem, QueueLogic.sent(entry(stem), server, now))

    fun markFailed(stem: String, error: String) = write(stem, QueueLogic.failed(entry(stem), error))

    /**
     * The only deletion path: notes the *currently paired* companion confirmed,
     * more than [ageMs] ago. Unpaired → nothing; sent only elsewhere → kept.
     */
    fun deleteSentOlderThan(ageMs: Long, now: Long = System.currentTimeMillis()): Int {
        val current = currentServer() ?: return 0
        var n = 0
        val audios = notesDir.listFiles { f -> f.name.endsWith(NoteStore.AUDIO_EXT) } ?: emptyArray()
        for (audio in audios) {
            val stem = audio.name.removeSuffix(NoteStore.AUDIO_EXT)
            if (Progress.sending.value.containsKey(stem)) continue
            if (!QueueLogic.deletable(entry(stem), current, now, ageMs)) continue
            audio.delete()
            File(notesDir, "$stem.json").delete()
            File(dir, "$stem.json").delete()
            n++
        }
        return n
    }

    /**
     * Current fingerprint, after the one-time legacy migration: entries written
     * before per-server tracking were all sent to the companion paired at update
     * time, so they are stamped with it once (no needless re-uploads). Runs only
     * while paired; the marker makes later legacy-looking entries (an older build
     * installed over us) count as unsent rather than sent to whoever is paired.
     */
    private fun currentServer(): String? {
        val current = server() ?: return null
        val marker = File(dir, MIGRATED)
        if (!marker.exists()) {
            val now = System.currentTimeMillis()
            dir.listFiles { f -> f.name.endsWith(".json") }?.forEach { f ->
                val stem = f.name.removeSuffix(".json")
                entry(stem)?.let { QueueLogic.migrate(it, current, now) }?.let { write(stem, it) }
            }
            marker.writeText("")
        }
        return current
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
        val JSON = Json { ignoreUnknownKeys = true; encodeDefaults = true }
        const val MIGRATED = ".sent-per-server-v1"
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
