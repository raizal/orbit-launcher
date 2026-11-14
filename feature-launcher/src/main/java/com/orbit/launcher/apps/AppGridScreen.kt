package com.orbit.launcher.apps

import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animate
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.orbit.core.OrbitColors
import com.orbit.core.OrbitFonts
import com.orbit.core.RotaryEffect
import com.orbit.core.SvgIcon
import com.orbit.launcher.LauncherEnv
import com.orbit.launcher.LauncherNav
import com.orbit.launcher.Screen
import com.orbit.launcher.apps.Honeycomb.CX
import com.orbit.launcher.apps.Honeycomb.CY
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlin.math.hypot
import kotlin.math.roundToInt

private const val MIC = "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3zM19 10v2a7 7 0 0 1-14 0v-2M12 19v3"
private const val LIST = "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"
private val CELL = Color(0xFF232321)

/** App icon drawn inside its dark disc at this fraction of the disc. */
private const val ICON_FRACTION = 0.84f

/**
 * Honeycomb app grid — design/LauncherHoneycomb.dc.html. Opened by BACK on the face.
 * The center cell is the audio diary. Drag pans (snaps a cell to the center), rotary zooms,
 * tap launches; edge cells (dimmed) are not tappable — tapping one scrolls it to the center.
 * Everything is drawn on one Canvas that reads pan/zoom only in the draw phase, so a drag
 * frame is a redraw, not a recomposition or relayout.
 */
@Composable
fun AppGridScreen(nav: LauncherNav, env: LauncherEnv) {
    val context = LocalContext.current
    LaunchedEffect(Unit) { AppCache.refresh(context) }
    val apps = AppCache.apps.collectAsState().value.orEmpty()
    val count = apps.size + 1 // + the diary in the center
    val grid = remember(count) { Honeycomb.grid(count) }
    val maxPan = remember(count) { Honeycomb.rings(count) + 0.3f }

    // Pan in cell units (so zoom keeps the centered cell), zoom as a factor of the design size.
    var panX by remember { mutableFloatStateOf(0f) }
    var panY by remember { mutableFloatStateOf(0f) }
    var zoom by remember { mutableFloatStateOf(1f) }
    var zoomTarget by remember { mutableFloatStateOf(1f) }
    val scope = rememberCoroutineScope()
    val jobs = remember { arrayOfNulls<Job>(2) } // [pan, zoom] animations
    val currentApps by rememberUpdatedState(apps)
    val haptics = LocalHapticFeedback.current

    fun panTo(tx: Float, ty: Float) {
        jobs[0]?.cancel()
        val sx = panX
        val sy = panY
        jobs[0] = scope.launch {
            animate(0f, 1f, animationSpec = spring(dampingRatio = 0.85f, stiffness = Spring.StiffnessLow)) { f, _ ->
                panX = sx + (tx - sx) * f
                panY = sy + (ty - sy) * f
            }
        }
    }

    fun snapToCell(i: Int) = panTo(-grid[2 * i], -grid[2 * i + 1])

    RotaryEffect { step ->
        // Clockwise = zoom in.
        zoomTarget = (zoomTarget + step * Honeycomb.ZOOM_STEP).coerceIn(Honeycomb.ZOOM_MIN, Honeycomb.ZOOM_MAX)
        jobs[1]?.cancel()
        val target = zoomTarget
        jobs[1] = scope.launch { animate(zoom, target, animationSpec = tween(140)) { v, _ -> zoom = v } }
    }

    val centered by remember(grid) { derivedStateOf { Honeycomb.nearest(grid, -panX, -panY) } }
    val mic = remember { PathParser().parsePathString(MIC).toPath() }
    val micStroke = remember { Stroke(2f, cap = StrokeCap.Round, join = StrokeJoin.Round) }

    Box(Modifier.fillMaxSize()) {
        Canvas(
            Modifier
                .fillMaxSize()
                .pointerInput(grid) {
                    dragOrTap(
                        onStart = { jobs[0]?.cancel() },
                        onDrag = { dx, dy ->
                            val sp = Honeycomb.SPACING * zoom
                            var x = panX + dx / sp
                            var y = panY + dy / sp
                            val len = hypot(x, y)
                            if (len > maxPan) { x *= maxPan / len; y *= maxPan / len }
                            panX = x
                            panY = y
                        },
                        onEnd = { vx, vy ->
                            // Short fling: project ~0.2 s ahead, then settle on the nearest cell.
                            val sp = Honeycomb.SPACING * zoom
                            snapToCell(Honeycomb.nearest(grid, -(panX + vx * 0.2f / sp), -(panY + vy * 0.2f / sp)))
                        },
                        onTap = { tx, ty ->
                            val sp = Honeycomb.SPACING * zoom
                            val i = Honeycomb.hit(grid, tx, ty, panX, panY, sp)
                            when {
                                i < 0 -> Unit
                                !Honeycomb.tappable(Honeycomb.distance(grid, i, panX, panY, sp)) -> snapToCell(i)
                                i == 0 -> nav.openDiary()
                                else -> currentApps.getOrNull(i - 1)?.let { nav.launch(it.intent()) }
                            }
                        },
                        onLongPress = { tx, ty ->
                            // Hold an app icon → its App info page. The diary cell and dimmed edge cells do nothing.
                            val sp = Honeycomb.SPACING * zoom
                            val i = Honeycomb.hit(grid, tx, ty, panX, panY, sp)
                            if (i > 0 && Honeycomb.tappable(Honeycomb.distance(grid, i, panX, panY, sp))) {
                                currentApps.getOrNull(i - 1)?.let {
                                    haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                                    nav.launch(it.infoIntent())
                                }
                            }
                        },
                    )
                },
        ) {
            val u = size.width / 400f
            val sp = Honeycomb.SPACING * zoom
            val px = panX
            val py = panY
            for (i in 0 until count) {
                val x = CX + sp * (grid[2 * i] + px)
                val y = CY + sp * (grid[2 * i + 1] + py)
                val d = hypot(x - CX, y - CY)
                val s = Honeycomb.size(d, zoom)
                if (hypot(x - 200f, y - 200f) - s / 2 > 200f) continue // outside the round screen
                val a = Honeycomb.alpha(d)
                val center = Offset(x * u, y * u)
                if (i == 0) {
                    drawCircle(OrbitColors.Accent, radius = s / 2 * u, center = center, alpha = a)
                    val g = s * 0.44f * u
                    translate(center.x - g / 2, center.y - g / 2) {
                        scale(g / 24f, pivot = Offset.Zero) {
                            drawPath(mic, OrbitColors.OnAccent, alpha = a, style = micStroke)
                        }
                    }
                } else {
                    drawCircle(CELL, radius = s / 2 * u, center = center, alpha = a)
                    val icon = apps[i - 1].icon ?: continue
                    val g = (s * ICON_FRACTION * u).roundToInt()
                    drawImage(
                        icon,
                        srcOffset = IntOffset.Zero,
                        srcSize = IntSize(icon.width, icon.height),
                        dstOffset = IntOffset((center.x - g / 2f).roundToInt(), (center.y - g / 2f).roundToInt()),
                        dstSize = IntSize(g, g),
                        alpha = a,
                        filterQuality = FilterQuality.Low,
                    )
                }
            }
        }

        // Name of the centered cell (the design's "VOICE NOTE" caption).
        val label = if (centered == 0) "VOICE NOTE" else apps.getOrNull(centered - 1)?.label?.uppercase().orEmpty()
        BasicText(
            label,
            Modifier.offset(100.dp, 366.dp).width(200.dp),
            style = TextStyle(
                fontFamily = OrbitFonts.Inter, fontWeight = FontWeight.SemiBold, fontSize = 12.sp,
                letterSpacing = 0.1.em, color = OrbitColors.Muted, textAlign = TextAlign.Center,
            ),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )

        // Not in the design: a small "list" button on the top rim → A–Z list (Screen.APP_LIST).
        Box(
            Modifier.offset(168.dp, 0.dp).size(64.dp, 44.dp).clickable { nav.go(Screen.APP_LIST) },
            contentAlignment = Alignment.Center,
        ) {
            Box(
                Modifier.size(28.dp).clip(CircleShape).background(OrbitColors.Surface),
                contentAlignment = Alignment.Center,
            ) {
                SvgIcon(LIST, 14.dp, OrbitColors.Muted, strokeWidth = 2.4f)
            }
        }
    }
}
