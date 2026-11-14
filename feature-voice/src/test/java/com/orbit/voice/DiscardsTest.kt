package com.orbit.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class DiscardsTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun file(dir: File, name: String, ageMs: Long = 60_000) = File(dir, name).apply {
        writeText("audio")
        setLastModified(System.currentTimeMillis() - ageMs)
    }

    @Test fun sweepDeletesOnlyCancelledRecordings() {
        val dir = tmp.root
        // Unrelated notes: sent/queued (finalized) and an un-cancelled crash leftover.
        val saved = file(dir, "2026-11-15_09-00-00+0700.aac")
        val sidecar = file(dir, "2026-11-15_09-00-00+0700.json")
        val orphan = file(dir, "2026-11-15_09-10-00+0700.aac.rec")
        // The cancelled one.
        val cancelled = file(dir, "2026-11-15_09-20-00+0700.aac.rec")
        Discards.mark(dir, "2026-11-15_09-20-00+0700.aac")
        Discards.marker(dir, "2026-11-15_09-20-00+0700.aac").setLastModified(System.currentTimeMillis() - 60_000)

        assertEquals(1, Discards.sweep(dir, minIdleMs = 0))

        assertFalse(cancelled.exists())
        assertFalse(Discards.marker(dir, "2026-11-15_09-20-00+0700.aac").exists())
        assertTrue(saved.exists() && sidecar.exists() && orphan.exists())
        assertFalse(Discards.isCancelled(orphan))
    }

    @Test fun recordingInProgressIsNotSweptByTheUi() {
        val dir = tmp.root
        val live = file(dir, "2026-11-15_10-00-00+0700.aac.rec", ageMs = 0)
        Discards.mark(dir, "2026-11-15_10-00-00+0700.aac")
        assertEquals(0, Discards.sweep(dir, minIdleMs = 10_000))
        assertTrue(live.exists())
        // ...but it stays marked, so it is never recovered as a note later.
        assertTrue(Discards.isCancelled(live))
    }

    @Test fun markerForASavedNoteNeverDeletesIt() {
        // Stop won the race before the cancel marker was written: the note stays.
        val dir = tmp.root
        val saved = file(dir, "2026-11-15_11-00-00+0700.aac")
        Discards.mark(dir, "2026-11-15_11-00-00+0700.aac")
        Discards.marker(dir, "2026-11-15_11-00-00+0700.aac").setLastModified(System.currentTimeMillis() - 60_000)
        Discards.sweep(dir, minIdleMs = 0)
        assertTrue(saved.exists())
        assertFalse(Discards.marker(dir, "2026-11-15_11-00-00+0700.aac").exists()) // stale marker cleaned
    }

    @Test fun discardIsIdempotent() {
        val dir = tmp.root
        val temp = file(dir, "2026-11-15_12-00-00+0700.aac.rec")
        Discards.mark(dir, "2026-11-15_12-00-00+0700.aac")
        assertTrue(Discards.discard(temp))
        assertTrue(Discards.discard(temp))
        assertFalse(temp.exists())
    }
}
