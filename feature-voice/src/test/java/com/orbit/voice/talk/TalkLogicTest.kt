package com.orbit.voice.talk

import com.orbit.voice.talk.TalkUi.Phase
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ReplyParserTest {
    private val id = "abc"

    @Test fun pendingStatuses() {
        for (s in listOf("queued", "transcribing", "thinking", "speaking")) {
            assertEquals(Reply.Pending(s), ReplyParser.parse(202, """{"status":"$s"}""", id))
        }
    }

    @Test fun pendingWithoutStatusIsQueued() =
        assertEquals(Reply.Pending("queued"), ReplyParser.parse(202, "{}", id))

    @Test fun done() {
        val body = """{"status":"done","text":" Sounds like a full day. ","audio":"/v1/notes/abc/reply.wav","transcript":"hi","extra":1}"""
        assertEquals(
            Reply.Done("Sounds like a full day.", "/v1/notes/abc/reply.wav", "hi"),
            ReplyParser.parse(200, body, id),
        )
    }

    @Test fun doneWithoutAudioFallsBackToSpecPath() =
        assertEquals("/v1/notes/abc/reply.wav", (ReplyParser.parse(200, """{"status":"done","text":"ok"}""", id) as Reply.Done).audioPath)

    @Test fun audioPathMustStayOnTheServer() {
        assertEquals("/v1/notes/abc/reply.wav", ReplyParser.safeAudioPath("http://evil/x.wav", id))
        assertEquals("/v1/notes/abc/reply.wav", ReplyParser.safeAudioPath("//evil/x.wav", id))
        assertEquals("/a.wav", ReplyParser.safeAudioPath("/a.wav", id))
    }

    @Test fun doneWithEmptyTextIsAnError() =
        assertEquals(Reply.Failed("Empty reply"), ReplyParser.parse(200, """{"status":"done","text":"  "}""", id))

    @Test fun error() {
        assertEquals(Reply.Failed("codex failed"), ReplyParser.parse(200, """{"status":"error","error":"codex failed"}""", id))
        assertEquals(Reply.Failed("Companion error"), ReplyParser.parse(200, """{"status":"error"}""", id))
    }

    @Test fun httpCodes() {
        assertEquals(Reply.NotFound, ReplyParser.parse(404, "", id))
        assertTrue(ReplyParser.parse(401, "", id) is Reply.Failed)
        assertEquals(Reply.Transient(500), ReplyParser.parse(500, "oops", id))
        assertEquals(Reply.Transient(200), ReplyParser.parse(200, "not json", id))
    }
}

class TalkMachineTest {
    private fun run(vararg events: TalkEvent, from: TalkUi = TalkUi.Idle()) =
        events.fold(from) { s, e -> TalkMachine.reduce(s, e) }

    @Test fun happyPath() {
        var s: TalkUi = TalkUi.Idle()
        s = TalkMachine.reduce(s, TalkEvent.RecStarted(10))
        assertEquals(TalkUi.Listening(10), s)
        s = TalkMachine.reduce(s, TalkEvent.Level(0.5f))
        assertEquals(TalkUi.Listening(10, 0.5f), s)
        s = TalkMachine.reduce(s, TalkEvent.Saved)
        assertEquals(TalkUi.Waiting(Phase.SENDING), s)
        s = TalkMachine.reduce(s, TalkEvent.Uploaded)
        assertEquals(TalkUi.Waiting(Phase.THINKING, "queued"), s)
        s = TalkMachine.reduce(s, TalkEvent.ReplyUpdate(Reply.Pending("thinking")))
        assertEquals(TalkUi.Waiting(Phase.THINKING, "thinking"), s)
        s = TalkMachine.reduce(s, TalkEvent.ReplyUpdate(Reply.Done("Nice.", "/a.wav", null)))
        assertEquals(TalkUi.Speaking("Nice."), s)
        s = TalkMachine.reduce(s, TalkEvent.PlaybackEnded)
        assertEquals(TalkUi.Done("Nice."), s)
        s = TalkMachine.reduce(s, TalkEvent.RecStarted(99)) // "Talk again"
        assertEquals(TalkUi.Listening(99), s)
    }

    @Test fun offlineWhileWaitingMeansLater() =
        assertEquals(TalkUi.Later("Not on home Wi-Fi"), run(TalkEvent.Offline("Not on home Wi-Fi"), from = TalkUi.Waiting(Phase.SENDING)))

    @Test fun timeoutDependsOnPhase() {
        assertEquals(TalkUi.Later("Companion not found"), run(TalkEvent.TimedOut, from = TalkUi.Waiting(Phase.SENDING)))
        assertEquals(TalkUi.Failed("No reply in time"), run(TalkEvent.TimedOut, from = TalkUi.Waiting(Phase.THINKING)))
    }

    @Test fun replyErrorsEndTheTurn() {
        val w = TalkUi.Waiting(Phase.THINKING, "queued")
        assertEquals(TalkUi.Failed("x"), run(TalkEvent.ReplyUpdate(Reply.Failed("x")), from = w))
        assertTrue(run(TalkEvent.ReplyUpdate(Reply.NotFound), from = w) is TalkUi.Failed)
        assertEquals(w, run(TalkEvent.ReplyUpdate(Reply.Transient(503)), from = w))
    }

    @Test fun recorderErrorReturnsToIdleWithMessage() =
        assertEquals(TalkUi.Idle("Mic unavailable"), run(TalkEvent.RecError("Mic unavailable"), from = TalkUi.Listening(1)))

    @Test fun staleEventsAreIgnored() {
        val speaking = TalkUi.Speaking("t")
        assertEquals(speaking, run(TalkEvent.ReplyUpdate(Reply.Pending("thinking")), TalkEvent.Offline("x"), TalkEvent.TimedOut, from = speaking))
        assertEquals(TalkUi.Idle(), run(TalkEvent.Saved, TalkEvent.Uploaded, TalkEvent.PlaybackEnded))
        // A recording tick never interrupts a turn in flight.
        val w = TalkUi.Waiting(Phase.THINKING, "thinking")
        assertEquals(w, run(TalkEvent.RecStarted(5), from = w))
    }

    @Test fun resetAlwaysGoesIdle() =
        assertEquals(TalkUi.Idle(), run(TalkEvent.Reset, from = TalkUi.Speaking("t")))

    @Test fun labelsAndScreen() {
        assertEquals("Sending…", TalkMachine.statusLabel(TalkUi.Waiting(Phase.SENDING)))
        assertEquals("Thinking…", TalkMachine.statusLabel(TalkUi.Waiting(Phase.THINKING, "thinking")))
        assertEquals("Odd…", TalkMachine.statusLabel(TalkUi.Waiting(Phase.THINKING, "odd")))
        assertTrue(TalkMachine.keepScreenOn(TalkUi.Waiting(Phase.SENDING)))
        assertFalse(TalkMachine.keepScreenOn(TalkUi.Listening(0)))
    }
}
