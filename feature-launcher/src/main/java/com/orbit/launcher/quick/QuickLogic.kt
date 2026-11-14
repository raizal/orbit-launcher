package com.orbit.launcher.quick

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

/** The six ring toggles, in the design's order (clockwise from 12 o'clock). */
enum class Toggle(val label: String, val icon: String) {
    WIFI("Wi-Fi", "M5 13a10 10 0 0 1 14 0M8.5 16.5a5 5 0 0 1 7 0M2 8.82a15 15 0 0 1 20 0M12 20h.01"),
    BT("Bluetooth", "m7 7 10 10-5 5V2l5 5L7 17"),
    DND("Do not disturb", "M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"),
    LIGHT("Screen light", "M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7V16h8v-1.3A7 7 0 0 0 12 2z"),
    GPS("Location", "M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0ZM12 13a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"),
    SOUND("Sound", "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0"),
}

/** Pure layout + formatting math for the quick settings panel (design/QuickSettings.dc.html). */
object QuickLogic {
    const val TOGGLE_SIZE = 72
    const val RING_RADIUS = 124.0

    /**
     * Top-left of toggle [i] (design px), ported from renderVals():
     * angle = −90° + i·60°, center = 200 + 124·(cos, sin), left/top = round(center − 36).
     */
    fun togglePos(i: Int): Pair<Int, Int> {
        val a = (-90.0 + i * 60) * PI / 180
        val x = 200 + RING_RADIUS * cos(a)
        val y = 200 + RING_RADIUS * sin(a)
        return jsRound(x - TOGGLE_SIZE / 2) to jsRound(y - TOGGLE_SIZE / 2)
    }

    /** JS Math.round (half rounds toward +∞), so negative halves match the design. */
    private fun jsRound(v: Double) = kotlin.math.floor(v + 0.5).toInt()

    // Brightness arc: "M107 361.1 A186 186 0 0 0 293 361.1" — r 186 around (200,200),
    // from 120° (lower left) counter-clockwise through 90° to 60°; progress fills from the left.
    const val ARC_RADIUS = 186f
    const val ARC_START_DEG = 120f
    const val ARC_SWEEP_DEG = -60f
    const val ARC_STROKE = 8f

    /** Status line under the battery (design: DND wins, else Wi-Fi + LTE / LTE only). */
    fun status(dnd: Boolean, wifiConnected: Boolean, lte: Boolean, simAbsent: Boolean): String = when {
        dnd -> "DO NOT DISTURB"
        wifiConnected && lte -> "WI-FI + LTE"
        lte -> "LTE ONLY"
        wifiConnected -> if (simAbsent) "WI-FI · NO SIM" else "WI-FI · NO LTE"
        else -> if (simAbsent) "OFFLINE · NO SIM" else "OFFLINE"
    }

    /** System brightness (0..255) → percent shown on the arc. */
    fun brightnessFraction(value: Int): Float = (value.coerceIn(0, 255) / 255f)

    /**
     * Next system brightness after [step] rotary detents (+1 clockwise = brighter):
     * snap to the nearest 5 % and move 5 % per detent; never 0 (the screen must stay readable).
     */
    fun nextBrightness(value: Int, step: Int): Int {
        val pct = (value.coerceIn(0, 255) * 100f / 255f).roundToInt()
        val snapped = ((pct + 2) / 5) * 5
        val target = (snapped + 5 * step).coerceIn(0, 100)
        return (target * 255f / 100f).roundToInt().coerceIn(1, 255)
    }

    /** Notes recorded today: stems start with the local date "yyyy-MM-dd". */
    fun countToday(stems: List<String>, today: String): Int = stems.count { it.startsWith(today) }

    /**
     * Hours left from the discharge seen since [anchorLevel] at [anchorMs]. Null until the drop
     * is big and long enough to mean something (≥ 3 % over ≥ 30 min).
     */
    fun hoursLeftFromHistory(anchorLevel: Int, anchorMs: Long, level: Int, nowMs: Long): Double? {
        val dropped = anchorLevel - level
        val elapsedH = (nowMs - anchorMs) / 3_600_000.0
        if (dropped < 3 || elapsedH < 0.5) return null
        return level / (dropped / elapsedH)
    }

    /** Hours left from the charge counter (µAh) and the average current (µA). Null if the driver reports junk. */
    fun hoursLeftFromCurrent(chargeUah: Int, currentUa: Int): Double? {
        if (chargeUah <= 0 || chargeUah == Int.MIN_VALUE || currentUa == 0 || currentUa == Int.MIN_VALUE) return null
        val ua = kotlin.math.abs(currentUa.toDouble())
        if (ua < 1_000) return null // under 1 mA is not a real reading on a watch with the screen on
        return chargeUah / ua
    }

    /** "about 2 days" / "about 5 hours" / "under an hour". */
    fun formatRemaining(hours: Double): String = when {
        hours >= 36 -> "about ${(hours / 24).roundToInt()} days"
        hours >= 1.5 -> "about ${hours.roundToInt()} hours"
        hours >= 0.75 -> "about 1 hour"
        else -> "under an hour"
    }
}
