package com.orbit.voice.talk

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class HistoryParserTest {
    @Test fun parsesSpecExample() {
        val h = HistoryParser.parse(
            """{"conversation":"c-1","turns":[
              {"id":"a","started_at":"2026-11-14T21:22:42+07:00","transcript":"hello","status":"done",
               "reply":"Hi there.","error":null,"audio":"/v1/notes/a/reply.wav"},
              {"id":"b","started_at":"2026-11-14T21:30:00+07:00","transcript":null,"status":"queued",
               "reply":null,"error":null,"audio":null,"extra":1}
            ]}"""
        )!!
        assertEquals("c-1", h.conversation)
        assertEquals(2, h.turns.size)
        val a = h.turns[0]
        assertEquals("2026-11-14_21-22-42+0700", a.stem)
        assertTrue(a.hasReply)
        assertEquals("/v1/notes/a/reply.wav", a.audio)
        val b = h.turns[1]
        assertNull(b.transcript)
        assertTrue(b.working)
        assertNull(b.audio)
    }

    @Test fun noConversationYet() {
        val h = HistoryParser.parse("""{"conversation":null,"turns":[]}""")!!
        assertNull(h.conversation)
        assertTrue(h.turns.isEmpty())
    }

    @Test fun garbageIsNull() {
        assertNull(HistoryParser.parse("not json"))
        assertNull(HistoryParser.parse("[]"))
    }

    @Test fun skipsTurnsWithoutIdAndSanitizesAudio() {
        val h = HistoryParser.parse(
            """{"conversation":"c","turns":[
              {"started_at":"2026-11-14T21:22:42+07:00","status":"done"},
              {"id":"x","started_at":"2026-11-14T21:22:42+07:00","status":"done","reply":"ok","audio":"http://evil/x.wav"}
            ]}"""
        )!!
        assertEquals(listOf("x"), h.turns.map { it.id })
        assertEquals("/v1/notes/x/reply.wav", h.turns[0].audio)
    }

    @Test fun stemMatchesNoteStore() {
        assertEquals("2026-11-14_21-22-42+0700", TalkHistory.stemFor("2026-11-14T21:22:42+07:00"))
        assertEquals("2026-11-14_14-22-42+0000", TalkHistory.stemFor("2026-11-14T14:22:42Z"))
        assertNull(TalkHistory.stemFor("yesterday"))
    }
}

class TalkHistoryMergeTest {
    private fun turn(id: String, minute: Int, status: String = STATUS_SAVED, reply: String? = null) = TalkTurn(
        id = id,
        stem = "2026-11-14_21-%02d-00+0700".format(minute),
        started_at = "2026-11-14T21:%02d:00+07:00".format(minute),
        status = status,
        reply = reply,
    )

    private fun state(conv: String?, vararg t: TalkTurn, fresh: Boolean = false) =
        TalkHistory.State(ConversationFile(conv, t.map { it.stem }, fresh), t.toList())

    @Test fun serverWinsAndFillsInPendingTurn() {
        val local = state("c1", turn("a", 1, "done", "r1"), turn("b", 2))
        val server = History("c1", listOf(turn("a", 1, "done", "r1"), turn("b", 2, "done", "r2").copy(transcript = "hi")))
        val m = TalkHistory.merge(local, server)
        assertEquals(listOf("a", "b"), m.turns.map { it.id })
        assertEquals("r2", m.turns[1].reply)
        assertEquals("hi", m.turns[1].transcript)
        assertEquals("c1", m.file.conversation)
        assertEquals(m.turns.map { it.stem }, m.file.stems)
    }

    @Test fun keepsLocalStemForKnownId() {
        val local = state("c1", turn("a", 1).copy(stem = "local-stem"))
        val m = TalkHistory.merge(local, History("c1", listOf(turn("a", 1, "thinking"))))
        assertEquals("local-stem", m.turns.single().stem)
        assertEquals("thinking", m.turns.single().status)
    }

    @Test fun unsentLocalTurnStaysAndSortsByTime() {
        val local = state("c1", turn("a", 1, "done", "r"), turn("c", 9))
        val server = History("c1", listOf(turn("a", 1, "done", "r"), turn("b", 5, "done", "r")))
        assertEquals(listOf("a", "b", "c"), TalkHistory.merge(local, server).turns.map { it.id })
    }

    @Test fun otherConversationReplacesViewButKeepsUnsent() {
        val local = state("c1", turn("a", 1, "done", "r"), turn("z", 9))
        val server = History("c2", listOf(turn("b", 5, "done", "r")))
        val m = TalkHistory.merge(local, server)
        assertEquals(listOf("b", "z"), m.turns.map { it.id })
        assertEquals("c2", m.file.conversation)
    }

    @Test fun afterNewTheOldConversationDoesNotComeBack() {
        val fresh = TalkHistory.startNew()
        assertTrue(fresh.file.nextIsNew)
        val old = History("c1", listOf(turn("a", 1, "done", "r")))
        assertEquals(fresh, TalkHistory.merge(fresh, old))
        // First new turn recorded but not on the server yet.
        val withTurn = TalkHistory.add(fresh, turn("n", 10))
        assertFalse(withTurn.file.nextIsNew)
        assertEquals(withTurn, TalkHistory.merge(withTurn, old))
        // The server has it: adopt the new conversation.
        val m = TalkHistory.merge(withTurn, History("c2", listOf(turn("n", 10, "thinking"))))
        assertEquals("c2", m.file.conversation)
        assertFalse(m.file.fresh)
        assertEquals("thinking", m.turns.single().status)
    }

    @Test fun nullConversationKeepsLocal() {
        val local = state("c1", turn("a", 1))
        assertEquals(local, TalkHistory.merge(local, History(null, emptyList())))
    }

    @Test fun addIgnoresDuplicatesAndCapsAt50() {
        var s = TalkHistory.State()
        for (i in 0 until 55) s = TalkHistory.add(s, turn("t$i", i))
        s = TalkHistory.add(s, turn("t54", 54))
        assertEquals(HISTORY_KEEP, s.turns.size)
        assertEquals("t5", s.turns.first().id)
        assertEquals(s.turns.map { it.stem }, s.file.stems)
    }

    @Test fun updateOnlyTouchesThatTurn() {
        val s = state("c", turn("a", 1), turn("b", 2))
        val u = TalkHistory.update(s, "b") { it.copy(status = "queued") }
        assertEquals(listOf(STATUS_SAVED, "queued"), u.turns.map { it.status })
        assertEquals(s, TalkHistory.update(s, "nope") { it.copy(status = "x") })
    }
}

class TalkCacheTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun stem(i: Int) = "2026-11-%02d_10-00-00+0700".format(1 + i / 24).replace("10-00", "%02d-00".format(i % 24))

    @Test fun stemsToDeleteKeepsNewest50AndInView() {
        val all = (0 until 60).map(::stem)
        val gone = TalkHistory.stemsToDelete(all, inView = listOf(all[0]))
        assertEquals((1 until 10).map(::stem).toSet(), gone)
    }

    @Test fun pruneDeletesOnlyOldCacheFiles() {
        val dir = tmp.newFolder("talk")
        val cache = TalkCache(dir)
        val stems = (0 until 52).map(::stem)
        for (s in stems) {
            cache.writeTurn(TalkTurn(id = s, stem = s, started_at = "x"))
            File(dir, "$s.reply.wav").writeText("wav")
        }
        File(dir, "unrelated.aac").writeText("keep me")
        cache.writeConversation(ConversationFile("c", stems.takeLast(3)))
        assertEquals(2, cache.prune(stems.takeLast(3)))
        assertFalse(File(dir, "${stems[0]}.reply.json").exists())
        assertFalse(File(dir, "${stems[1]}.reply.wav").exists())
        assertTrue(File(dir, "${stems[2]}.reply.wav").exists())
        assertTrue(File(dir, "unrelated.aac").exists())
        assertTrue(File(dir, TalkCache.CONVERSATION).exists())
    }

    @Test fun roundTripsConversation() {
        val cache = TalkCache(tmp.newFolder("talk"))
        assertEquals(TalkHistory.State(), cache.load())
        val a = TalkTurn("a", stem(1), "2026-11-01T01:00:00+07:00", "hi", "done", "r", null, "/v1/notes/a/reply.wav")
        val b = TalkTurn("b", stem(2), "2026-11-01T02:00:00+07:00")
        cache.writeTurn(a)
        cache.writeTurn(b)
        // A stem whose json is missing is dropped from view.
        cache.writeConversation(ConversationFile("c", listOf(a.stem, "missing", b.stem), fresh = true))
        val s = cache.load()
        assertEquals(listOf(a, b), s.turns)
        assertEquals(ConversationFile("c", listOf(a.stem, b.stem), fresh = true), s.file)
    }

    @Test fun corruptFilesReadAsEmpty() {
        val dir = tmp.newFolder("talk")
        File(dir, TalkCache.CONVERSATION).writeText("{oops")
        assertEquals(TalkHistory.State(), TalkCache(dir).load())
    }
}
