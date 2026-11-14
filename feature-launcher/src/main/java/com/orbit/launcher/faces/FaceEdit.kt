package com.orbit.launcher.faces

import com.orbit.launcher.face.Face
import com.orbit.launcher.face.Layer
import kotlin.math.abs
import kotlin.math.min
import kotlin.math.roundToInt

/** Pure layout / editing helpers for the face picker and customize screens (unit-tested). */

/** A complication slot of a face, at its on-screen center in design px (400 grid). */
data class SlotSpot(val slot: Int, val x: Float, val y: Float, val default: String)

/**
 * Every `complication` layer of [face], also inside (nested) groups, one per slot
 * number (first wins), sorted by slot. Positions follow ComplicationLayers: a group
 * shifts its children by (group.x − 200, group.y − 200) face units, then the face
 * is scaled from its `size` grid to 400.
 */
fun complicationSlots(face: Face): List<SlotSpot> {
    val k = 400f / face.size
    val out = LinkedHashMap<Int, SlotSpot>()
    fun walk(layers: List<Layer>, dx: Float, dy: Float) {
        layers.forEach { l ->
            when (l.type) {
                "complication" -> if (l.slot !in out) out[l.slot] = SlotSpot(l.slot, (l.x + dx) * k, (l.y + dy) * k, l.default)
                "group" -> walk(l.layers, dx + l.x - 200, dy + l.y - 200)
            }
        }
    }
    walk(face.layers, 0f, 0f)
    return out.values.sortedBy { it.slot }
}

/**
 * The value [step] places after [current] in [options], wrapping around. A current
 * value that is not in the list (unknown type, custom color) goes to the first
 * option on a forward step and the last on a backward one.
 */
fun <T> cycle(options: List<T>, current: T?, step: Int): T? {
    if (options.isEmpty()) return current
    val i = options.indexOf(current)
    if (i < 0) return if (step >= 0) options.first() else options.last()
    return options[(i + step).mod(options.size)]
}

/** Where the data-source card sits on the customize screen (design px). */
data class CardBox(val left: Float, val top: Float, val width: Float, val height: Float)

/** Design position (FaceCustomize): above the bottom slot row. */
val CARD_TOP = CardBox(64f, 112f, 272f, 104f)
/** For faces with slots in the middle band (e.g. Analog): below them, above the Done row. */
val CARD_LOW = CardBox(80f, 240f, 240f, 84f)

const val CHIP_RADIUS = 36f

/** The card goes where it covers no slot chip: the design spot first, else lower down. */
fun cardBox(spots: List<SlotSpot>): CardBox {
    fun clear(c: CardBox) = spots.none { s ->
        s.x + CHIP_RADIUS > c.left && s.x - CHIP_RADIUS < c.left + c.width &&
            s.y + CHIP_RADIUS > c.top - 4 && s.y - CHIP_RADIUS < c.top + c.height + 4
    }
    return when {
        clear(CARD_TOP) -> CARD_TOP
        clear(CARD_LOW) -> CARD_LOW
        else -> CARD_TOP
    }
}

// --- Picker carousel -----------------------------------------------------------

/** Horizontal distance between neighboring previews (design: centers at −40, 200, 440). */
const val PICKER_SPACING = 240f

/** One preview of the carousel at distance [d] (in items) from the center. */
data class PickerItem(val cx: Float, val cy: Float, val size: Float, val alpha: Float, val focus: Float)

/**
 * FacePicker: center preview 220 px at (200, 184), neighbors 160 px at (∓40, 190),
 * 60 % opacity; further items fade out.
 */
fun pickerItem(d: Float): PickerItem {
    val t = min(abs(d), 1f)
    val far = (abs(d) - 1f).coerceIn(0f, 0.6f) / 0.6f
    return PickerItem(
        cx = 200f + d * PICKER_SPACING,
        cy = 184f + 6f * t,
        size = 220f - 60f * t,
        alpha = (1f - 0.4f * t) * (1f - far),
        focus = 1f - t,
    )
}

/** Index to settle on after a drag that moved the carousel from [start] to [pos]. */
fun snapIndex(start: Int, pos: Float, count: Int): Int {
    if (count <= 0) return 0
    val moved = pos - start
    val target = when {
        moved > 0.15f -> start + maxOf(1, moved.roundToInt())
        moved < -0.15f -> start + minOf(-1, moved.roundToInt())
        else -> start
    }
    return target.coerceIn(0, count - 1)
}
