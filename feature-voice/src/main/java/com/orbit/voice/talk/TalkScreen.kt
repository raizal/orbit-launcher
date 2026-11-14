package com.orbit.voice.talk

import android.os.SystemClock
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.animateScrollBy
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
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
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.orbit.core.OrbitColors
import com.orbit.core.OrbitFonts
import com.orbit.core.RotaryEffect
import com.orbit.core.SvgIcon
import com.orbit.voice.CancelButton
import com.orbit.voice.TICKS
import com.orbit.voice.inbox.INBOX_ICON
import com.orbit.voice.inbox.InboxItem
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
    history: List<TalkTurn>,
    fresh: Boolean,
    playingStem: String?,
    onStart: () -> Unit,
    onStop: () -> Unit,
    onCancel: () -> Unit,
    onSkip: () -> Unit,
    onReplay: (TalkTurn) -> Unit,
    onNew: () -> Unit,
    inbox: TalkInbox = TalkInbox(),
) {
    Box(Modifier.fillMaxSize()) {
        Rim(state, levels)
        when (state) {
            is TalkUi.Listening -> Listening(state, onStop, onCancel)
            is TalkUi.Waiting -> Waiting(state)
            is TalkUi.Speaking -> Reply(state.text, onSkip)
            // Resting states: the conversation, with the new turn in it.
            is TalkUi.Idle -> HistoryView(history, fresh, playingStem, state.note, serverName, onStart, onReplay, onNew, inbox)
            is TalkUi.Done -> HistoryView(history, fresh, playingStem, null, serverName, onStart, onReplay, onNew, inbox)
            is TalkUi.Later -> HistoryView(history, fresh, playingStem, null, serverName, onStart, onReplay, onNew, inbox)
            is TalkUi.Failed -> HistoryView(history, fresh, playingStem, state.message, serverName, onStart, onReplay, onNew, inbox)
        }
    }
}

/**
 * Inbox on the Talk screen (docs/inbox.md): [about] = the item the next turn is about (shown
 * as a card at the bottom of the conversation; ✕ drops it), [unread] for the Inbox pill.
 */
data class TalkInbox(
    val about: InboxItem? = null,
    val unread: Int = 0,
    val onOpen: () -> Unit = {},
    val onClearAbout: () -> Unit = {},
)

/**
 * The conversation, newest at the bottom (reverse layout keeps it anchored there),
 * crown + drag scroll. "New" and "Inbox" on top, the big talk button below.
 */
@Composable
private fun HistoryView(
    turns: List<TalkTurn>,
    fresh: Boolean,
    playingStem: String?,
    note: String?,
    serverName: String?,
    onStart: () -> Unit,
    onReplay: (TalkTurn) -> Unit,
    onNew: () -> Unit,
    inbox: TalkInbox,
) {
    val list = rememberLazyListState()
    val scope = rememberCoroutineScope()
    // Clockwise = later (down). In a reversed list, later is toward index 0.
    RotaryEffect { step -> scope.launch { list.animateScrollBy(-step * 48f) } }
    val newest = turns.lastOrNull()?.stem
    LaunchedEffect(newest, note, inbox.about?.id) { list.scrollToItem(0) }

    val showNew = turns.isNotEmpty() || !fresh
    InboxPill(inbox, x = if (showNew) 200 else 146)
    if (showNew) {
        // Visible 96 × 36; the touch target is 108 × 72 (left of the Inbox pill).
        Box(
            Modifier
                .offset(92.dp, 22.dp)
                .size(108.dp, 72.dp)
                .clickable(onClick = onNew)
                .semantics { contentDescription = "New conversation" },
            contentAlignment = Alignment.Center,
        ) {
            Row(
                Modifier.size(96.dp, 36.dp).clip(RoundedCornerShape(50)).background(OrbitColors.Surface),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                SvgIcon(PLUS_ICON, 14.dp, OrbitColors.Ink)
                Spacer(Modifier.width(6.dp))
                BasicText("New", style = style(13, FontWeight.SemiBold))
            }
        }
    }

    if (turns.isEmpty() && inbox.about == null) {
        Box(
            Modifier.offset(168.dp, 116.dp).size(64.dp).clip(CircleShape).background(OrbitColors.Surface),
            contentAlignment = Alignment.Center,
        ) {
            SvgIcon(BUBBLE_ICON, 30.dp, OrbitColors.Accent)
        }
        val line = when {
            note != null -> note
            serverName != null && fresh -> "New conversation"
            serverName != null -> "Feedback from $serverName"
            else -> "Not paired — saved as notes"
        }
        CenterText(line, top = 198.dp, size = 13, color = if (note != null) OrbitColors.Rec else OrbitColors.Muted)
    } else {
        LazyColumn(
            Modifier
                .offset(60.dp, 94.dp)
                .size(280.dp, 168.dp)
                .fadeEdges(),
            state = list,
            reverseLayout = true,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            inbox.about?.let { item -> item(key = "about") { AboutCard(item, inbox.onClearAbout) } }
            if (note != null) item(key = "note") {
                BasicText(
                    note,
                    Modifier.padding(vertical = 8.dp).width(240.dp),
                    style = style(12, FontWeight.Normal, color = OrbitColors.Muted).copy(textAlign = TextAlign.Center),
                )
            }
            items(turns.asReversed(), key = { it.stem }) { t ->
                TurnItem(t, playing = t.stem == playingStem, onReplay = { onReplay(t) })
            }
        }
    }

    // The big talk button: 176 × 64 visible, 72 tall to touch.
    Box(
        Modifier
            .offset(112.dp, 266.dp)
            .size(176.dp, 72.dp)
            .clickable(onClick = onStart)
            .semantics { contentDescription = "Start talking" },
        contentAlignment = Alignment.Center,
    ) {
        Row(
            Modifier.size(176.dp, 64.dp).clip(RoundedCornerShape(50)).background(OrbitColors.Accent),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SvgIcon(BUBBLE_ICON, 22.dp, OrbitColors.OnAccent)
            Spacer(Modifier.width(8.dp))
            BasicText("Tap to talk", style = style(15, FontWeight.SemiBold, color = OrbitColors.OnAccent))
        }
    }
}

/** "Inbox" (or "2 new"): visible 96 × 36, touch 108 × 72. Opens the inbox list. */
@Composable
private fun InboxPill(inbox: TalkInbox, x: Int) {
    val unread = inbox.unread > 0
    Box(
        Modifier
            .offset(x.dp, 22.dp)
            .size(108.dp, 72.dp)
            .clickable(onClick = inbox.onOpen)
            .semantics { contentDescription = if (unread) "Inbox, ${inbox.unread} new" else "Inbox" },
        contentAlignment = Alignment.Center,
    ) {
        Row(
            Modifier.size(96.dp, 36.dp).clip(RoundedCornerShape(50)).background(OrbitColors.Surface),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SvgIcon(INBOX_ICON, 14.dp, if (unread) OrbitColors.Accent else OrbitColors.Ink)
            Spacer(Modifier.width(6.dp))
            BasicText(
                if (unread) "${inbox.unread} new" else "Inbox",
                style = style(13, FontWeight.SemiBold, color = if (unread) OrbitColors.Accent else OrbitColors.Ink),
            )
        }
    }
}

/** The inbox item the next turn is about: full text, scrolls with the conversation. ✕ = not about it. */
@Composable
private fun AboutCard(item: InboxItem, onClear: () -> Unit) {
    Column(
        Modifier
            .padding(top = 10.dp, bottom = 8.dp)
            .width(264.dp)
            .clip(RoundedCornerShape(22.dp))
            .background(OrbitColors.Surface)
            .padding(start = 16.dp, bottom = 14.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            SvgIcon(INBOX_ICON, 13.dp, OrbitColors.Accent)
            Spacer(Modifier.width(6.dp))
            BasicText(
                "TALKING ABOUT",
                Modifier.weight(1f),
                style = style(11, FontWeight.SemiBold, color = OrbitColors.Accent).copy(letterSpacing = 0.12.em),
            )
            Box(
                Modifier
                    .size(64.dp, 48.dp)
                    .clickable(onClick = onClear)
                    .semantics { contentDescription = "Not about this item" },
                contentAlignment = Alignment.Center,
            ) {
                SvgIcon(CLOSE_ICON, 16.dp, OrbitColors.Muted, strokeWidth = 2.4f)
            }
        }
        BasicText(
            item.title,
            Modifier.padding(end = 16.dp),
            style = style(16, FontWeight.SemiBold, OrbitFonts.Grotesk).copy(lineHeight = 21.sp),
        )
        if (item.body.isNotBlank()) {
            BasicText(
                item.body,
                Modifier.padding(top = 6.dp, end = 16.dp),
                style = style(13, FontWeight.Normal, color = OrbitColors.Muted).copy(lineHeight = 18.sp),
            )
        }
    }
}

/** Your words (2 lines, muted), then the reply with ▶ — or where the reply stands. */
@Composable
private fun TurnItem(t: TalkTurn, playing: Boolean, onReplay: () -> Unit) {
    Column(
        Modifier.width(264.dp).padding(top = 12.dp, bottom = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        BasicText(
            t.transcript ?: "Voice note · ${TalkHistory.clock(t.started_at)}",
            style = style(13, FontWeight.Normal, color = OrbitColors.Muted).copy(textAlign = TextAlign.Center, lineHeight = 18.sp),
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        Spacer(Modifier.size(6.dp))
        when {
            t.hasReply -> {
                BasicText(
                    t.reply!!,
                    style = style(16, FontWeight.Medium).copy(textAlign = TextAlign.Center, lineHeight = 22.sp),
                )
                Box(
                    Modifier
                        .size(72.dp, 56.dp)
                        .clickable(onClick = onReplay)
                        .semantics { contentDescription = if (playing) "Stop reply" else "Play reply" },
                    contentAlignment = Alignment.Center,
                ) {
                    Box(
                        Modifier.size(40.dp).clip(CircleShape).background(if (playing) OrbitColors.Accent else OrbitColors.Surface),
                        contentAlignment = Alignment.Center,
                    ) {
                        SvgIcon(
                            if (playing) STOP_ICON else PLAY_ICON, 18.dp,
                            if (playing) OrbitColors.OnAccent else OrbitColors.Accent,
                        )
                    }
                }
            }
            t.status == STATUS_SAVED -> TurnLine("Saved — feedback when you're home", OrbitColors.Accent)
            t.working -> TurnLine(TalkMachine.statusLabel(TalkUi.Waiting(TalkUi.Phase.THINKING, t.status)), OrbitColors.Info)
            else -> TurnLine("No feedback" + (t.error?.let { " · $it" } ?: ""), OrbitColors.Muted)
        }
        Box(Modifier.padding(top = 6.dp).size(32.dp, 2.dp).clip(RoundedCornerShape(1.dp)).background(OrbitColors.Track))
    }
}

@Composable
private fun TurnLine(text: String, color: Color) {
    BasicText(
        text,
        Modifier.padding(vertical = 4.dp),
        style = style(13, FontWeight.Medium, color = color).copy(textAlign = TextAlign.Center),
        maxLines = 2,
        overflow = TextOverflow.Ellipsis,
    )
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
private fun Listening(state: TalkUi.Listening, onStop: () -> Unit, onCancel: () -> Unit) {
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
    CancelButton(onCancel, "Cancel — discard, send nothing")
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

/** The reply being spoken, inside the inscribed square; rotary scrolls it. */
@Composable
private fun Reply(text: String, onSkip: () -> Unit) {
    val scroll = rememberScrollState()
    val scope = rememberCoroutineScope()
    RotaryEffect { step -> scope.launch { scroll.animateScrollBy(step * 48f) } }
    Row(
        Modifier.offset(y = 46.dp).width(400.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(OrbitColors.Accent))
        Spacer(Modifier.width(7.dp))
        BasicText(
            "SPEAKING",
            style = style(12, FontWeight.SemiBold, OrbitFonts.Inter, OrbitColors.Accent).copy(letterSpacing = 0.14.em),
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
    Pill("Skip", top = 308.dp, primary = false, onClick = onSkip)
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

/** lucide `plus`, `play`, `square`. */
private const val PLUS_ICON = "M5 12h14M12 5v14"
private const val PLAY_ICON = "M7 4l13 8-13 8Z"
private const val STOP_ICON = "M6 6h12v12H6Z"
private const val CLOSE_ICON = "M18 6 6 18M6 6l12 12"

/** lucide `message-circle`. */
const val BUBBLE_ICON = "M7.9 20A9 9 0 1 0 4 16.1L2 22Z"
