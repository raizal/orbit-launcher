package com.orbit.launcher.quick

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class QuickLogicTest {
    @Test fun ringMatchesDesignRenderVals() {
        // Values computed by the design's renderVals(): left/top = round(200 + 124·(cos, sin)(−90° + i·60°) − 36).
        val expected = listOf(164 to 40, 271 to 102, 271 to 226, 164 to 288, 57 to 226, 57 to 102)
        assertEquals(expected, (0 until 6).map(QuickLogic::togglePos))
        assertEquals(listOf("Wi-Fi", "Bluetooth", "Do not disturb", "Screen light", "Location", "Sound"), Toggle.entries.map { it.label })
    }

    @Test fun statusLine() {
        assertEquals("DO NOT DISTURB", QuickLogic.status(dnd = true, wifiConnected = true, lte = true, simAbsent = false))
        assertEquals("WI-FI + LTE", QuickLogic.status(false, true, true, false))
        assertEquals("LTE ONLY", QuickLogic.status(false, false, true, false))
        assertEquals("WI-FI · NO SIM", QuickLogic.status(false, true, false, true))
        assertEquals("OFFLINE · NO SIM", QuickLogic.status(false, false, false, true))
    }

    @Test fun brightnessSteps() {
        assertEquals(166, QuickLogic.nextBrightness(153, 1)) // 60 % → 65 %
        assertEquals(140, QuickLogic.nextBrightness(153, -1)) // 60 % → 55 %
        assertEquals(255, QuickLogic.nextBrightness(255, 1))
        assertEquals(1, QuickLogic.nextBrightness(13, -1)) // 5 % → 0 % clamps to 1, never black
        assertEquals(13, QuickLogic.nextBrightness(1, 1))
        assertEquals(0.6f, QuickLogic.brightnessFraction(153), 0.001f)
    }

    @Test fun todayCount() {
        val stems = listOf("2026-11-14_09-00-00+0700", "2026-11-14_12-41-02+0700", "2026-11-13_22-40-00+0700")
        assertEquals(2, QuickLogic.countToday(stems, "2026-11-14"))
        assertEquals(0, QuickLogic.countToday(emptyList(), "2026-11-14"))
    }

    @Test fun batteryEstimate() {
        val h = 3_600_000L
        assertNull(QuickLogic.hoursLeftFromHistory(80, 0, 79, 2 * h)) // too small a drop
        assertNull(QuickLogic.hoursLeftFromHistory(80, 0, 70, h / 4)) // too short
        assertEquals(76.0, QuickLogic.hoursLeftFromHistory(80, 0, 76, 4 * h)!!, 0.001) // 1 %/h
        assertNull(QuickLogic.hoursLeftFromCurrent(0, -50_000))
        assertNull(QuickLogic.hoursLeftFromCurrent(300_000, Int.MIN_VALUE))
        assertEquals(6.0, QuickLogic.hoursLeftFromCurrent(300_000, -50_000)!!, 0.001)
        assertEquals("about 3 days", QuickLogic.formatRemaining(76.0))
        assertEquals("about 6 hours", QuickLogic.formatRemaining(6.0))
        assertEquals("under an hour", QuickLogic.formatRemaining(0.5))
    }
}
