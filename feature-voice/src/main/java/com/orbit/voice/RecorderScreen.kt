package com.orbit.voice

import android.os.SystemClock
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
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
import com.orbit.core.SvgIcon
import com.orbit.voice.talk.BUBBLE_ICON
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.cos
import kotlin.math.sin

/** Recorder — `design/VoiceRecord.dc.html`. All numbers are design px (1 dp in [com.orbit.core.RoundScreen]). */
@Composable
fun RecorderScreen(
    state: RecorderUi,
    waiting: Int,
    levels: FloatArray,
    onStart: () -> Unit,
    onStop: () -> Unit,
    onNewNote: () -> Unit,
    onInbox: () -> Unit,
    onTalk: () -> Unit,
) {
    Box(Modifier.fillMaxSize()) {
        LevelRing(levels, recording = state is RecorderUi.Recording)
        when (state) {
            is RecorderUi.Idle -> Idle(waiting, null, onStart, onInbox, onTalk)
            is RecorderUi.Error -> Idle(waiting, state.message, onStart, onInbox, onTalk)
            is RecorderUi.Recording -> Recording(state, onStop)
            is RecorderUi.Saved -> Saved(state, onNewNote)
        }
    }
}

/** 60 ticks on the rim; while recording each tick is a recent mic level, newest at 12 o'clock. */
@Composable
private fun LevelRing(levels: FloatArray, recording: Boolean) {
    Canvas(Modifier.fillMaxSize()) {
        val c = size.width / 2
        val unit = size.width / 400f
        val color = if (recording) OrbitColors.Rec else OrbitColors.Track
        for (i in 0 until TICKS) {
            val a = Math.toRadians((i * 6 - 90).toDouble())
            val amp = if (recording) levels[i] else 0f
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
private fun Idle(waiting: Int, error: String?, onStart: () -> Unit, onInbox: () -> Unit, onTalk: () -> Unit) {
    var clock by remember { mutableStateOf(clockNow()) }
    LaunchedEffect(Unit) {
        while (true) {
            clock = clockNow()
            delay(1_000)
        }
    }
    // "Talk" pill at the top (not in the design file): 108 × 40 visual, 64 px-tall touch target.
    Box(
        Modifier
            .offset(146.dp, 22.dp)
            .size(108.dp, 64.dp)
            .clickable(onClick = onTalk)
            .semantics { contentDescription = "Talk" },
        contentAlignment = Alignment.Center,
    ) {
        Row(
            Modifier.size(108.dp, 40.dp).clip(RoundedCornerShape(50)).background(OrbitColors.Surface),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SvgIcon(BUBBLE_ICON, 18.dp, OrbitColors.Info)
            Spacer(Modifier.width(7.dp))
            BasicText("Talk", style = style(14, FontWeight.SemiBold))
        }
    }
    CenterText(clock, top = 92.dp, size = 22, weight = FontWeight.SemiBold, font = OrbitFonts.Grotesk, color = OrbitColors.Muted)
    Box(
        Modifier
            .offset(140.dp, 128.dp)
            .size(120.dp)
            .clip(CircleShape)
            .background(OrbitColors.Accent)
            .clickable(onClick = onStart)
            .semantics { contentDescription = "Start recording" },
        contentAlignment = Alignment.Center,
    ) {
        Image(
            painterResource(R.drawable.ic_mic), null,
            Modifier.size(48.dp),
            colorFilter = ColorFilter.tint(OrbitColors.OnAccent),
        )
    }
    CenterText("Tap to record", top = 270.dp, size = 15, weight = FontWeight.SemiBold)
    if (error != null) {
        CenterText(error, top = 296.dp, size = 13, color = OrbitColors.Rec)
    } else {
        CenterText(waitingText(waiting), top = 296.dp, size = 13, color = OrbitColors.Muted)
    }
    // "N notes waiting" opens the inbox; a 64 px-tall target around the small text.
    Box(Modifier.offset(100.dp, 284.dp).size(200.dp, 64.dp).clickable(onClick = onInbox))
}

@Composable
private fun Recording(state: RecorderUi.Recording, onStop: () -> Unit) {
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
        BasicText("REC", style = style(13, FontWeight.SemiBold, OrbitFonts.Inter, OrbitColors.Rec).copy(letterSpacing = 0.14.em))
    }
    CenterText(
        mmss(now - state.startElapsed), top = 114.dp, size = 72, weight = FontWeight.SemiBold,
        font = OrbitFonts.Grotesk, letterSpacing = (-0.02).em, lineHeight = 72,
    )
    CenterText(state.fileName, top = 196.dp, size = 12, color = OrbitColors.Muted)
    Box(
        Modifier
            .offset(156.dp, 230.dp)
            .size(88.dp)
            .clip(CircleShape)
            .background(OrbitColors.Rec)
            .clickable(onClick = onStop)
            .semantics { contentDescription = "Stop and save" },
        contentAlignment = Alignment.Center,
    ) {
        Box(Modifier.size(28.dp).clip(RoundedCornerShape(6.dp)).background(OrbitColors.OnAccent))
    }
    CenterText("Screen can turn off", top = 332.dp, size = 12, color = OrbitColors.Muted)
}

@Composable
private fun Saved(state: RecorderUi.Saved, onNewNote: () -> Unit) {
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
    CenterText("Saved · ${mmss(state.durationMs)}", top = 160.dp, size = 26, weight = FontWeight.SemiBold, font = OrbitFonts.Grotesk)
    CenterText(state.fileName, top = 198.dp, size = 12, color = OrbitColors.Muted)
    CenterText("Queued · sends on home Wi-Fi", top = 218.dp, size = 13, color = OrbitColors.Accent)
    Box(
        Modifier
            .offset(130.dp, 260.dp)
            .size(140.dp, 52.dp)
            .clip(RoundedCornerShape(50))
            .background(OrbitColors.Surface)
            .clickable(onClick = onNewNote),
        contentAlignment = Alignment.Center,
    ) {
        BasicText("New note", style = style(15, FontWeight.SemiBold))
    }
}

/** A line of text centered across the 400-wide canvas, `top` from the design. */
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
        Modifier.offset(y = top).width(400.dp),
        style = style(size, weight, font, color).copy(
            textAlign = TextAlign.Center,
            letterSpacing = letterSpacing,
            lineHeight = lineHeight?.sp ?: androidx.compose.ui.unit.TextUnit.Unspecified,
        ),
        maxLines = 1,
    )
}

private fun style(size: Int, weight: FontWeight, font: FontFamily = OrbitFonts.Inter, color: Color = OrbitColors.Ink) =
    TextStyle(fontSize = size.sp, fontWeight = weight, fontFamily = font, color = color)

private fun waitingText(n: Int) = when (n) {
    0 -> "All notes sent"
    1 -> "1 note waiting to send"
    else -> "$n notes waiting to send"
}

private fun mmss(ms: Long): String {
    val t = (ms / 1000).coerceAtLeast(0)
    return "%02d:%02d".format(t / 60, t % 60)
}

private fun clockNow() = SimpleDateFormat("HH:mm", Locale.US).format(Date())

const val TICKS = 60
