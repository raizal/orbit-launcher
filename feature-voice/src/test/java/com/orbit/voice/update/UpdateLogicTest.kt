package com.orbit.voice.update

import com.orbit.voice.talk.TalkUi
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.security.MessageDigest

class UpdateLogicTest {
    private val sha = "a".repeat(64)

    private fun body(code: Long = 45, sha: String = this.sha, size: Long = 2_000_000, apk: String = "/v1/app/apk", name: String = "0.1.0-abc1234") =
        """{"version_code":$code,"version_name":"$name","sha256":"$sha","size":$size,"published_at":"2026-11-15T10:00:00+07:00","apk":"$apk","extra":1}"""

    @Test fun parsesCompanionResponse() {
        val info = UpdateLogic.parse(body())!!
        assertEquals(45L, info.version_code)
        assertEquals("0.1.0-abc1234", info.version_name)
        assertEquals("/v1/app/apk", info.apk)
    }

    @Test fun rejectsBadDescriptions() {
        assertNull(UpdateLogic.parse("not json"))
        assertNull(UpdateLogic.parse(body(code = 0)))
        assertNull(UpdateLogic.parse(body(sha = "abc")))
        assertNull(UpdateLogic.parse(body(size = 0)))
        assertNull(UpdateLogic.parse(body(name = " ")))
    }

    @Test fun apkPathStaysOnTheCompanion() {
        assertEquals("/v1/app/apk", UpdateLogic.parse(body(apk = "http://evil/x.apk"))!!.apk)
        assertEquals("/v1/app/apk", UpdateLogic.parse(body(apk = "//evil/x.apk"))!!.apk)
        assertEquals("/v1/app/apk2", UpdateLogic.parse(body(apk = "/v1/app/apk2"))!!.apk)
    }

    @Test fun shaIsNormalizedAndRoundTrips() {
        val info = UpdateLogic.parse(body(sha = "A".repeat(64)))!!
        assertEquals("a".repeat(64), info.sha256)
        assertEquals(info, UpdateLogic.parse(UpdateLogic.encode(info)))
    }

    @Test fun onlyStrictlyNewerIsOffered() {
        val info = UpdateLogic.parse(body(code = 45))!!
        assertTrue(UpdateLogic.isNewer(info, 44))
        assertFalse(UpdateLogic.isNewer(info, 45))
        assertFalse(UpdateLogic.isNewer(info, 46))
        assertFalse(UpdateLogic.isNewer(null, 1))
    }

    @Test fun opportunisticChecksAreRateLimited() {
        val now = 10_000_000_000L
        assertTrue(UpdateLogic.dueForCheck(0, now))
        assertFalse(UpdateLogic.dueForCheck(now - 60_000, now))
        assertTrue(UpdateLogic.dueForCheck(now - UpdateLogic.MIN_CHECK_INTERVAL_MS, now))
        assertTrue(UpdateLogic.dueForCheck(now + 60_000, now)) // clock went back
    }

    @Test fun freshRecFileMeansRecording() {
        val now = 1_000_000L
        assertTrue(UpdateLogic.recordingActive(listOf("2026-11-15_10-00-00+0700.aac.rec" to now - 500), now))
        assertFalse(UpdateLogic.recordingActive(listOf("2026-11-15_10-00-00+0700.aac.rec" to now - 120_000), now))
        assertFalse(UpdateLogic.recordingActive(listOf("2026-11-15_10-00-00+0700.aac" to now, "x.json" to now), now))
        assertFalse(UpdateLogic.recordingActive(emptyList(), now))
    }

    @Test fun talkBlocksOnlyWhileATurnIsInFlight() {
        assertTrue(UpdateLogic.talkBlocks(TalkUi.Listening(0)))
        assertTrue(UpdateLogic.talkBlocks(TalkUi.Waiting(TalkUi.Phase.SENDING)))
        assertTrue(UpdateLogic.talkBlocks(TalkUi.Waiting(TalkUi.Phase.THINKING, "thinking")))
        assertTrue(UpdateLogic.talkBlocks(TalkUi.Speaking("hi")))
        assertFalse(UpdateLogic.talkBlocks(TalkUi.Idle()))
        assertFalse(UpdateLogic.talkBlocks(TalkUi.Done("hi")))
        assertFalse(UpdateLogic.talkBlocks(TalkUi.Later("x")))
        assertFalse(UpdateLogic.talkBlocks(TalkUi.Failed("x")))
    }

    @Test fun verifyChecksSizeAndHash() {
        val f = File.createTempFile("orbit", ".apk").apply { writeBytes(byteArrayOf(1, 2, 3)); deleteOnExit() }
        val good = MessageDigest.getInstance("SHA-256").digest(byteArrayOf(1, 2, 3)).joinToString("") { "%02x".format(it) }
        val info = UpdateInfo(45, "0.1.45", good, 3)
        assertTrue(UpdateLogic.verify(f, info))
        assertFalse(UpdateLogic.verify(f, info.copy(size = 4)))
        assertFalse(UpdateLogic.verify(f, info.copy(sha256 = sha)))
        assertFalse(UpdateLogic.verify(File(f.path + ".missing"), info))
    }
}
