package com.orbit.launcher.faces

import com.orbit.launcher.face.Face
import com.orbit.launcher.face.Layer
import org.junit.Assert.assertEquals
import org.junit.Test

class FaceEditTest {

    private fun comp(slot: Int, x: Float, y: Float, default: String = "none") =
        Layer(type = "complication", slot = slot, x = x, y = y, default = default)

    @Test fun slotsFromTopLevelSortedBySlot() {
        val face = Face("f", "F", layers = listOf(
            comp(3, 272f, 286f, "battery"), Layer(type = "text"), comp(1, 128f, 286f, "weather"), comp(2, 200f, 286f),
        ))
        val s = complicationSlots(face)
        assertEquals(listOf(1, 2, 3), s.map { it.slot })
        assertEquals(SlotSpot(1, 128f, 286f, "weather"), s[0])
    }

    @Test fun slotsInsideNestedGroupsAreOffsetLikeTheRenderer() {
        val face = Face("f", "F", layers = listOf(
            Layer(type = "group", x = 220f, y = 190f, layers = listOf(
                comp(1, 100f, 100f),
                Layer(type = "group", x = 210f, y = 200f, layers = listOf(comp(2, 50f, 60f))),
            )),
        ))
        val s = complicationSlots(face)
        assertEquals(SlotSpot(1, 120f, 90f, "none"), s[0])
        assertEquals(SlotSpot(2, 80f, 50f, "none"), s[1])
    }

    @Test fun slotsScaleFromFaceSize() {
        val face = Face("f", "F", size = 200f, layers = listOf(comp(1, 50f, 150f)))
        assertEquals(SlotSpot(1, 100f, 300f, "none"), complicationSlots(face).single())
    }

    @Test fun duplicateSlotFirstWins() {
        val face = Face("f", "F", layers = listOf(comp(1, 10f, 10f, "date"), comp(1, 20f, 20f, "weather")))
        assertEquals("date", complicationSlots(face).single().default)
    }

    @Test fun cycleWrapsBothWays() {
        val l = listOf("a", "b", "c")
        assertEquals("b", cycle(l, "a", 1))
        assertEquals("a", cycle(l, "c", 1))
        assertEquals("c", cycle(l, "a", -1))
        assertEquals("a", cycle(l, "b", -4))
    }

    @Test fun cycleFromUnknownValue() {
        val l = listOf("a", "b", "c")
        assertEquals("a", cycle(l, "zzz", 1))
        assertEquals("c", cycle(l, null, -1))
        assertEquals("x", cycle(emptyList(), "x", 1))
    }

    @Test fun cardStaysAtDesignSpotForBottomRowSlots() {
        val spots = listOf(SlotSpot(1, 128f, 286f, "w"), SlotSpot(2, 200f, 286f, "v"), SlotSpot(3, 272f, 286f, "b"))
        assertEquals(CARD_TOP, cardBox(spots))
    }

    @Test fun cardMovesBelowMiddleSlots() {
        val spots = listOf(SlotSpot(1, 118f, 200f, "w"), SlotSpot(2, 282f, 200f, "d"))
        assertEquals(CARD_LOW, cardBox(spots))
    }

    @Test fun pickerItemMatchesDesign() {
        assertEquals(PickerItem(200f, 184f, 220f, 1f, 1f), pickerItem(0f))
        val left = pickerItem(-1f)
        assertEquals(-40f, left.cx, 0.01f)
        assertEquals(190f, left.cy, 0.01f)
        assertEquals(160f, left.size, 0.01f)
        assertEquals(0.6f, left.alpha, 0.01f)
        assertEquals(440f, pickerItem(1f).cx, 0.01f)
        assertEquals(0f, pickerItem(1.6f).alpha, 0.001f)
    }

    @Test fun snapStepsOnSmallDragAndClamps() {
        assertEquals(2, snapIndex(2, 2.1f, 5))
        assertEquals(3, snapIndex(2, 2.2f, 5))
        assertEquals(1, snapIndex(2, 1.8f, 5))
        assertEquals(4, snapIndex(4, 4.5f, 5))
        assertEquals(0, snapIndex(0, -0.3f, 5))
    }
}
