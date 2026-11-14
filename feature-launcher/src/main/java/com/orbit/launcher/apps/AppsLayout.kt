package com.orbit.launcher.apps

import kotlin.math.abs
import kotlin.math.floor
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Honeycomb math, ported from `renderVals()` in design/LauncherHoneycomb.dc.html.
 * All values are on the 400-unit design grid. Pure Kotlin so it is unit-tested on the JVM.
 *
 * Grid positions are kept in "cell units" (multiply by the spacing to get design px):
 * x = q + r/2, y = 0.866·r. The pan is stored in cell units too, so zooming (which only
 * changes the spacing) keeps the same cell in the center.
 */
object Honeycomb {
    const val CX = 200f
    const val CY = 196f
    const val SPACING = 82f
    const val BASE_SIZE = 72f
    const val MIN_SCALE = 0.38f
    const val FISHEYE = 60000f
    /** Farther than this from the center: dimmed (design) and not tappable (docs/launcher.md rule 4). */
    const val EDGE_DIST = 150f
    const val EDGE_ALPHA = 0.7f
    const val ROW = 0.866f

    /** Rotary zoom range. 1 = the design; 0.85…1.17 is the design's own spacing slider (70…96 px). */
    const val ZOOM_MIN = 0.8f
    const val ZOOM_MAX = 1.3f
    const val ZOOM_STEP = 0.1f

    private val DIRS = arrayOf(intArrayOf(1, 0), intArrayOf(1, -1), intArrayOf(0, -1), intArrayOf(-1, 0), intArrayOf(-1, 1), intArrayOf(0, 1))

    /** Axial (q, r) pairs for [count] cells in the design's order: center, then ring by ring. */
    fun axial(count: Int): IntArray {
        val out = IntArray(count * 2)
        if (count == 0) return out
        var n = 1
        var ring = 1
        while (n < count) {
            var q = -ring
            var r = ring
            for (side in 0 until 6) {
                for (k in 0 until ring) {
                    if (n == count) return out
                    out[2 * n] = q
                    out[2 * n + 1] = r
                    n++
                    q += DIRS[side][0]
                    r += DIRS[side][1]
                }
            }
            ring++
        }
        return out
    }

    /** Cell centers in cell units, interleaved x, y. */
    fun grid(count: Int): FloatArray {
        val a = axial(count)
        return FloatArray(count * 2) { i ->
            val q = a[i and 1.inv()]
            val r = a[(i and 1.inv()) + 1]
            if (i % 2 == 0) q + r / 2f else ROW * r
        }
    }

    /** Ring number of the outermost of [count] cells (0 for just the center). */
    fun rings(count: Int): Int {
        var ring = 0
        var total = 1
        while (total < count) { ring++; total += 6 * ring }
        return ring
    }

    fun scale(d: Float): Float = max(MIN_SCALE, 1f - d * d / FISHEYE)
    fun size(d: Float, zoom: Float): Float = BASE_SIZE * zoom * scale(d)
    fun alpha(d: Float): Float = if (d > EDGE_DIST) EDGE_ALPHA else 1f
    fun tappable(d: Float): Boolean = d <= EDGE_DIST

    /** Index of the cell nearest to the point ([x], [y]) in cell units. */
    fun nearest(grid: FloatArray, x: Float, y: Float): Int {
        var best = 0
        var bestD = Float.MAX_VALUE
        var i = 0
        while (i < grid.size / 2) {
            val dx = grid[2 * i] - x
            val dy = grid[2 * i + 1] - y
            val d = dx * dx + dy * dy
            if (d < bestD) { bestD = d; best = i }
            i++
        }
        return best
    }

    /**
     * Cell under a tap at design point ([tx], [ty]) with pan ([panX], [panY]) in cell units, or -1.
     * Picks the nearest cell center within 0.6 cells, so hit areas tile the screen (≈ 80 px each).
     */
    fun hit(grid: FloatArray, tx: Float, ty: Float, panX: Float, panY: Float, spacing: Float): Int {
        if (grid.isEmpty()) return -1
        val ux = (tx - CX) / spacing - panX
        val uy = (ty - CY) / spacing - panY
        val i = nearest(grid, ux, uy)
        return if (hypot(grid[2 * i] - ux, grid[2 * i + 1] - uy) <= 0.6f) i else -1
    }

    /** Distance (design px) of cell [i] from the lens center. */
    fun distance(grid: FloatArray, i: Int, panX: Float, panY: Float, spacing: Float): Float =
        spacing * hypot(grid[2 * i] + panX, grid[2 * i + 1] + panY)
}

/**
 * Curved-list math from design/LauncherList.dc.html, made continuous so the list can
 * scroll smoothly: `s` is a row's offset from the focused slot (0 = center row, ±1, ±2 =
 * the design's other rows, fractional while scrolling).
 */
object CurvedList {
    /** Design row centers for s = -2..2. */
    private val YS = floatArrayOf(70f, 132f, 200f, 268f, 330f)
    private const val OUTER_GAP = 62f

    /** Design px per row while dragging (gap between the center rows). */
    const val ROW_PX = 68f
    /** Layout size of the focused row; other rows are drawn scaled from it. */
    const val FOCUS_ICON = 56f
    const val FOCUS_H = 72f
    const val FOCUS_FONT = 20f
    const val GAP = 14f
    const val RIGHT = 352f

    fun y(s: Float): Float {
        if (s <= -2f) return YS[0] + (s + 2f) * OUTER_GAP
        if (s >= 2f) return YS[4] + (s - 2f) * OUTER_GAP
        val i = floor(s).toInt() + 2
        val f = s - floor(s)
        return YS[i] + (YS[min(i + 1, 4)] - YS[i]) * f
    }

    private fun interp(a: Float, v0: Float, v1: Float, v2: Float, v3: Float): Float = when {
        a <= 1f -> v0 + (v1 - v0) * a
        a <= 2f -> v1 + (v2 - v1) * (a - 1f)
        else -> v2 + (v3 - v2) * min(a - 2f, 1f)
    }

    /** 0 off-center … 1 exactly centered. */
    fun focus(s: Float): Float = 1f - min(abs(s), 1f)

    fun icon(s: Float): Float = interp(abs(s), FOCUS_ICON, 46f, 38f, 30f)
    fun alpha(s: Float): Float = interp(abs(s), 1f, 0.85f, 0.5f, 0f)
    fun fontSize(s: Float): Float = 16f + 4f * focus(s)
    fun height(s: Float): Float = icon(s) + (FOCUS_H - FOCUS_ICON) * focus(s)
    fun pad(s: Float): Float = 8f * focus(s)

    /** Left edge of the row: follows the circle (r 178) plus an inset (18 focused, 34 others). */
    fun left(s: Float): Float {
        val dy = min(abs(y(s) - 200f), 177f)
        val edge = 200f - sqrt(178f * 178f - dy * dy)
        return edge + 34f - 16f * focus(s)
    }

    fun iconLeft(s: Float): Float = left(s) + pad(s)
    fun textLeft(s: Float): Float = iconLeft(s) + icon(s) + GAP

    /** Scrollbar thumb length as a fraction of the rim arc. */
    fun thumbLength(count: Int): Float = if (count <= 0) 1f else (5f / count).coerceIn(0.12f, 1f)

    /** Scrollbar thumb start as a fraction of the rim arc. */
    fun thumbStart(pos: Float, count: Int): Float =
        if (count <= 1) 0f else (pos / (count - 1)).coerceIn(0f, 1f) * (1f - thumbLength(count))
}
