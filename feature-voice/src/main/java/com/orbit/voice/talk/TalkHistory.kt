package com.orbit.voice.talk

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.time.OffsetDateTime
import java.time.format.DateTimeFormatter

/** Sidecar `conversation` value on the first turn after New (docs/talk.md → Conversations & history). */
const val CONVERSATION_NEW = "new"

/** Turns kept in the local cache and asked from the companion. */
const val HISTORY_KEEP = 50

/** Local-only status: the companion has not reported this turn yet ("Saved — feedback when you're home"). */
const val STATUS_SAVED = "saved"

private val WORKING = setOf("queued", "transcribing", "thinking", "speaking")

/**
 * One turn of a conversation, as the watch shows and caches it
 * (`files/talk/<stem>.reply.json`). Field names follow `GET /v1/talk/history`;
 * [stem] is the note's file stem on the watch.
 */
@Serializable
data class TalkTurn(
    val id: String,
    val stem: String,
    val started_at: String,
    val transcript: String? = null,
    val status: String = STATUS_SAVED,
    val reply: String? = null,
    val error: String? = null,
    val audio: String? = null,
) {
    val hasReply: Boolean get() = status == "done" && !reply.isNullOrBlank()
    val working: Boolean get() = status in WORKING
}

/**
 * `files/talk/conversation.json`: the current conversation id and its ordered stems.
 * [fresh] = the user tapped New: until the companion reports one of our turns, the
 * conversation it calls "latest" is the old one and must not come back into view.
 */
@Serializable
data class ConversationFile(
    val conversation: String? = null,
    val stems: List<String> = emptyList(),
    val fresh: Boolean = false,
) {
    /** The next recorded turn carries `"conversation": "new"`. */
    val nextIsNew: Boolean get() = fresh && stems.isEmpty()
}

/** What `GET /v1/talk/history` said. */
data class History(val conversation: String?, val turns: List<TalkTurn>)

object HistoryParser {
    @Serializable
    private data class Body(val conversation: String? = null, val turns: List<Turn> = emptyList())

    @Serializable
    private data class Turn(
        val id: String? = null,
        val started_at: String? = null,
        val transcript: String? = null,
        val status: String? = null,
        val reply: String? = null,
        val error: String? = null,
        val audio: String? = null,
    )

    private val JSON = Json { ignoreUnknownKeys = true }

    /** Parses a 200 body; null if it is not a history document. Turns without id/time are skipped. */
    fun parse(body: String): History? {
        val b = runCatching { JSON.decodeFromString(Body.serializer(), body) }.getOrNull() ?: return null
        val turns = b.turns.mapNotNull { t ->
            val id = t.id?.takeIf { it.isNotBlank() } ?: return@mapNotNull null
            val started = t.started_at ?: return@mapNotNull null
            TalkTurn(
                id = id,
                stem = TalkHistory.stemFor(started) ?: id,
                started_at = started,
                transcript = t.transcript?.takeIf { it.isNotBlank() },
                status = t.status ?: "queued",
                reply = t.reply?.takeIf { it.isNotBlank() },
                error = t.error,
                audio = t.audio?.let { ReplyParser.safeAudioPath(it, id) },
            )
        }
        return History(b.conversation, turns)
    }
}

object TalkHistory {
    /** The conversation in view: [file] as persisted, [turns] oldest → newest (stems = file.stems). */
    data class State(val file: ConversationFile = ConversationFile(), val turns: List<TalkTurn> = emptyList())

    /** New: clear the view; the next turn starts a new conversation. */
    fun startNew(): State = State(ConversationFile(fresh = true), emptyList())

    /** A turn just recorded on the watch (not on the companion yet). */
    fun add(s: State, turn: TalkTurn): State {
        if (s.turns.any { it.id == turn.id }) return s
        return withTurns(s.file, sorted(s.turns + turn))
    }

    /** Applies [f] to the turn with note id [id], if it is in view. */
    fun update(s: State, id: String, f: (TalkTurn) -> TalkTurn): State {
        if (s.turns.none { it.id == id }) return s
        return s.copy(turns = s.turns.map { if (it.id == id) f(it) else it })
    }

    /**
     * Merges the companion's history into the local view (merge by note id; the
     * server wins on transcript/reply/status). Turns the companion has not seen yet
     * stay; a different conversation on the server replaces the view, except right
     * after New (see [ConversationFile.fresh]).
     */
    fun merge(local: State, server: History): State {
        val conv = server.conversation ?: return local
        val serverIds = server.turns.mapTo(HashSet()) { it.id }
        val overlap = local.turns.any { it.id in serverIds }
        if (local.file.fresh && !overlap) return local
        val same = overlap || local.file.conversation == conv
        val localById = local.turns.associateBy { it.id }
        val fromServer = server.turns.map { s -> localById[s.id]?.let { s.copy(stem = it.stem) } ?: s }
        val extra = local.turns.filter { it.id !in serverIds && (same || it.status == STATUS_SAVED) }
        return withTurns(ConversationFile(conversation = conv), sorted(fromServer + extra))
    }

    private fun withTurns(file: ConversationFile, turns: List<TalkTurn>): State {
        val kept = turns.takeLast(HISTORY_KEEP)
        return State(file.copy(stems = kept.map { it.stem }), kept)
    }

    private fun sorted(turns: List<TalkTurn>) = turns.sortedBy { epochOf(it.started_at) ?: Long.MAX_VALUE }

    /** Cached stems to delete: everything but the newest [keep] turns and the ones in view. */
    fun stemsToDelete(cached: Collection<String>, inView: Collection<String>, keep: Int = HISTORY_KEEP): Set<String> {
        val newest = cached.distinct().sortedByDescending { stemEpoch(it) ?: Long.MIN_VALUE }.take(keep)
        return cached.toSet() - newest.toSet() - inView.toSet()
    }

    /** `2026-11-14T21:22:42+07:00` → `2026-11-14_21-22-42+0700` (NoteStore's stem for the same instant). */
    fun stemFor(startedAt: String): String? = runCatching {
        OffsetDateTime.parse(startedAt).format(STEM)
    }.getOrNull()

    fun epochOf(iso: String): Long? = runCatching { OffsetDateTime.parse(iso).toInstant().toEpochMilli() }.getOrNull()

    private fun stemEpoch(stem: String): Long? =
        runCatching { OffsetDateTime.parse(stem, STEM).toInstant().toEpochMilli() }.getOrNull()

    /** "21:22" from `started_at` (the time as recorded, in its own offset). */
    fun clock(startedAt: String): String =
        runCatching { OffsetDateTime.parse(startedAt).format(CLOCK) }.getOrDefault("")

    private val STEM = DateTimeFormatter.ofPattern("yyyy-MM-dd_HH-mm-ssZ")
    private val CLOCK = DateTimeFormatter.ofPattern("HH:mm")
}

/**
 * The watch's Talk cache in internal storage (`files/talk/`): `<stem>.reply.json`,
 * `<stem>.reply.wav`, `conversation.json`. Only ever deletes its own cache files,
 * never notes. Blocking; call off the main thread.
 */
class TalkCache(val dir: File) {

    fun audioFile(stem: String) = File(dir, "$stem$WAV")

    fun load(): TalkHistory.State {
        val file = readConversation()
        val turns = file.stems.mapNotNull(::readTurn)
        return TalkHistory.State(file.copy(stems = turns.map { it.stem }), turns)
    }

    fun readConversation(): ConversationFile =
        runCatching { JSON.decodeFromString(ConversationFile.serializer(), File(dir, CONVERSATION).readText()) }
            .getOrNull() ?: ConversationFile()

    fun writeConversation(c: ConversationFile) = write(CONVERSATION, JSON.encodeToString(ConversationFile.serializer(), c))

    fun readTurn(stem: String): TalkTurn? =
        runCatching { JSON.decodeFromString(TalkTurn.serializer(), File(dir, "$stem$JSON_EXT").readText()) }.getOrNull()

    fun writeTurn(t: TalkTurn) = write("${t.stem}$JSON_EXT", JSON.encodeToString(TalkTurn.serializer(), t))

    /** Stems that have anything cached. */
    fun cachedStems(): Set<String> =
        dir.listFiles()?.mapNotNullTo(HashSet()) { f ->
            when {
                f.name.endsWith(JSON_EXT) -> f.name.removeSuffix(JSON_EXT)
                f.name.endsWith(WAV) -> f.name.removeSuffix(WAV)
                else -> null
            }
        } ?: emptySet()

    /** Keeps the newest [HISTORY_KEEP] turns plus [inView]; deletes older cached replies. */
    fun prune(inView: Collection<String>): Int {
        val gone = TalkHistory.stemsToDelete(cachedStems(), inView)
        for (stem in gone) {
            File(dir, "$stem$JSON_EXT").delete()
            audioFile(stem).delete()
            File(dir, "$stem$WAV.part").delete()
        }
        return gone.size
    }

    private fun write(name: String, text: String) {
        dir.mkdirs()
        val tmp = File(dir, "$name.tmp")
        tmp.writeText(text)
        if (!tmp.renameTo(File(dir, name))) tmp.delete()
    }

    companion object {
        const val CONVERSATION = "conversation.json"
        const val JSON_EXT = ".reply.json"
        const val WAV = ".reply.wav"
        private val JSON = Json { ignoreUnknownKeys = true }
    }
}
