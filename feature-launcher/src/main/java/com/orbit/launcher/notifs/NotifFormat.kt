package com.orbit.launcher.notifs

import java.util.Calendar
import java.util.TimeZone

/** Pure helpers for the notifications panel (unit-tested). */
internal object NotifFormat {

    /** "now", "5m", "13:05" (today), "Yesterday", "12 Nov". */
    fun age(postedAt: Long, now: Long, tz: TimeZone = TimeZone.getDefault()): String {
        val mins = (now - postedAt) / 60_000
        if (mins < 1) return "now"
        if (mins < 60) return "${mins}m"
        val p = Calendar.getInstance(tz).apply { timeInMillis = postedAt }
        val n = Calendar.getInstance(tz).apply { timeInMillis = now }
        if (sameDay(p, n)) return "%02d:%02d".format(p.get(Calendar.HOUR_OF_DAY), p.get(Calendar.MINUTE))
        n.add(Calendar.DAY_OF_YEAR, -1)
        if (sameDay(p, n)) return "Yesterday"
        return "${p.get(Calendar.DAY_OF_MONTH)} ${MONTHS[p.get(Calendar.MONTH)]}"
    }

    /** Rim title: "3 NEW" on the newest, "2 OF 3" once scrolled; "NOTIFICATIONS" when empty. */
    fun header(count: Int, focus: Int): String = when {
        count == 0 -> "NOTIFICATIONS"
        focus == 0 -> "$count NEW"
        else -> "${focus + 1} OF $count"
    }

    private fun sameDay(a: Calendar, b: Calendar) =
        a.get(Calendar.YEAR) == b.get(Calendar.YEAR) && a.get(Calendar.DAY_OF_YEAR) == b.get(Calendar.DAY_OF_YEAR)

    private val MONTHS = arrayOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
}
