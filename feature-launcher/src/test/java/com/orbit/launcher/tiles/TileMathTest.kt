package com.orbit.launcher.tiles

import com.orbit.launcher.data.Weather
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class TileMathTest {
    private fun near(expected: Float, actual: Float) = assertEquals(expected, actual, 0.2f)

    @Test fun arcEndsMatchDesignPath() {
        // M79.8 320.2 A170 ... 320.2 320.2
        val (x0, y0) = TileMath.arcPoint(0f)
        near(79.8f, x0); near(320.2f, y0)
        val (x1, y1) = TileMath.arcPoint(1f)
        near(320.2f, x1); near(320.2f, y1)
    }

    @Test fun sunDotMatchesDesignAt39Percent() {
        val (x, y) = TileMath.arcPoint(0.39f)
        near(115.8f, x); near(52.3f, y)
    }

    @Test fun pageDotsMatchDesign() {
        val expected = listOf(170.9f to 383.8f, 190.3f to 385.7f, 209.7f to 385.7f, 229.1f to 383.8f)
        expected.forEachIndexed { i, (ex, ey) ->
            val (x, y) = TileMath.pageDot(i, 4)
            near(ex, x); near(ey, y)
        }
        val (cx, _) = TileMath.pageDot(1, 3)
        near(200f, cx)
    }

    @Test fun sunFraction() {
        assertEquals(0.5f, TileMath.sunFraction("06:00", "18:00", 12 * 60)!!, 0.001f)
        assertNull(TileMath.sunFraction("06:00", "18:00", 5 * 60))
        assertNull(TileMath.sunFraction("06:00", "18:00", 19 * 60))
        assertNull(TileMath.sunFraction(null, "18:00", 12 * 60))
        assertNull(TileMath.sunFraction("bad", "18:00", 12 * 60))
    }

    @Test fun summaryAndColumns() {
        val hours = (13..20).map { Weather.Hour(it, 40 - it, 1) }
        val w = Weather(temp = 31, code = 0, high = 33, low = 25, hourly = hours)
        assertEquals("Clear · H 33° L 25°", TileMath.summary(w))
        assertEquals(listOf(13, 15, 17, 19), TileMath.columns(hours).map { it.hour })
        assertEquals("Cloudy", TileMath.summary(Weather(temp = 1, code = 3)))
    }

    @Test fun rainLine() {
        val dry = Weather(temp = 30, code = 1, hourly = listOf(Weather.Hour(13, 30, 2, 10), Weather.Hour(14, 30, 3, 20)))
        assertNull(TileMath.rainLine(dry))
        val later = dry.copy(hourly = dry.hourly + Weather.Hour(15, 28, 61, 60))
        assertEquals("Rain 60% after 15:00", TileMath.rainLine(later))
        val likely = dry.copy(hourly = listOf(Weather.Hour(9, 28, 3, 55)))
        assertEquals("Rain 55% after 09:00", TileMath.rainLine(likely))
        val noProb = dry.copy(hourly = listOf(Weather.Hour(16, 28, 80)))
        assertEquals("Rain after 16:00", TileMath.rainLine(noProb))
        assertNull(TileMath.rainLine(later.copy(code = 63))) // already raining
    }
}
