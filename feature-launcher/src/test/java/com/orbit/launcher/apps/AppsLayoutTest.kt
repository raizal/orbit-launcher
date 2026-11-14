package com.orbit.launcher.apps

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.roundToInt

class HoneycombTest {
    @Test fun spiralMatchesDesignOrder() {
        // From the design script: center, then ring 1 starting at (-1, 1), then ring 2 at (-2, 2).
        val a = Honeycomb.axial(19)
        val pairs = (0 until 19).map { a[2 * it] to a[2 * it + 1] }
        assertEquals(0 to 0, pairs[0])
        assertEquals(listOf(-1 to 1, 0 to 1, 1 to 0, 1 to -1, 0 to -1, -1 to 0), pairs.subList(1, 7))
        assertEquals(-2 to 2, pairs[7])
        assertEquals(19, pairs.toSet().size)
    }

    @Test fun ringCounts() {
        assertEquals(0, Honeycomb.rings(1))
        assertEquals(1, Honeycomb.rings(7))
        assertEquals(2, Honeycomb.rings(8))
        assertEquals(2, Honeycomb.rings(19))
        assertEquals(3, Honeycomb.rings(20))
    }

    @Test fun sizesMatchDesignAtRest() {
        // Design: size = round(72 · max(0.38, 1 − d²/60000)), d from (200, 196).
        val grid = Honeycomb.grid(19)
        for (i in 0 until 19) {
            val x = 200 + 82 * grid[2 * i]
            val y = 196 + 82 * grid[2 * i + 1]
            val d = hypot(x - 200, y - 196)
            val expected = (72 * max(0.38f, 1 - d * d / 60000)).roundToInt()
            assertEquals(expected, Honeycomb.size(Honeycomb.distance(grid, i, 0f, 0f, 82f), 1f).roundToInt())
        }
        assertEquals(72, Honeycomb.size(0f, 1f).roundToInt())
        assertEquals(64, Honeycomb.size(82f, 1f).roundToInt())
    }

    @Test fun edgeCellsAreDimAndNotTappable() {
        val grid = Honeycomb.grid(19)
        // Ring 2 corner (-2, 2): d = 164 > 150.
        val d = Honeycomb.distance(grid, 7, 0f, 0f, 82f)
        assertEquals(164f, d, 0.5f)
        assertFalse(Honeycomb.tappable(d))
        assertEquals(0.7f, Honeycomb.alpha(d))
        // Pan it to the center: tappable.
        assertTrue(Honeycomb.tappable(Honeycomb.distance(grid, 7, -grid[14], -grid[15], 82f)))
    }

    @Test fun hitPicksNearestCell() {
        val grid = Honeycomb.grid(19)
        assertEquals(0, Honeycomb.hit(grid, 200f, 196f, 0f, 0f, 82f))
        assertEquals(0, Honeycomb.hit(grid, 230f, 210f, 0f, 0f, 82f))
        // Cell (1, 0) is at x = 282.
        assertEquals(3, Honeycomb.hit(grid, 282f, 196f, 0f, 0f, 82f))
        // Panned one cell left: (1, 0) is now in the center.
        assertEquals(3, Honeycomb.hit(grid, 200f, 196f, -1f, 0f, 82f))
        // Far outside the grid.
        assertEquals(-1, Honeycomb.hit(grid, 0f, 0f, 0f, 0f, 82f))
    }

    @Test fun nearestFollowsPan() {
        val grid = Honeycomb.grid(7)
        assertEquals(0, Honeycomb.nearest(grid, 0f, 0f))
        assertEquals(3, Honeycomb.nearest(grid, 0.9f, 0.1f))
    }
}

class CurvedListTest {
    @Test fun integerSlotsMatchDesign() {
        // design: ys = [70, 132, 200, 268, 330]; left = round(edge + (focus ? 18 : 34))
        val ys = floatArrayOf(70f, 132f, 200f, 268f, 330f)
        val icons = floatArrayOf(38f, 46f, 56f, 46f, 38f)
        val lefts = intArrayOf(112, 70, 40, 70, 112)
        for (k in 0..4) {
            val s = (k - 2).toFloat()
            assertEquals(ys[k], CurvedList.y(s), 0.01f)
            assertEquals(icons[k], CurvedList.icon(s), 0.01f)
            assertEquals(lefts[k].toFloat(), CurvedList.left(s), 0.6f)
        }
        assertEquals(72f, CurvedList.height(0f), 0.01f)
        assertEquals(46f, CurvedList.height(1f), 0.01f)
        assertEquals(20f, CurvedList.fontSize(0f), 0.01f)
        assertEquals(16f, CurvedList.fontSize(1f), 0.01f)
        assertEquals(0.85f, CurvedList.alpha(-1f), 0.001f)
        assertEquals(0.5f, CurvedList.alpha(2f), 0.001f)
        assertEquals(0f, CurvedList.alpha(3f), 0.001f)
    }

    @Test fun continuousBetweenSlots() {
        assertEquals(234f, CurvedList.y(0.5f), 0.01f)
        assertEquals(51f, CurvedList.icon(0.5f), 0.01f)
        assertEquals(8f, CurvedList.y(-3f), 0.01f)
    }

    @Test fun thumb() {
        assertEquals(1f, CurvedList.thumbLength(3), 0f)
        assertEquals(0f, CurvedList.thumbStart(0f, 40), 0f)
        assertEquals(1f - CurvedList.thumbLength(40), CurvedList.thumbStart(39f, 40), 1e-6f)
    }
}
