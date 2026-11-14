package com.orbit.voice.talk

import android.os.SystemClock
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.animateScrollBy
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.orbit.core.OrbitColors
import com.orbit.core.OrbitFonts
import com.orbit.core.RotaryEffect
import com.orbit.core.SvgIcon
import com.orbit.voice.R
import com.orbit.voice.TICKS
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.cos
import kotlin.math.sin

/**
 * Talk screen — same visual language as `design/VoiceRecord.dc.html` (400-unit grid,
 * rim of 60 ticks, Orbit colors/fonts). The rim says what is happening:
 * red levels = listening, cyan sweep = sending/thinking, accent pulse = speaking.
 */
@Composable
fun TalkScreen(
    state: TalkUi,
    levels: FloatArray,
    serverName: String?,
    onStart: () -> Unit,
    onStop: () -> Unit,
    onSkip: () -> Unit,
    onAgain: () -> Unit,
) {
    Box(Modifier.fillMaxSize()) {
        Rim(state, levels)
        when (state) {
            is TalkUi.Idle -> Idle(state, serverName, onStart)
            is TalkUi.Listening -> Listening(state, onStop)
            is TalkUi.Waiting -> Waiting(state)
            is TalkUi.Speaking -> Reply(state.text, speaking = true, onSkip)
            is TalkUi.Done -> Reply(state.text, speaking = false, onAgain)
            is TalkUi.Later -> Ended(
                title = "Saved", line = "Feedback when you're home", lineColor = OrbitColors.Accent,
                detail = state.reason, onAgain = onAgain,
            )
            is TalkUi.Failed -> Ended(
                title = "No feedback", line = "Your note is saved", lineColor = OrbitColors.Muted,
                detail = state.message, onAgain = onAgain,
            )
        }
    }
}

@Composable
private fun Rim(state: TalkUi, levels: FloatArray) {
    val t = rememberInfiniteTransition(label = "rim")
    // One sweep per 1.6 s while waiting; a slow breath while speaking.
    val sweep by t.animateFloat(0f, 1f, infiniteRepeatable(tween(1_600, easing = LinearEasing)), label = "sweep")
    val breath by t.animateFloat(0f, 1f, infiniteRepeatable(tween(700), RepeatMode.Reverse), label = "breath")
    Canvas(Modifier.fillMaxSize()) {
        val c = size.width / 2
        val unit = size.width / 400f
        for (i in 0 until TICKS) {
            val a = Math.toRadians((i * 6 - 90).toDouble())
            var color: Color = OrbitColors.Track
            var amp = 0f
            when (state) {
                is TalkUi.Listening -> { color = OrbitColors.Rec; amp = levels[i] }
                is TalkUi.Waiting -> {
                    // A comet: bright head, fading tail, moving clockwise.
                    val head = sweep * TICKS
                    val behind = ((head - i) % TICKS + TICKS) % TICKS
                    val k = (1f - behind / 14f).coerceIn(0f, 1f)
                    color = if (k > 0f) OrbitColors.Info.copy(alpha = 0.25f + 0.75f * k) else OrbitColors.Track
                    amp = 0.35f * k
                }
                is TalkUi.Speaking -> {
                    color = OrbitColors.Accent
                    amp = 0.15f + 0.35f * breath * (0.6f + 0.4f * sin(i * 1.7f))
                }
                is TalkUi.Done -> color = OrbitColors.Accent.copy(alpha = 0.35f)
                else -> Unit
            }
            val r1 = 170f * unit
            val r2 = (174f + amp * 16f) * unit
            drawLine(
                color,
                Offset(c + r1 * cos(a).toFloat(), c + r1 * sin(a).toFloat()),
                Offset(c + r2 * cos(a).toFloat(), c + r2 * sin(a).toFloat()),
                strokeWidth = 3f * unit,
                cap = StrokeCap.Round,
            )
        }
    }
}

@Composable
private fun Idle(state: TalkUi.Idle, serverName: String?, onStart: () -> Unit) {
    Label("TALK", top = 84.dp, color = OrbitColors.Muted)
    Box(
        Modifier
            .offset(140.dp, 128.dp)
            .size(120.dp)
            .clip(CircleShape)
            .background(OrbitColors.Accent)
            .clickable(onClick = onStart)
            .semantics { contentDescription = "Start talking" },
        contentAlignment = Alignment.Center,
    ) {
        SvgIcon(BUBBLE_ICON, 48.dp, OrbitColors.OnAccent)
    }
    CenterText("Tap to talk", top = 270.dp, size = 15, weight = FontWeight.SemiBold)
    when {
        state.note != null -> CenterText(state.note, top = 296.dp, size = 13, color = OrbitColors.Rec)
        serverName != null -> CenterText("Feedback from $serverName", top = 296.dp, size = 13, color = OrbitColors.Muted)
        else -> CenterText("Not paired — saved as notes", top = 296.dp, size = 13, color = OrbitColors.Muted)
    }
}

@Composable
private fun Listening(state: TalkUi.Listening, onStop: () -> Unit) {
    var now by remember { mutableLongStateOf(SystemClock.elapsedRealtime()) }
    LaunchedEffect(state.startElapsed) {
        while (true) {
            now = SystemClock.elapsedRealtime()
            delay(200)
        }
    }
    Row(
        Modifier.offset(y = 92.dp).width(400.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(10.dp).clip(CircleShape).background(OrbitColors.Rec))
        Spacer(Modifier.width(8.dp))
        BasicText("LISTENING", style = style(13, FontWeight.SemiBold, OrbitFonts.Inter, OrbitColors.Rec).copy(letterSpacing = 0.14.em))
    }
    CenterText(
        mmss(now - state.startElapsed), top = 114.dp, size = 72, weight = FontWeight.SemiBold,
        font = OrbitFonts.Grotesk, letterSpacing = (-0.02).em, lineHeight = 72,
    )
    Box(
        Modifier
            .offset(156.dp, 230.dp)
            .size(88.dp)
            .clip(CircleShape)
            .background(OrbitColors.Rec)
            .clickable(onClick = onStop)
            .semantics { contentDescription = "Stop and send" },
        contentAlignment = Alignment.Center,
    ) {
        Box(Modifier.size(28.dp).clip(RoundedCornerShape(6.dp)).background(OrbitColors.OnAccent))
    }
    CenterText("Tap to stop", top = 332.dp, size = 12, color = OrbitColors.Muted)
}

@Composable
private fun Waiting(state: TalkUi.Waiting) {
    Box(
        Modifier.offset(168.dp, 104.dp).size(64.dp).clip(CircleShape).background(OrbitColors.Surface),
        contentAlignment = Alignment.Center,
    ) {
        SvgIcon(BUBBLE_ICON, 30.dp, OrbitColors.Info)
    }
    CenterText(TalkMachine.statusLabel(state), top = 188.dp, size = 26, weight = FontWeight.SemiBold, font = OrbitFonts.Grotesk, color = OrbitColors.Info)
    CenterText(
        if (state.phase == TalkUi.Phase.SENDING) "Saved · sending to home" else "Your note is saved",
        top = 228.dp, size = 13, color = OrbitColors.Muted,
    )
}

/** Reply text inside the inscribed square; rotary scrolls it. */
@Composable
private fun Reply(text: String, speaking: Boolean, onButton: () -> Unit) {
    val scroll = rememberScrollState()
    val scope = rememberCoroutineScope()
    RotaryEffect { step -> scope.launch { scroll.animateScrollBy(step * 48f) } }
    Row(
        Modifier.offset(y = 46.dp).width(400.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (speaking) {
            Box(Modifier.size(8.dp).clip(CircleShape).background(OrbitColors.Accent))
            Spacer(Modifier.width(7.dp))
        }
        BasicText(
            if (speaking) "SPEAKING" else "REPLY",
            style = style(12, FontWeight.SemiBold, OrbitFonts.Inter, if (speaking) OrbitColors.Accent else OrbitColors.Muted)
                .copy(letterSpacing = 0.14.em),
        )
    }
    Box(
        Modifier
            .offset(68.dp, 74.dp)
            .size(264.dp, 222.dp)
            .fadeEdges()
            .verticalScroll(scroll),
    ) {
        BasicText(
            text,
            Modifier.padding(vertical = 4.dp),
            style = style(18, FontWeight.Medium).copy(textAlign = TextAlign.Center, lineHeight = 25.sp),
        )
    }
    Pill(if (speaking) "Skip" else "Talk again", top = 308.dp, primary = !speaking, onClick = onButton)
}

@Composable
private fun Ended(title: String, line: String, lineColor: Color, detail: String, onAgain: () -> Unit) {
    Box(
        Modifier.offset(168.dp, 84.dp).size(64.dp).clip(CircleShape).background(OrbitColors.Surface),
        contentAlignment = Alignment.Center,
    ) {
        Image(
            painterResource(R.drawable.ic_check), null,
            Modifier.size(30.dp),
            colorFilter = ColorFilter.tint(OrbitColors.Accent),
        )
    }
    CenterText(title, top = 160.dp, size = 26, weight = FontWeight.SemiBold, font = OrbitFonts.Grotesk)
    CenterText(line, top = 198.dp, size = 13, color = lineColor)
    CenterText(detail, top = 220.dp, size = 12, color = OrbitColors.Muted)
    Pill("Talk again", top = 260.dp, primary = false, onClick = onAgain)
}

@Composable
private fun Pill(label: String, top: Dp, primary: Boolean, onClick: () -> Unit) {
    // 140 × 52 like the recorder's "New note"; the touch target is the full 64+ px height.
    Box(
        Modifier
            .offset(130.dp, top)
            .size(140.dp, 52.dp)
            .clip(RoundedCornerShape(50))
            .background(if (primary) OrbitColors.Accent else OrbitColors.Surface)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        BasicText(label, style = style(15, FontWeight.SemiBold, color = if (primary) OrbitColors.OnAccent else OrbitColors.Ink))
    }
}

@Composable
private fun Label(text: String, top: Dp, color: Color) {
    BasicText(
        text,
        Modifier.offset(y = top).width(400.dp),
        style = style(13, FontWeight.SemiBold, OrbitFonts.Inter, color).copy(textAlign = TextAlign.Center, letterSpacing = 0.14.em),
    )
}

@Composable
private fun CenterText(
    text: String,
    top: Dp,
    size: Int,
    weight: FontWeight = FontWeight.Normal,
    font: FontFamily = OrbitFonts.Inter,
    color: Color = OrbitColors.Ink,
    letterSpacing: androidx.compose.ui.unit.TextUnit = androidx.compose.ui.unit.TextUnit.Unspecified,
    lineHeight: Int? = null,
) {
    BasicText(
        text,
        Modifier.offset(y = top).padding(horizontal = 60.dp).width(280.dp),
        style = style(size, weight, font, color).copy(
            textAlign = TextAlign.Center,
            letterSpacing = letterSpacing,
            lineHeight = lineHeight?.sp ?: androidx.compose.ui.unit.TextUnit.Unspecified,
        ),
        maxLines = 1,
    )
}

/** Fades the top and bottom 20 px so scrolled text doesn't end in a hard cut. */
private fun Modifier.fadeEdges() = graphicsLayer { compositingStrategy = CompositingStrategy.Offscreen }
    .drawWithContent {
        drawContent()
        val f = 20.dp.toPx() / size.height
        drawRect(
            Brush.verticalGradient(0f to Color.Transparent, f to Color.Black, 1f - f to Color.Black, 1f to Color.Transparent),
            blendMode = BlendMode.DstIn,
        )
    }

private fun style(size: Int, weight: FontWeight, font: FontFamily = OrbitFonts.Inter, color: Color = OrbitColors.Ink) =
    TextStyle(fontSize = size.sp, fontWeight = weight, fontFamily = font, color = color)

private fun mmss(ms: Long): String {
    val t = (ms / 1000).coerceAtLeast(0)
    return "%02d:%02d".format(t / 60, t % 60)
}

/** lucide `message-circle`. */
const val BUBBLE_ICON = "M7.9 20A9 9 0 1 0 4 16.1L2 22Z"
