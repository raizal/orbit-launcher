package com.orbit.voice.inbox

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectVerticalDragGestures
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.lifecycleScope
import com.orbit.core.CurvedText
import com.orbit.core.OrbitColors
import com.orbit.core.OrbitFonts
import com.orbit.core.R as CoreR
import com.orbit.core.RotaryEffect
import com.orbit.core.RoundScreen
import com.orbit.core.SvgIcon
import com.orbit.voice.talk.TalkActivity
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.abs
import kotlin.math.sqrt

/**
 * Inbox — what the companion sent (docs/inbox.md). Newest first, unread marked, rotary or
 * drag to move, tap the centered item to talk about it (it becomes read). Reached from the
 * Talk screen's Inbox pill; each new item is also a notification (launcher panel) that opens
 * Talk about it directly.
 */
class InboxListActivity : ComponentActivity() {
    private var poll: Job? = null
    private var checking by mutableStateOf(false)
    private var reachable by mutableStateOf(true)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true) // like Talk, which this opens
        InboxSync.load(this)
        setContent {
            val state by InboxSync.state.collectAsState()
            RoundScreen {
                InboxListScreen(state, checking, reachable, onOpen = ::open)
            }
        }
    }

    override fun onStart() {
        super.onStart()
        // While open: check every ~10 s (stops with the screen).
        poll = lifecycleScope.launch {
            while (true) {
                checking = true
                reachable = InboxSync.sync(this@InboxListActivity)
                checking = false
                delay(POLL_MS)
            }
        }
    }

    override fun onStop() {
        poll?.cancel()
        super.onStop()
    }

    private fun open(item: InboxItem) {
        startActivity(TalkActivity.aboutIntent(this, item.id))
        finish()
    }

    private companion object {
        const val POLL_MS = 10_000L
    }
}

/** lucide `check`, `circle-help`, `circle-alert`, `bell`, `inbox`. */
/** The web UI's `--ok`. */
private val OK_GREEN = Color(0xFFB9F26B)
private const val DONE = "M20 6 9 17l-5-5"
private const val ASK = "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3M12 17h.01"
private const val ALERT = "M12 8v5M12 17h.01M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z"
private const val BELL = "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0"
const val INBOX_ICON = "M22 12h-6l-2 3h-4l-2-3H2M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"

/** Row centers like the notes list (`design/VoiceInbox.dc.html`); index 2 is the focused row. */
private val ROW_Y = intArrayOf(74, 134, 200, 266, 326)

@Composable
fun InboxListScreen(state: InboxState, checking: Boolean, reachable: Boolean, onOpen: (InboxItem) -> Unit) {
    val items = state.items
    var focus by remember { mutableIntStateOf(0) }
    var drag by remember { mutableFloatStateOf(0f) }
    val last = (items.size - 1).coerceAtLeast(0)
    if (focus > last) focus = last
    RotaryEffect { focus = (focus + it).coerceIn(0, last) }
    val now = System.currentTimeMillis()
    val unread = state.unreadCount

    Box(
        Modifier.fillMaxSize().pointerInput(last) {
            detectVerticalDragGestures(onDragEnd = { drag = 0f }) { _, dy ->
                drag += dy
                while (drag <= -60f) { focus = (focus + 1).coerceAtMost(last); drag += 60f }
                while (drag >= 60f) { focus = (focus - 1).coerceAtLeast(0); drag -= 60f }
            }
        },
    ) {
        CurvedText(
            if (unread == 0) "INBOX" else "INBOX · $unread NEW",
            top = true, color = if (unread == 0) OrbitColors.Muted else OrbitColors.Accent, sizeSp = 13f, letterSpacingPx = 2.5f,
        )
        val bottom = when {
            checking && state.lastCheckMs == 0L -> "CHECKING…"
            !reachable -> "AWAY FROM HOME · CACHED"
            items.isNotEmpty() -> "TAP TO TALK ABOUT IT"
            else -> "CHECKED ${InboxLogic.age(java.time.Instant.ofEpochMilli(state.lastCheckMs).toString(), now).uppercase()}"
                .replace("CHECKED JUST NOW", "UP TO DATE")
        }
        CurvedText(bottom, top = false, color = OrbitColors.Muted, sizeSp = 12f, letterSpacingPx = 1.5f, fontRes = CoreR.font.inter_medium)
        if (items.isEmpty()) {
            Box(
                Modifier.offset(168.dp, 132.dp).size(64.dp).clip(CircleShape).background(OrbitColors.Surface),
                contentAlignment = Alignment.Center,
            ) { SvgIcon(INBOX_ICON, 28.dp, OrbitColors.Muted) }
            BasicText(
                "Nothing yet",
                Modifier.offset(y = 212.dp).width(400.dp),
                style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 15.sp, color = OrbitColors.Muted, textAlign = TextAlign.Center),
            )
        }
        for (slot in ROW_Y.indices) {
            val index = focus + slot - 2
            val item = items.getOrNull(index) ?: continue
            ItemRow(item, slot, now) { if (slot == 2) onOpen(item) else focus = index }
        }
    }
}

@Composable
private fun ItemRow(item: InboxItem, slot: Int, now: Long, onClick: () -> Unit) {
    val dy = ROW_Y[slot] - 200
    val center = dy == 0
    val icon = if (center) 48 else if (abs(dy) < 100) 42 else 36
    val h = if (center) 64 else icon
    val edge = 200 - sqrt((178f * 178f - dy * dy))
    val left = (edge + if (center) 18 else 30).toInt()
    val (path, fg) = when (item.kind) {
        "thread_done" -> DONE to OK_GREEN
        "thread_input" -> ASK to OrbitColors.Accent
        "thread_error" -> ALERT to OrbitColors.Rec
        else -> BELL to OrbitColors.Info
    }
    // Rows are ≥ 64 px to touch: the visible pill is smaller off-center, the target is not.
    Box(
        Modifier
            .offset(left.dp, (ROW_Y[slot] - 32).dp)
            .size((352 - left).dp, 64.dp)
            .clickable(onClick = onClick)
            .semantics { contentDescription = if (center) "Talk about: ${item.title}" else item.title },
        contentAlignment = Alignment.CenterStart,
    ) {
        Row(
            Modifier
                .size((352 - left).dp, h.dp)
                .clip(RoundedCornerShape(50))
                .background(if (center) OrbitColors.Surface else Color.Transparent)
                .padding(start = if (center) 8.dp else 0.dp)
                .alpha(if (center) 1f else if (abs(dy) < 100) 0.85f else 0.55f),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Box(
                Modifier.size(icon.dp).clip(CircleShape).background(if (item.unread) Color(0xFF232321) else Color(0xFF161615)),
                contentAlignment = Alignment.Center,
            ) {
                SvgIcon(path, (icon * 0.42f).dp, if (item.unread) fg else OrbitColors.Muted, strokeWidth = 2.4f)
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    if (item.unread) {
                        Box(Modifier.size(8.dp).clip(CircleShape).background(OrbitColors.Accent))
                        Spacer(Modifier.width(6.dp))
                    }
                    BasicText(
                        item.title,
                        style = TextStyle(
                            fontFamily = OrbitFonts.Grotesk, fontWeight = FontWeight.SemiBold,
                            fontSize = (if (center) 17 else 15).sp, color = if (item.unread) OrbitColors.Ink else OrbitColors.Muted,
                        ),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                BasicText(
                    InboxLogic.age(item.created_at, now) + if (item.body.isNotBlank()) " · ${item.body}" else "",
                    style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 12.sp, color = OrbitColors.Muted),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Spacer(Modifier.width(16.dp))
        }
    }
}
