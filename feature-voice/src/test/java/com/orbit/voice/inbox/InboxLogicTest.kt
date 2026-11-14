package com.orbit.voice.inbox

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class InboxLogicTest {
    private fun item(id: String, rev: Long, minute: Int, read: String? = null, title: String = "Item $id") =
        InboxItem(id = id, rev = rev, created_at = "2026-11-15T10:%02d:00+00:00".format(minute), title = title, read_at = read)

    @Test fun parsesSpecExampleAndIgnoresUnknownFields() {
        val r = InboxLogic.parse(
            """{"cursor":7,"unread":1,"items":[
              {"id":"in-a","rev":7,"created_at":"2026-11-15T10:00:00+00:00","kind":"thread_done",
               "title":"Cancel feature is done","body":"Works.","ref":"thr_x","read_at":null,"extra":1},
              {"id":"in-b","rev":6,"created_at":"2026-11-15T09:00:00+00:00","kind":"note","title":"Old",
               "body":"","ref":null,"read_at":"2026-11-15T09:30:00+00:00"}
            ]}"""
        )!!
        assertEquals(7L, r.cursor)
        assertEquals(listOf("in-a", "in-b"), r.items.map { it.id })
        assertEquals("thr_x", r.items[0].ref)
        assertTrue(r.items[0].unread)
        assertEquals(false, r.items[1].unread)
    }

    @Test fun garbageAndIncompleteItems() {
        assertNull(InboxLogic.parse("not json"))
        assertNull(InboxLogic.parse("""{"items":[]}""")) // no cursor
        val r = InboxLogic.parse("""{"cursor":1,"items":[{"id":"","created_at":"x","title":"t"},{"id":"a","created_at":"x","title":" "}]}""")!!
        assertTrue(r.items.isEmpty())
    }

    @Test fun firstSyncFillsTheListWithoutAnnouncing() {
        val m = InboxLogic.merge(InboxState(), InboxResponse(2, 2, listOf(item("a", 1, 1), item("b", 2, 2))), nowMs = 5)
        assertTrue(m.announce.isEmpty())
        assertEquals(2L, m.state.cursor)
        assertEquals(listOf("b", "a"), m.state.items.map { it.id }) // newest first
        assertEquals(5L, m.state.lastCheckMs)
        assertEquals(2, m.state.unreadCount)
    }

    @Test fun laterSyncsAnnounceOnlyNewUnreadItems() {
        val s = InboxLogic.merge(InboxState(), InboxResponse(1, 1, listOf(item("a", 1, 1))), 0).state
        val m = InboxLogic.merge(
            s,
            InboxResponse(4, 2, listOf(item("c", 4, 4), item("b", 3, 3, read = "2026-11-15T10:05:00+00:00"), item("a", 2, 1))),
            0,
        )
        assertEquals(listOf("c"), m.announce.map { it.id }) // b arrived already read; a is not new
        assertEquals(listOf("c", "b", "a"), m.state.items.map { it.id })
        assertEquals(4L, m.state.cursor)
        // Nothing changed → nothing announced, cursor kept.
        val again = InboxLogic.merge(m.state, InboxResponse(4, 2, emptyList()), 0)
        assertTrue(again.announce.isEmpty())
        assertEquals(4L, again.state.cursor)
    }

    @Test fun serverUpdatesReplaceLocalCopies() {
        val s = InboxLogic.merge(InboxState(), InboxResponse(1, 1, listOf(item("a", 1, 1))), 0).state
        val readElsewhere = item("a", 2, 1, read = "2026-11-15T11:00:00+00:00")
        val m = InboxLogic.merge(s, InboxResponse(2, 0, listOf(readElsewhere)), 0)
        assertEquals("2026-11-15T11:00:00+00:00", m.state.item("a")!!.read_at)
        // …and marked unread again from the web UI.
        val m2 = InboxLogic.merge(m.state, InboxResponse(3, 1, listOf(item("a", 3, 1))), 0)
        assertTrue(m2.state.item("a")!!.unread)
        assertTrue(m2.announce.isEmpty()) // not new: no second buzz
    }

    @Test fun localReadSurvivesUntilAcknowledged() {
        val s = InboxLogic.merge(InboxState(), InboxResponse(1, 1, listOf(item("a", 1, 1))), 0).state
        val read = InboxLogic.markRead(s, "a", "2026-11-15T12:00:00Z")
        assertEquals(setOf("a"), read.pendingReads)
        assertEquals(0, read.unreadCount)
        assertEquals(read, InboxLogic.markRead(read, "a", "later")) // already read: no change
        assertEquals(read, InboxLogic.markRead(read, "nope", "x"))
        // A poll before the read reached the companion still says unread: the watch keeps it read.
        val m = InboxLogic.merge(read, InboxResponse(2, 1, listOf(item("a", 2, 1, title = "Renamed"))), 0)
        assertEquals("2026-11-15T12:00:00Z", m.state.item("a")!!.read_at)
        assertEquals("Renamed", m.state.item("a")!!.title)
        assertEquals(setOf("a"), m.state.pendingReads)
        // The companion reports it read → no longer pending.
        val m2 = InboxLogic.merge(m.state, InboxResponse(3, 0, listOf(item("a", 3, 1, read = "2026-11-15T12:00:01+00:00"))), 0)
        assertTrue(m2.state.pendingReads.isEmpty())
        assertTrue(InboxLogic.readAcked(read, "a").pendingReads.isEmpty())
    }

    @Test fun keepsAtMostKeepItems() {
        val s = InboxLogic.merge(InboxState(), InboxResponse(1, 1, listOf(item("x", 1, 0))), 0).state
        val many = (1..InboxLogic.KEEP + 10).map { InboxItem("n$it", it.toLong() + 1, "2026-11-15T10:00:%02d+00:00".format(it % 60).replace("10:00", "1%d:00".format(it / 60)), title = "t") }
        val m = InboxLogic.merge(s, InboxResponse(500, 0, many), 0)
        assertEquals(InboxLogic.KEEP, m.state.items.size)
        assertNull(m.state.item("x")) // the oldest went
        assertTrue(m.announce.all { m.state.item(it.id) != null })
    }

    @Test fun anotherCompanionStartsAfresh() {
        val a = InboxLogic.serverKey("home-pc", 8765, "token-aaaaaaaaaaaaaaaa")
        val b = InboxLogic.serverKey("home-pc", 8811, "token-aaaaaaaaaaaaaaaa")
        assertTrue(a != b && !a.contains("token"))
        val s = InboxLogic.merge(InboxLogic.forServer(InboxState(), a), InboxResponse(6, 1, listOf(item("a", 6, 1))), 9).state
        assertEquals(a, s.server)
        assertEquals(s, InboxLogic.forServer(s, a))
        val other = InboxLogic.forServer(s, b)
        assertEquals(0L, other.cursor)
        assertTrue(other.items.isEmpty() && other.pendingReads.isEmpty())
        assertEquals(9L, other.lastCheckMs)
        // Same companion, data reset: its cursor is now below ours.
        assertTrue(InboxLogic.cursorWentBack(s, InboxResponse(2)))
        assertFalse(InboxLogic.cursorWentBack(s, InboxResponse(6)))
        // A state saved before `server` existed belongs to nobody: it starts afresh too.
        assertEquals(0L, InboxLogic.forServer(s.copy(server = null), a).cursor)
    }

    @Test fun ages() {
        val t = "2026-11-15T10:00:00+00:00"
        val base = java.time.OffsetDateTime.parse(t).toInstant().toEpochMilli()
        assertEquals("just now", InboxLogic.age(t, base + 20_000))
        assertEquals("5 min", InboxLogic.age(t, base + 5 * 60_000))
        assertEquals("3 h", InboxLogic.age(t, base + 3 * 3_600_000))
        assertEquals("2 d", InboxLogic.age(t, base + 48 * 3_600_000))
        assertEquals("", InboxLogic.age("garbage", base))
        assertNotNull(InboxLogic.age(t, base - 1000)) // clock skew: never negative
    }
}
