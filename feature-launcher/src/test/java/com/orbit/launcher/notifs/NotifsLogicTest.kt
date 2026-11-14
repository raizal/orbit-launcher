package com.orbit.launcher.notifs

import com.orbit.launcher.notifs.NotifListener.Raw
import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.Calendar
import java.util.TimeZone

class NotifsLogicTest {

    @Test fun filtersOwnOngoingAndDuplicateSummaries() {
        val raws = listOf(
            Raw("rec", "com.orbit", null, summary = false, ongoing = true), // recorder foreground service
            Raw("orbitDone", "com.orbit", null, summary = false, ongoing = false), // ours, normal → keep
            Raw("usb", "android", null, summary = false, ongoing = true), // others' ongoing → keep
            Raw("chatSum", "chat", "g1", summary = true, ongoing = false),
            Raw("chat1", "chat", "g1", summary = false, ongoing = false),
            Raw("chat2", "chat", "g1", summary = false, ongoing = false),
            Raw("lonelySum", "mail", "g2", summary = true, ongoing = false), // no children posted → keep
        )
        assertEquals(
            setOf("orbitDone", "usb", "chat1", "chat2", "lonelySum"),
            NotifListener.visibleKeys(raws, "com.orbit"),
        )
    }

    private val tz = TimeZone.getTimeZone("Asia/Jakarta")
    private fun at(y: Int, mo: Int, d: Int, h: Int, mi: Int) =
        Calendar.getInstance(tz).apply { clear(); set(y, mo - 1, d, h, mi) }.timeInMillis

    @Test fun age() {
        val now = at(2026, 11, 14, 13, 30)
        assertEquals("now", NotifFormat.age(now - 20_000, now, tz))
        assertEquals("5m", NotifFormat.age(now - 5 * 60_000, now, tz))
        assertEquals("09:05", NotifFormat.age(at(2026, 11, 14, 9, 5), now, tz))
        assertEquals("Yesterday", NotifFormat.age(at(2026, 11, 13, 22, 0), now, tz))
        assertEquals("2 Nov", NotifFormat.age(at(2026, 11, 2, 8, 0), now, tz))
    }

    @Test fun header() {
        assertEquals("NOTIFICATIONS", NotifFormat.header(0, 0))
        assertEquals("3 NEW", NotifFormat.header(3, 0))
        assertEquals("2 OF 3", NotifFormat.header(3, 1))
    }
}
