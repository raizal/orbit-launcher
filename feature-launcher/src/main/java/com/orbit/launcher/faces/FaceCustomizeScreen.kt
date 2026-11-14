package com.orbit.launcher.faces

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.orbit.core.CurvedText
import com.orbit.core.OrbitColors
import com.orbit.core.OrbitFonts
import com.orbit.core.RotaryEffect
import com.orbit.core.SvgIcon
import com.orbit.launcher.LauncherEnv
import com.orbit.launcher.LauncherNav
import com.orbit.launcher.Screen
import com.orbit.launcher.face.Complication
import com.orbit.launcher.face.FaceColors
import com.orbit.launcher.face.FaceData
import com.orbit.launcher.face.FaceEntry
import com.orbit.launcher.face.FaceView
import com.orbit.launcher.face.weatherIcon

private const val CHEV_LEFT = "m15 18-6-6 6-6"
private const val CHEV_RIGHT = "m9 18 6-6-6-6"
private const val CHECK = "M20 6 9 17l-5-5"
private val CHIP_BG = Color(0xFF161615)
private val DASH = Color(0xFF4A4A46)
private val DIM_TEXT = Color(0xFF6E6E69)

/**
 * Face customize (design/FaceCustomize): the face dimmed behind, a chip on each
 * complication slot (tap to select), and a card showing the selected slot's data
 * source. Rotary, swipe or the card arrows change it; if the face offers accent
 * tweaks, an accent chip next to Done edits the color the same way. Every change
 * is saved at once (so BACK also keeps it); Done returns to the face.
 */
@Composable
fun FaceCustomizeScreen(nav: LauncherNav, env: LauncherEnv) {
    val faces by env.faces.faces.collectAsState()
    val data by env.data.collectAsState()
    val faceId = remember { nav.editingFaceId ?: env.prefs.faceId }
    val face = (faces.firstOrNull { it.id == faceId } as? FaceEntry.Ok)?.face

    fun done() {
        if (face != null) env.prefs.faceId = face.id // customizing a face also puts it on
        nav.go(Screen.FACE)
    }

    if (face == null) {
        Box(Modifier.fillMaxSize()) {
            BasicText(
                "Face not available",
                Modifier.offset(0.dp, 180.dp).width(400.dp),
                style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 15.sp, color = OrbitColors.Muted, textAlign = TextAlign.Center),
            )
            DoneButton(150f, OrbitColors.Accent) { nav.go(Screen.FACE) }
        }
        return
    }

    val spots = remember(face) { complicationSlots(face) }
    val accents = face.tweaks["accent"].orEmpty()
    val types = remember(face) {
        mutableStateMapOf<Int, String>().apply { spots.forEach { put(it.slot, env.prefs.slot(face.id, it.slot) ?: it.default) } }
    }
    var accent by remember(face) { mutableStateOf(env.prefs.accent(face.id)) }
    // Edit targets: the slots in order, then the accent (if the face offers tweaks).
    val targetCount = spots.size + if (accents.isNotEmpty()) 1 else 0
    var selected by remember(face) { mutableIntStateOf(0) }
    if (selected >= targetCount) selected = (targetCount - 1).coerceAtLeast(0)
    val onAccent = accents.isNotEmpty() && selected == spots.size
    val accentColor = FaceColors(face.theme + accentPair(accent))["accent"]

    val change: (Int) -> Unit = { step ->
        if (onAccent) {
            val next = cycle(accents, accent ?: face.theme["accent"], step)
            accent = next
            env.prefs.setAccent(face.id, next)
        } else spots.getOrNull(selected)?.let { s ->
            val next = cycle(Complication.ALL, types[s.slot], step) ?: Complication.NONE
            types[s.slot] = next
            env.prefs.setSlot(face.id, s.slot, next)
        }
    }
    val latestChange by rememberUpdatedState(change)
    RotaryEffect { latestChange(it) }

    val card = remember(spots) { cardBox(spots) }
    var drag by remember { mutableFloatStateOf(0f) }

    Box(
        Modifier
            .fillMaxSize()
            .pointerInput(Unit) {
                detectHorizontalDragGestures(onDragEnd = { drag = 0f }, onDragCancel = { drag = 0f }) { c, dx ->
                    c.consume()
                    drag += dx / density
                    // Swipe left = next (like paging), one step per 60 px.
                    while (drag <= -60f) { latestChange(1); drag += 60f }
                    while (drag >= 60f) { latestChange(-1); drag -= 60f }
                }
            },
    ) {
        // Live preview, dimmed; a cover on top keeps its complications from taking taps.
        Box(Modifier.fillMaxSize().graphicsLayer { alpha = 0.3f }) {
            FaceView(
                face = face,
                data = data,
                accentOverride = accent,
                slotType = { slot, default -> types[slot] ?: default },
                onComplication = {},
            )
        }
        Box(Modifier.fillMaxSize().clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) {})

        Canvas(Modifier.fillMaxSize()) {
            val unit = size.width / 400f
            drawArc(
                Color(0xFF1A1A18), 135f, 270f, false,
                topLeft = androidx.compose.ui.geometry.Offset(12f * unit, 12f * unit),
                size = androidx.compose.ui.geometry.Size(376f * unit, 376f * unit),
                style = Stroke(8f * unit, cap = androidx.compose.ui.graphics.StrokeCap.Round),
            )
        }
        CurvedText(
            when {
                onAccent -> "ACCENT COLOR"
                spots.isEmpty() -> "NO SLOTS"
                else -> "SLOT ${selected + 1} OF ${spots.size}"
            },
            top = true, color = accentColor, sizeSp = 14f, letterSpacingPx = 2.5f,
        )

        // Slot chips at the slots' real positions on the face.
        spots.forEachIndexed { i, s ->
            SlotChip(s, i, types[s.slot] ?: s.default, i == selected, accentColor, data) { selected = i }
        }

        if (targetCount > 0) {
            SourceCard(
                card = card,
                icon = { if (onAccent) Box(Modifier.size(26.dp).clip(CircleShape).background(accentColor)) else SourceIcon(types[spots[selected].slot], data, accentColor, 26f) },
                label = if (onAccent) "Accent" else Complication.label(types[spots[selected].slot] ?: Complication.NONE),
                position = if (onAccent) {
                    "${accents.indexOf(accent ?: face.theme["accent"]) + 1} of ${accents.size}".takeUnless { it.startsWith("0 ") } ?: "custom"
                } else {
                    "${Complication.ALL.indexOf(types[spots[selected].slot]) + 1} of ${Complication.ALL.size}"
                },
                onStep = { latestChange(it) },
            )
        }

        // Bottom row: [accent chip] [Done].
        if (accents.isNotEmpty()) {
            AccentChip(accentColor, onAccent, left = 120f) { selected = spots.size }
            DoneButton(184f, accentColor, ::done)
        } else {
            DoneButton(152f, accentColor, ::done)
        }
    }
}

@Composable
private fun SlotChip(s: SlotSpot, index: Int, type: String, selected: Boolean, accent: Color, data: FaceData, onClick: () -> Unit) {
    val d = 2 * CHIP_RADIUS
    Box(
        Modifier
            .offset((s.x - CHIP_RADIUS).dp, (s.y - CHIP_RADIUS).dp)
            .size(d.dp)
            .clip(CircleShape)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        if (selected) {
            Box(
                Modifier.size(72.dp).border(3.dp, accent, CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                Column(
                    Modifier.size(58.dp).clip(CircleShape).background(CHIP_BG),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    SourceIcon(type, data, accent, 15f)
                    BasicText(
                        shortValue(type, data),
                        style = TextStyle(fontFamily = OrbitFonts.Grotesk, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, color = OrbitColors.Ink),
                    )
                }
            }
        } else {
            Canvas(Modifier.size(60.dp)) {
                val w = 2.dp.toPx()
                drawCircle(
                    DASH, radius = size.minDimension / 2 - w / 2,
                    style = Stroke(w, pathEffect = PathEffect.dashPathEffect(floatArrayOf(6.dp.toPx(), 4.dp.toPx()))),
                )
            }
            BasicText(
                "${index + 1}",
                style = TextStyle(fontFamily = OrbitFonts.Grotesk, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, color = DIM_TEXT),
            )
        }
    }
}

@Composable
private fun SourceIcon(type: String?, data: FaceData, color: Color, size: Float) {
    val path = if (type == Complication.WEATHER) weatherIcon(data.weatherCode) else Complication.icon(type ?: Complication.NONE)
    if (path.isNotEmpty()) SvgIcon(path, size.dp, color, strokeWidth = 2.2f)
}

private fun shortValue(type: String, data: FaceData): String = when (type) {
    Complication.WEATHER -> data.weatherTemp?.let { "$it°" } ?: "--"
    Complication.BATTERY -> data.batteryLevel?.toString() ?: "--"
    Complication.DATE -> data.resolve("{date.d}") ?: "--"
    Complication.NOTIF -> data.notifUnread?.toString() ?: "–"
    Complication.VOICE_NOTE -> "Note"
    else -> "–"
}

@Composable
private fun SourceCard(card: CardBox, icon: @Composable () -> Unit, label: String, position: String, onStep: (Int) -> Unit) {
    Box(
        Modifier
            .offset(card.left.dp, card.top.dp)
            .size(card.width.dp, card.height.dp)
            .clip(RoundedCornerShape(28.dp))
            .background(OrbitColors.Surface),
    ) {
        Column(
            Modifier.fillMaxSize(),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(4.dp, Alignment.CenterVertically),
        ) {
            icon()
            BasicText(label, style = TextStyle(fontFamily = OrbitFonts.Inter, fontWeight = FontWeight.SemiBold, fontSize = 17.sp, color = OrbitColors.Ink))
            BasicText(position, style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 12.sp, color = OrbitColors.Muted))
        }
        // Arrows: 48 px visuals (design) on full-height 64 px tap areas at the card's ends.
        Arrow(CHEV_LEFT, Modifier.align(Alignment.CenterStart)) { onStep(-1) }
        Arrow(CHEV_RIGHT, Modifier.align(Alignment.CenterEnd)) { onStep(1) }
    }
}

@Composable
private fun Arrow(path: String, modifier: Modifier, onClick: () -> Unit) {
    Box(
        modifier.width(64.dp).fillMaxHeight().clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { SvgIcon(path, 22.dp, OrbitColors.Muted, strokeWidth = 2.4f) }
}

@Composable
private fun AccentChip(accent: Color, selected: Boolean, left: Float, onClick: () -> Unit) {
    Box(
        Modifier.offset(left.dp, 330.dp).size(52.dp).clip(CircleShape).clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            Modifier
                .size(48.dp)
                .then(if (selected) Modifier.border(3.dp, accent, CircleShape) else Modifier.border(2.dp, DASH, CircleShape)),
            contentAlignment = Alignment.Center,
        ) { Box(Modifier.size(26.dp).clip(CircleShape).background(accent)) }
    }
}

@Composable
private fun DoneButton(left: Float, accent: Color, onClick: () -> Unit) {
    // Visual pill 96 × 44, tap area 96 × 60.
    Box(
        Modifier.offset(left.dp, 326.dp).size(96.dp, 60.dp).clickable(
            interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClick,
        ),
        contentAlignment = Alignment.TopCenter,
    ) {
        Row(
            Modifier.offset(y = 4.dp).size(96.dp, 44.dp).clip(RoundedCornerShape(22.dp)).background(accent),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SvgIcon(CHECK, 16.dp, OrbitColors.OnAccent, strokeWidth = 2.6f)
            Spacer(Modifier.width(6.dp))
            BasicText("Done", style = TextStyle(fontFamily = OrbitFonts.Inter, fontWeight = FontWeight.SemiBold, fontSize = 15.sp, color = OrbitColors.OnAccent))
        }
    }
}
