package com.orbit.voice.talk

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/** Sidecar `mode` of a Talk turn (docs/talk.md). */
const val MODE_TALK = "talk"

/** Poll `GET /v1/notes/{id}/reply` this often, and give up after [REPLY_GIVE_UP_MS] (docs/talk.md). */
const val REPLY_POLL_MS = 1_500L
const val REPLY_GIVE_UP_MS = 3 * 60 * 1000L

/** What `GET /v1/notes/{id}/reply` said. */
sealed interface Reply {
    /** 202: still working; [status] is queued | transcribing | thinking | speaking. */
    data class Pending(val status: String) : Reply
    /** 200 done. [audioPath] is a server path like `/v1/notes/{id}/reply.wav`. */
    data class Done(val text: String, val audioPath: String, val transcript: String?) : Reply
    /** 200 error (the note itself is safe on the server), or a permanent HTTP error. */
    data class Failed(val error: String) : Reply
    /** 404: unknown id, or not a talk note. */
    data object NotFound : Reply
    /** 5xx / unexpected code: try again on the next poll. */
    data class Transient(val code: Int) : Reply
}

object ReplyParser {
    @Serializable
    private data class Body(
        val status: String? = null,
        val text: String? = null,
        val audio: String? = null,
        val transcript: String? = null,
        val error: String? = null,
    )

    private val JSON = Json { ignoreUnknownKeys = true }

    /** Maps an HTTP response of the reply endpoint for note [id]. Never throws. */
    fun parse(code: Int, body: String, id: String): Reply {
        when (code) {
            404 -> return Reply.NotFound
            401, 403 -> return Reply.Failed("Not authorized — pair again")
            200, 202 -> Unit
            else -> return Reply.Transient(code)
        }
        val b = runCatching { JSON.decodeFromString(Body.serializer(), body) }.getOrNull()
            ?: return Reply.Transient(code)
        return when {
            code == 202 -> Reply.Pending(b.status ?: "queued")
            b.status == "done" -> {
                val text = b.text?.trim().orEmpty()
                if (text.isEmpty()) Reply.Failed("Empty reply")
                else Reply.Done(text, safeAudioPath(b.audio, id), b.transcript)
            }
            b.status == "error" -> Reply.Failed(b.error?.takeIf { it.isNotBlank() } ?: "Companion error")
            // 200 with a working status: treat like 202.
            b.status != null -> Reply.Pending(b.status)
            else -> Reply.Transient(code)
        }
    }

    /** Only a path on the same server; anything else falls back to the spec's path. */
    fun safeAudioPath(audio: String?, id: String): String =
        audio?.takeIf { it.startsWith("/") && !it.startsWith("//") } ?: "/v1/notes/$id/reply.wav"
}

/** What the Talk screen shows. */
sealed interface TalkUi {
    /** "Tap to talk". [note] = a line under it (an error, or why the last turn ended). */
    data class Idle(val note: String? = null) : TalkUi
    data class Listening(val startElapsed: Long, val level: Float = 0f) : TalkUi
    /** Uploading the note ([Phase.SENDING]) or waiting for the companion ([Phase.THINKING]). */
    data class Waiting(val phase: Phase, val status: String? = null) : TalkUi
    data class Speaking(val text: String) : TalkUi
    data class Done(val text: String) : TalkUi
    /** Saved, queued as usual — "Saved — feedback when you're home". */
    data class Later(val reason: String) : TalkUi
    /** No feedback this time (companion error, timeout, not a talk note). The note is safe. */
    data class Failed(val message: String) : TalkUi

    enum class Phase { SENDING, THINKING }
}

sealed interface TalkEvent {
    data class RecStarted(val startElapsed: Long) : TalkEvent
    data class Level(val level: Float) : TalkEvent
    data object Saved : TalkEvent
    data class RecError(val message: String) : TalkEvent
    /** Can't reach the companion now (not paired / not home / not found): stays queued. */
    data class Offline(val reason: String) : TalkEvent
    data object Uploaded : TalkEvent
    data class ReplyUpdate(val reply: Reply) : TalkEvent
    data object TimedOut : TalkEvent
    data object PlaybackEnded : TalkEvent
    data object Reset : TalkEvent
}

/** Pure state transitions; events that don't fit the current state are ignored (stale). */
object TalkMachine {
    fun reduce(s: TalkUi, e: TalkEvent): TalkUi = when (e) {
        is TalkEvent.Reset -> TalkUi.Idle()
        is TalkEvent.RecStarted -> when (s) {
            is TalkUi.Listening -> s
            // Only from a resting state; never interrupt a turn in flight.
            is TalkUi.Idle, is TalkUi.Done, is TalkUi.Later, is TalkUi.Failed -> TalkUi.Listening(e.startElapsed)
            else -> s
        }
        is TalkEvent.Level -> if (s is TalkUi.Listening) s.copy(level = e.level) else s
        is TalkEvent.Saved -> if (s is TalkUi.Listening) TalkUi.Waiting(TalkUi.Phase.SENDING) else s
        is TalkEvent.RecError -> if (s is TalkUi.Listening) TalkUi.Idle(e.message) else s
        is TalkEvent.Offline -> if (s is TalkUi.Waiting) TalkUi.Later(e.reason) else s
        is TalkEvent.Uploaded ->
            if (s is TalkUi.Waiting && s.phase == TalkUi.Phase.SENDING) TalkUi.Waiting(TalkUi.Phase.THINKING, "queued") else s
        is TalkEvent.ReplyUpdate -> if (s !is TalkUi.Waiting) s else when (val r = e.reply) {
            is Reply.Pending -> TalkUi.Waiting(TalkUi.Phase.THINKING, r.status)
            is Reply.Done -> TalkUi.Speaking(r.text)
            is Reply.Failed -> TalkUi.Failed(r.error)
            is Reply.NotFound -> TalkUi.Failed("No feedback for this note")
            is Reply.Transient -> s
        }
        is TalkEvent.TimedOut -> when {
            s is TalkUi.Waiting && s.phase == TalkUi.Phase.SENDING -> TalkUi.Later("Companion not found")
            s is TalkUi.Waiting -> TalkUi.Failed("No reply in time")
            else -> s
        }
        is TalkEvent.PlaybackEnded -> if (s is TalkUi.Speaking) TalkUi.Done(s.text) else s
    }

    /** The line shown while waiting (cyan). */
    fun statusLabel(s: TalkUi.Waiting): String = when (s.phase) {
        TalkUi.Phase.SENDING -> "Sending…"
        TalkUi.Phase.THINKING -> when (s.status) {
            "queued", null -> "Queued…"
            "transcribing" -> "Listening back…"
            "thinking" -> "Thinking…"
            "speaking" -> "Finding words…"
            else -> s.status.replaceFirstChar { it.uppercase() } + "…"
        }
    }

    /** Screen stays on while something is about to be shown or heard. */
    fun keepScreenOn(s: TalkUi): Boolean = s is TalkUi.Waiting || s is TalkUi.Speaking || s is TalkUi.Done
}
