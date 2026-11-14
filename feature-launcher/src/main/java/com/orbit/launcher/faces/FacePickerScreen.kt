package com.orbit.launcher.faces

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
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
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.orbit.core.OrbitColors
import com.orbit.core.OrbitFonts
import com.orbit.core.RotaryEffect
import com.orbit.core.SvgIcon
import com.orbit.core.CurvedText
import com.orbit.launcher.LauncherEnv
import com.orbit.launcher.LauncherNav
import com.orbit.launcher.Screen
import com.orbit.launcher.face.Face
import com.orbit.launcher.face.FaceColors
import com.orbit.launcher.face.FaceEntry
import com.orbit.launcher.face.FaceRepo
import com.orbit.launcher.face.FaceView
import kotlinx.coroutines.launch
import kotlin.math.hypot

private const val SLIDERS = "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"
private val RING = Color(0xFF262624)
private val DOT = Color(0xFF4A4A46)

/** Face shown for an entry: the face itself, or the error face for a broken file. */
internal fun previewFace(entry: FaceEntry): Face = when (entry) {
    is FaceEntry.Ok -> entry.face
    is FaceEntry.Broken -> FaceRepo.errorFace(entry)
}

/**
 * Face picker (design/FacePicker): a carousel of live mini faces. Rotary / horizontal
 * drag / tapping a side preview steps between faces; tapping the centered face makes
 * it the watch face; "Customize" opens [FaceCustomizeScreen] for it.
 */
@Composable
fun FacePickerScreen(nav: LauncherNav, env: LauncherEnv) {
    val faces by env.faces.faces.collectAsState()
    val data by env.data.collectAsState()
    if (faces.isEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            BasicText("No faces", style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 15.sp, color = OrbitColors.Muted))
        }
        return
    }
    val count = faces.size
    val scope = rememberCoroutineScope()
    var index by remember { mutableIntStateOf(faces.indexOfFirst { it.id == env.prefs.faceId }.coerceAtLeast(0)) }
    if (index > count - 1) index = count - 1
    val pos = remember { Animatable(index.toFloat()) }
    var drag by remember { mutableFloatStateOf(0f) } // in items, while a finger is down

    val currentCount by rememberUpdatedState(count)
    val latestFaces by rememberUpdatedState(faces)
    fun settle(target: Int) {
        index = target.coerceIn(0, currentCount - 1)
        scope.launch { pos.animateTo(index.toFloat(), tween(220)) }
    }

    RotaryEffect { settle(index + it) }

    val entry = faces[index]
    val ok = entry as? FaceEntry.Ok
    val accent = previewFace(entry).let { f -> FaceColors(f.theme + accentPair(env.prefs.accent(f.id)))["accent"] }
    val customizable = ok != null && (complicationSlots(ok.face).isNotEmpty() || !ok.face.tweaks["accent"].isNullOrEmpty())

    fun select() {
        val chosen = latestFaces.getOrNull(index) as? FaceEntry.Ok ?: return // broken face: not selectable
        env.prefs.faceId = chosen.id
        nav.go(Screen.FACE)
    }

    Box(Modifier.fillMaxSize()) {
        CurvedText(
            if (ok != null) "${ok.face.name.uppercase()} · ${index + 1} / $count" else "BROKEN · ${index + 1} / $count",
            top = true, color = if (ok != null) OrbitColors.Muted else OrbitColors.Rec, sizeSp = 14f, letterSpacingPx = 2.5f,
        )

        // Previews: each face laid out at its full 400 dp and scaled down (complications use absolute offsets).
        for (i in (index - 2)..(index + 2)) {
            val e = faces.getOrNull(i) ?: continue
            key(e.id) {
                val f = previewFace(e)
                Box(
                    Modifier
                        .size(400.dp)
                        .graphicsLayer {
                            val p = pickerItem(i - pos.value - drag)
                            val unit = size.width / 400f
                            translationX = (p.cx - 200f) * unit
                            translationY = (p.cy - 200f) * unit
                            scaleX = (p.size - 4f) / 400f
                            scaleY = scaleX
                            alpha = p.alpha
                            shape = CircleShape
                            clip = true
                        },
                ) {
                    FaceView(
                        face = f,
                        data = data,
                        accentOverride = env.prefs.accent(f.id),
                        slotType = { slot, default -> env.prefs.slot(f.id, slot) ?: default },
                        onComplication = {},
                    )
                }
            }
        }

        // Rings: accent around the centered face, dim around neighbors (draw phase only).
        Canvas(Modifier.fillMaxSize()) {
            val unit = size.width / 400f
            for (i in (index - 2)..(index + 2)) {
                if (i !in 0 until count) continue
                val p = pickerItem(i - pos.value - drag)
                if (p.alpha <= 0f) continue
                val w = (2f + p.focus) * unit
                drawCircle(
                    color = lerp(RING, accent, p.focus),
                    radius = p.size / 2f * unit - w / 2f,
                    center = Offset(p.cx * unit, p.cy * unit),
                    style = Stroke(w),
                    alpha = p.alpha,
                )
            }
        }

        // Touch layer above the previews, so taps never reach the faces' complications.
        Box(
            Modifier
                .fillMaxSize()
                .pointerInput(Unit) {
                    detectTapGestures { p ->
                        val x = p.x / density
                        val y = p.y / density
                        when {
                            hypot(x - 200f, y - 184f) <= 110f -> select()
                            x < 90f -> settle(index - 1)
                            x > 310f -> settle(index + 1)
                        }
                    }
                }
                .pointerInput(Unit) {
                    var start = 0
                    detectHorizontalDragGestures(
                        onDragStart = { start = index; scope.launch { pos.stop() } },
                        onDragEnd = {
                            val now = pos.value + drag
                            val target = snapIndex(start, now, currentCount)
                            scope.launch { pos.snapTo(now); drag = 0f; settle(target) }
                        },
                        onDragCancel = { drag = 0f; settle(start) },
                    ) { change, dx ->
                        change.consume()
                        val next = drag - dx / density / PICKER_SPACING
                        val base = pos.value
                        // Rubber band at the ends, at most ~1.2 items per drag.
                        drag = (base + next).coerceIn(-0.3f, currentCount - 0.7f).let { it - base }.coerceIn(-1.2f, 1.2f)
                    }
                },
        )

        if (customizable) {
            // Visual pill 150 × 48 (design), tap area 170 × 64.
            Box(
                Modifier
                    .offset(115.dp, 304.dp)
                    .size(170.dp, 64.dp)
                    .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) {
                        nav.editingFaceId = entry.id
                        nav.go(Screen.CUSTOMIZE)
                    },
                contentAlignment = Alignment.Center,
            ) {
                Row(
                    Modifier.size(150.dp, 48.dp).clip(RoundedCornerShape(24.dp)).background(OrbitColors.Surface),
                    horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    SvgIcon(SLIDERS, 16.dp, OrbitColors.Ink, strokeWidth = 2.2f)
                    Spacer(Modifier.width(8.dp))
                    BasicText(
                        "Customize",
                        style = TextStyle(fontFamily = OrbitFonts.Inter, fontWeight = FontWeight.SemiBold, fontSize = 15.sp, color = OrbitColors.Ink),
                    )
                }
            }
        } else if (ok == null) {
            BasicText(
                "Can't load this face",
                Modifier.offset(0.dp, 324.dp).width(400.dp),
                style = TextStyle(
                    fontFamily = OrbitFonts.Inter, fontSize = 14.sp, color = OrbitColors.Muted,
                    textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                ),
            )
        }

        // Page dots: the current face is a 16 px accent pill.
        Row(
            Modifier.offset(0.dp, 372.dp).width(400.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp, Alignment.CenterHorizontally),
        ) {
            for (i in 0 until count) {
                Box(
                    Modifier
                        .size(if (i == index) 16.dp else 6.dp, 6.dp)
                        .clip(RoundedCornerShape(3.dp))
                        .background(if (i == index) accent else DOT),
                )
            }
        }
    }
}

internal fun accentPair(accent: String?): Map<String, String> =
    if (accent != null) mapOf("accent" to accent) else emptyMap()
