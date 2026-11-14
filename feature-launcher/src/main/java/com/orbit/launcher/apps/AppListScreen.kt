package com.orbit.launcher.apps

import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animate
import androidx.compose.animation.core.spring
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.MutableFloatState
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.lerp
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
import androidx.compose.ui.unit.sp
import com.orbit.core.CurvedText
import com.orbit.core.OrbitColors
import com.orbit.core.OrbitFonts
import com.orbit.core.RotaryEffect
import com.orbit.launcher.LauncherEnv
import com.orbit.launcher.LauncherNav
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlin.math.abs
import kotlin.math.roundToInt

private val ICON_BG = Color(0xFF232321)
private val TRACK = Color(0xFF262624)

/** Rim scrollbar: arc r 190 around the center, from (378.5, 135) to (378.5, 265) = −20°…+20°. */
private const val BAR_R = 190f
private const val BAR_START_DEG = -20f
private const val BAR_SWEEP_DEG = 40f

/** Text box width at the focused size (352 − text left of the focused row); other rows are scaled. */
private const val TEXT_W = 234f

/**
 * Curved A–Z app list — design/LauncherList.dc.html. Rotary and drag scroll (continuous, then
 * settle on a row), the center row is larger, tap launches. Rows are laid out once at the
 * focused size and moved/scaled with graphicsLayer + drawBehind, so scrolling never relayouts.
 */
@Composable
fun AppListScreen(nav: LauncherNav, env: LauncherEnv) {
    val context = LocalContext.current
    LaunchedEffect(Unit) { AppCache.refresh(context) }
    val apps = AppCache.apps.collectAsState().value
    val list = apps.orEmpty()
    val last = (list.size - 1).coerceAtLeast(0)
    val currentApps by rememberUpdatedState(list)
    val currentLast by rememberUpdatedState(last)
    val haptics = LocalHapticFeedback.current


    val pos = remember { mutableFloatStateOf(AppCache.listPos.coerceIn(0f, last.toFloat())) }

    /** The visible row under design-y [ty], if any. */
    fun rowAt(ty: Float): AppEntry? {
        val p = pos.floatValue
        val b = p.roundToInt()
        var hit = -1
        var best = 34f
        for (i in b - 2..b + 2) {
            if (i !in currentApps.indices) continue
            val d = abs(CurvedList.y(i - p) - ty)
            if (d < best) { best = d; hit = i }
        }
        return currentApps.getOrNull(hit)
    }
    DisposableEffect(Unit) { onDispose { AppCache.listPos = pos.floatValue } }
    val scope = rememberCoroutineScope()
    val job = remember { arrayOfNulls<Job>(1) }
    val target = remember { floatArrayOf(pos.floatValue) }

    fun settle(to: Float) {
        val t = to.roundToInt().coerceIn(0, currentLast).toFloat()
        target[0] = t
        job[0]?.cancel()
        val from = pos.floatValue
        job[0] = scope.launch {
            animate(from, t, animationSpec = spring(dampingRatio = 0.9f, stiffness = Spring.StiffnessMediumLow)) { v, _ ->
                pos.floatValue = v
            }
        }
    }

    if (pos.floatValue > last) pos.floatValue = last.toFloat()
    RotaryEffect { step -> settle(target[0] + step) }

    val base by remember { derivedStateOf { pos.floatValue.roundToInt() } }

    Box(
        Modifier.fillMaxSize().pointerInput(Unit) {
            dragOrTap(
                onStart = { job[0]?.cancel() },
                onDrag = { _, dy ->
                    pos.floatValue = (pos.floatValue - dy / CurvedList.ROW_PX).coerceIn(-0.3f, currentLast + 0.3f)
                },
                onEnd = { _, vy -> settle(pos.floatValue - vy * 0.2f / CurvedList.ROW_PX) },
                onTap = { _, ty -> rowAt(ty)?.let { nav.launch(it.intent()) } },
                onLongPress = { _, ty ->
                    // Hold a row → that app's App info page.
                    rowAt(ty)?.let {
                        haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                        nav.launch(it.infoIntent())
                    }
                },
            )
        },
    ) {
        CurvedText("APPS · A–Z", top = true, color = OrbitColors.Muted, sizeSp = 13f, letterSpacingPx = 2.5f)

        when {
            apps == null -> Hint("Loading…")
            list.isEmpty() -> Hint("No apps")
        }

        for (i in base - 3..base + 3) {
            val app = list.getOrNull(i) ?: continue
            key(app.component) { AppRow(app, i, pos, focused = i == base) }
        }

        // Rim scrollbar (track + thumb); reads the position only while drawing.
        val n = list.size
        Box(
            Modifier.fillMaxSize().drawWithCache {
                val u = size.width / 400f
                val r = BAR_R * u
                val topLeft = Offset(size.width / 2 - r, size.height / 2 - r)
                val arc = Size(2 * r, 2 * r)
                val stroke = Stroke(5f * u, cap = StrokeCap.Round)
                onDrawBehind {
                    drawArc(TRACK, BAR_START_DEG, BAR_SWEEP_DEG, false, topLeft, arc, style = stroke)
                    val len = CurvedList.thumbLength(n)
                    val start = CurvedList.thumbStart(pos.floatValue, n)
                    drawArc(OrbitColors.Ink, BAR_START_DEG + start * BAR_SWEEP_DEG, len * BAR_SWEEP_DEG, false, topLeft, arc, style = stroke)
                }
            },
        )
    }
}

@Composable
private fun Hint(text: String) {
    BasicText(
        text,
        Modifier.offset(y = 190.dp).width(400.dp),
        style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 15.sp, color = OrbitColors.Muted, textAlign = TextAlign.Center),
    )
}

/**
 * One row, laid out at the focused size (400 × 72, text at 20 sp) and placed / scaled per
 * frame from `s = index − pos`. Pill + icon are drawn in drawBehind; the label is a scaled layer.
 */
@Composable
private fun AppRow(app: AppEntry, index: Int, pos: MutableFloatState, focused: Boolean) {
    Box(
        Modifier
            .size(400.dp, CurvedList.FOCUS_H.dp)
            .graphicsLayer {
                val s = index - pos.floatValue
                translationY = (CurvedList.y(s) - CurvedList.FOCUS_H / 2) * density
                alpha = CurvedList.alpha(s)
            }
            .drawBehind {
                val s = index - pos.floatValue
                val u = size.height / CurvedList.FOCUS_H
                val mid = size.height / 2
                val f = CurvedList.focus(s)
                val left = CurvedList.left(s)
                if (f > 0f) {
                    val h = CurvedList.height(s) * u
                    drawRoundRect(
                        OrbitColors.Surface,
                        topLeft = Offset(left * u, mid - h / 2),
                        size = Size((CurvedList.RIGHT - left) * u, h),
                        cornerRadius = CornerRadius(h / 2),
                        alpha = f,
                    )
                }
                val icon = CurvedList.icon(s) * u
                val cx = CurvedList.iconLeft(s) * u + icon / 2
                // Focused: amber disc behind the icon (the design's accent icon); others dark.
                drawCircle(lerp(ICON_BG, OrbitColors.Accent, f), radius = icon / 2, center = Offset(cx, mid))
                val bmp = app.icon ?: return@drawBehind
                val g = (icon * 0.84f).roundToInt()
                drawImage(
                    bmp,
                    srcOffset = IntOffset.Zero,
                    srcSize = IntSize(bmp.width, bmp.height),
                    dstOffset = IntOffset((cx - g / 2f).roundToInt(), (mid - g / 2f).roundToInt()),
                    dstSize = IntSize(g, g),
                    filterQuality = FilterQuality.Low,
                )
            },
        contentAlignment = Alignment.CenterStart,
    ) {
        BasicText(
            app.label,
            Modifier
                .width(TEXT_W.dp)
                .graphicsLayer {
                    val s = index - pos.floatValue
                    transformOrigin = TransformOrigin(0f, 0.5f)
                    translationX = CurvedList.textLeft(s) * density
                    val k = CurvedList.fontSize(s) / CurvedList.FOCUS_FONT
                    scaleX = k
                    scaleY = k
                },
            style = TextStyle(
                fontFamily = OrbitFonts.Inter,
                fontWeight = if (focused) FontWeight.SemiBold else FontWeight.Medium,
                fontSize = CurvedList.FOCUS_FONT.sp,
                color = OrbitColors.Ink,
            ),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}
