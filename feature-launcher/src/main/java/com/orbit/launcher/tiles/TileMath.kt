package com.orbit.launcher.tiles

import com.orbit.launcher.data.Weather
import kotlin.math.cos
import kotlin.math.sin

/** Layout math and text for the tiles, ported from `design/Tiles.dc.html` (400-unit grid). Pure, unit-tested. */
internal object TileMath {

    /** Rim arc (radius 170): starts bottom-left at 135°, runs clockwise over the top, 270° long. */
    const val ARC_R = 170f
    const val ARC_START = 135f
    const val ARC_SWEEP = 270f

    /** Point on the rim arc at [fraction] 0..1 (the sun on the weather tile). */
    fun arcPoint(fraction: Float): Pair<Float, Float> {
        val a = Math.toRadians((ARC_START + ARC_SWEEP * fraction.coerceIn(0f, 1f)).toDouble())
        return (200 + ARC_R * cos(a)).toFloat() to (200 + ARC_R * sin(a)).toFloat()
    }

    /** Page dot centers along the bottom rim: radius 186, 6° apart, centered on 6 o'clock. */
    fun pageDot(i: Int, count: Int): Pair<Float, Float> {
        val mid = (count - 1) / 2f
        val a = Math.toRadians((90 + (mid - i) * 6f).toDouble())
        return (200 + 186 * cos(a)).toFloat() to (200 + 186 * sin(a)).toFloat()
    }

    /** "05:20" → 320. */
    fun minutes(hhmm: String?): Int? {
        if (hhmm == null || hhmm.length < 5 || hhmm[2] != ':') return null
        val h = hhmm.substring(0, 2).toIntOrNull() ?: return null
        val m = hhmm.substring(3, 5).toIntOrNull() ?: return null
        return h * 60 + m
    }

    /** How far the sun is between sunrise and sunset (0..1); null at night or when unknown. */
    fun sunFraction(sunrise: String?, sunset: String?, nowMinutes: Int): Float? {
        val rise = minutes(sunrise) ?: return null
        val set = minutes(sunset) ?: return null
        if (set <= rise || nowMinutes < rise || nowMinutes > set) return null
        return (nowMinutes - rise).toFloat() / (set - rise)
    }

    /** WMO weather code → short words. */
    fun describe(code: Int): String = when (code) {
        0 -> "Clear"
        1 -> "Mostly clear"
        2 -> "Partly cloudy"
        3 -> "Cloudy"
        45, 48 -> "Fog"
        in 51..57 -> "Drizzle"
        in 61..67 -> "Rain"
        in 71..77, 85, 86 -> "Snow"
        in 80..82 -> "Showers"
        in 95..99 -> "Thunderstorm"
        else -> "—"
    }

    fun isWet(code: Int) = code in 51..67 || code in 80..82 || code in 95..99

    /** "Clear · H 33° L 25°". */
    fun summary(w: Weather): String = buildString {
        append(describe(w.code))
        if (w.high != null && w.low != null) append(" · H ${w.high}° L ${w.low}°")
    }

    /** The four hourly columns: every second hour of the next eight. */
    fun columns(hourly: List<Weather.Hour>): List<Weather.Hour> = hourly.filterIndexed { i, _ -> i % 2 == 0 }.take(4)

    /**
     * "Rain 60% after 15:00" — first coming hour that is wet or ≥ 50 % likely to be;
     * null if it already rains now (the summary says so) or none is coming.
     */
    fun rainLine(w: Weather): String? {
        if (isWet(w.code)) return null
        val h = w.hourly.firstOrNull { isWet(it.code) || (it.rain ?: 0) >= 50 } ?: return null
        val p = h.rain?.takeIf { it > 0 }?.let { " $it%" } ?: ""
        return "Rain$p after ${h.hour.toString().padStart(2, '0')}:00"
    }
}
