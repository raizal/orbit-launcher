package com.orbit.voice.upload

import com.orbit.voice.NoteMeta
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class QueueTest {
    @get:Rule val tmp = TemporaryFolder()

    private val a = Pairing("home-pc", 8765, "a".repeat(32)).fingerprint
    private val b = Pairing("test", 8799, "b".repeat(32)).fingerprint
    private var paired: String? = a

    private val notes by lazy { tmp.newFolder("notes") }
    private val qdir by lazy { File(tmp.root, "queue") }
    private val queue by lazy { Queue(notes, qdir, { stem -> meta(stem) }, { paired }) }

    private fun meta(stem: String) = NoteMeta(stem, "2026-11-01T10:00:00+07:00", 1000, "00", "H10", "1")

    private fun note(stem: String): String = stem.also {
        File(notes, "$it.aac").writeText("audio")
        File(notes, "$it.json").writeText("{}")
    }

    private fun state(stem: String) = queue.status(stem).first

    private val day = 24L * 60 * 60 * 1000
    private val week = 7 * day

    @Test fun fingerprintHidesTokenAndSeparatesServers() {
        val p = Pairing("home-pc", 8765, "secret-token-0123456789")
        assertEquals(16, p.fingerprint.length)
        assertFalse(p.fingerprint.contains("secret"))
        assertEquals(p.fingerprint, p.copy(port = 9000).fingerprint) // same companion, other port
        assertTrue(p.fingerprint != p.copy(token = "other-token-0123456789").fingerprint)
        assertTrue(p.fingerprint != p.copy(name = "other").fingerprint)
    }

    @Test fun sentIsPerPairing() {
        val n = note("2026-11-01_10-00-00+0700")
        assertEquals(NoteState.WAITING, state(n))
        queue.markSent(n, a)
        assertEquals(NoteState.SENT, state(n))

        paired = b // re-paired to a test companion
        assertEquals(NoteState.WAITING, state(n))
        assertEquals(listOf(n), queue.unsent().map { it.stem })

        paired = a // back home: still sent there, nothing to re-send
        assertEquals(NoteState.SENT, state(n))
        assertTrue(queue.unsent().isEmpty())

        paired = null // unpaired: nothing counts as sent
        assertEquals(NoteState.WAITING, state(n))
    }

    @Test fun sendingToAnotherServerKeepsTheFirst() {
        val n = note("2026-11-01_10-00-00+0700")
        queue.markSent(n, a, now = 1000)
        paired = b
        queue.markSent(n, b, now = 2000)
        assertEquals(NoteState.SENT, state(n))
        paired = a
        assertEquals(1000L, queue.notes().single().sentAtMs)
    }

    @Test fun failureKeepsEarlierSends() {
        val n = note("2026-11-01_10-00-00+0700")
        queue.markSent(n, a)
        paired = b
        queue.markFailed(n, "Connection lost")
        assertEquals(NoteState.FAILED to "Connection lost", queue.status(n))
        paired = a
        assertEquals(NoteState.SENT, state(n))
    }

    @Test fun cleanupDeletesOnlyNotesSentToCurrentPairingAWeekAgo() {
        val now = 100 * day
        val homeOld = note("2026-11-01_10-00-00+0700").also { queue.markSent(it, a, now = now - week - day) }
        val homeNew = note("2026-11-02_10-00-00+0700").also { queue.markSent(it, a, now = now - day) }
        val testOnly = note("2026-11-03_10-00-00+0700").also { queue.markSent(it, b, now = now - 30 * day) }
        val unsent = note("2026-11-04_10-00-00+0700")
        val failed = note("2026-11-05_10-00-00+0700").also { queue.markFailed(it, "x") }

        paired = a
        assertEquals(1, queue.deleteSentOlderThan(week, now))
        assertFalse(File(notes, "$homeOld.aac").exists())
        assertFalse(File(notes, "$homeOld.json").exists())
        for (s in listOf(homeNew, testOnly, unsent, failed)) assertTrue(s, File(notes, "$s.aac").exists())

        paired = null
        assertEquals(0, queue.deleteSentOlderThan(0, now + 365 * day))
        assertTrue(File(notes, "$homeNew.aac").exists())
    }

    @Test fun cleanupNeverDeletesNotesSentOnlyToAnotherServer() {
        val now = 100 * day
        val testOnly = note("2026-11-03_10-00-00+0700").also { queue.markSent(it, b, now = now - 30 * day) }
        paired = a
        assertEquals(0, queue.deleteSentOlderThan(week, now))
        assertTrue(File(notes, "$testOnly.aac").exists())
    }

    @Test fun legacyEntriesAreStampedWithCurrentPairingOnce() {
        qdir.mkdirs()
        val old = note("2026-10-01_10-00-00+0700")
        File(qdir, "$old.json").writeText("""{"state":"SENT","sentAtMs":12345}""")
        val oldFailed = note("2026-10-02_10-00-00+0700")
        File(qdir, "$oldFailed.json").writeText("""{"state":"FAILED","error":"Connection lost"}""")

        paired = a
        assertEquals(NoteState.SENT, state(old))
        assertEquals(12345L, queue.notes().first { it.stem == old }.sentAtMs)
        assertEquals(NoteState.FAILED, state(oldFailed))
        assertFalse(File(qdir, "$old.json").readText().contains("\"SENT\"")) // never written as legacy SENT

        paired = b
        assertEquals(NoteState.WAITING, state(old))

        // An older build writes a legacy entry later: it is not trusted as sent anywhere.
        val later = note("2026-11-10_10-00-00+0700")
        File(qdir, "$later.json").writeText("""{"state":"SENT","sentAtMs":99}""")
        paired = a
        assertEquals(NoteState.WAITING, state(later))
        queue.deleteSentOlderThan(0, Long.MAX_VALUE / 2) // may delete `old` (sent to a), never `later`
        assertTrue(File(notes, "$later.aac").exists())
    }

    @Test fun legacyMigrationWaitsForAPairing() {
        qdir.mkdirs()
        val old = note("2026-10-01_10-00-00+0700")
        File(qdir, "$old.json").writeText("""{"state":"SENT","sentAtMs":12345}""")
        paired = null
        assertEquals(NoteState.WAITING, state(old))
        assertEquals(0, queue.deleteSentOlderThan(0, Long.MAX_VALUE / 2))
        paired = a
        assertEquals(NoteState.SENT, state(old))
    }

    @Test fun viewOfMissingEntryIsWaiting() {
        val v = QueueLogic.view(null, a)
        assertEquals(NoteState.WAITING, v.state)
        assertNull(v.sentAtMs)
        assertFalse(QueueLogic.deletable(null, a, Long.MAX_VALUE, 0))
    }
}
