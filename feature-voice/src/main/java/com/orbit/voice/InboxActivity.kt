package com.orbit.voice

import android.content.Intent
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
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.orbit.core.CurvedText
import com.orbit.core.OrbitColors
import com.orbit.core.OrbitFonts
import com.orbit.core.R as CoreR
import com.orbit.core.RotaryEffect
import com.orbit.core.RoundScreen
import com.orbit.core.SvgIcon
import com.orbit.voice.upload.Note
import com.orbit.voice.upload.NoteState
import com.orbit.voice.upload.Progress
import com.orbit.voice.upload.Queue
import com.orbit.voice.upload.ServerPrefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import kotlin.math.abs
import kotlin.math.sqrt

/** Notes and their send state — `design/VoiceInbox.dc.html`. */
class InboxActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true)
        val queue = Queue(this)
        val prefs = ServerPrefs(this)
        setContent {
            val version by Progress.version.collectAsState()
            val sending by Progress.sending.collectAsState()
            val notes by produceState(emptyList<Note>(), version) {
                value = withContext(Dispatchers.IO) { queue.notes() }
            }
            RoundScreen {
                InboxScreen(
                    notes = notes,
                    progress = sending,
                    serverName = prefs.pairing?.name,
                    onServers = { startActivity(Intent(this, ServersActivity::class.java)) },
                )
            }
        }
    }
}

private const val UP = "M12 19V5M5 12l7-7 7 7"
private const val OK = "M20 6 9 17l-5-5"
private const val WAIT = "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2"
private const val ALERT = "M12 8v5M12 17h.01M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z"

/** Row centers from the design; index 2 is the focused (center) row. */
private val ROW_Y = intArrayOf(74, 134, 200, 266, 326)

@Composable
fun InboxScreen(notes: List<Note>, progress: Map<String, Float>, serverName: String?, onServers: () -> Unit) {
    var focus by remember { mutableIntStateOf(0) }
    var drag by remember { mutableFloatStateOf(0f) }
    val last = (notes.size - 1).coerceAtLeast(0)
    if (focus > last) focus = last
    RotaryEffect { focus = (focus + it).coerceIn(0, last) }

    val toSend = notes.count { it.state != NoteState.SENT }
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
            if (toSend == 0) "NOTES · ALL SENT" else "NOTES · $toSend TO SEND",
            top = true, color = OrbitColors.Accent, sizeSp = 13f, letterSpacingPx = 2.5f,
        )
        CurvedText(
            if (serverName == null) "NOT PAIRED · TAP TO PAIR" else "TO ${serverName.uppercase()} · WI-FI",
            top = false, color = OrbitColors.Muted, sizeSp = 12f, letterSpacingPx = 1.5f, fontRes = CoreR.font.inter_medium,
        )
        if (notes.isEmpty()) {
            BasicText(
                "No notes yet",
                Modifier.offset(y = 188.dp).width(400.dp),
                style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 15.sp, color = OrbitColors.Muted, textAlign = TextAlign.Center),
            )
        }
        for (slot in ROW_Y.indices) {
            val note = notes.getOrNull(focus + slot - 2) ?: continue
            NoteRow(note, slot, progress[note.stem])
        }
        // Bottom rim label opens the server screen (≥ 64 px tall target).
        Box(Modifier.offset(100.dp, 344.dp).size(200.dp, 56.dp).clickable(onClick = onServers))
    }
}

@Composable
private fun NoteRow(note: Note, slot: Int, fraction: Float?) {
    val dy = ROW_Y[slot] - 200
    val center = dy == 0
    val icon = if (center) 48 else if (abs(dy) < 100) 42 else 36
    val h = if (center) 64 else icon
    val edge = 200 - sqrt((178f * 178f - dy * dy))
    val left = (edge + if (center) 18 else 30).toInt()
    val state = note.state
    val (path, iconBg, iconFg) = when (state) {
        NoteState.SENDING -> Triple(UP, OrbitColors.Accent, OrbitColors.OnAccent)
        NoteState.WAITING -> Triple(WAIT, Color(0xFF232321), OrbitColors.Ink)
        NoteState.FAILED -> Triple(ALERT, Color(0xFF2A1513), OrbitColors.Rec)
        NoteState.SENT -> Triple(OK, Color(0xFF161615), OrbitColors.Muted)
    }
    val subColor = when (state) {
        NoteState.SENDING -> OrbitColors.Accent
        NoteState.FAILED -> OrbitColors.Rec
        else -> OrbitColors.Muted
    }
    Row(
        Modifier
            .offset(left.dp, (ROW_Y[slot] - h / 2).dp)
            .size((352 - left).dp, h.dp)
            .clip(RoundedCornerShape(50))
            .background(if (center) OrbitColors.Surface else Color.Transparent)
            .padding(start = if (center) 8.dp else 0.dp)
            .alpha(if (center) 1f else if (abs(dy) < 100) 0.85f else 0.55f),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(Modifier.size(icon.dp).clip(CircleShape).background(iconBg), contentAlignment = Alignment.Center) {
            SvgIcon(path, (icon * 0.42f).dp, iconFg, strokeWidth = 2.4f)
        }
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            BasicText(
                title(note),
                style = TextStyle(fontFamily = OrbitFonts.Grotesk, fontWeight = FontWeight.SemiBold, fontSize = (if (center) 19 else 16).sp, color = OrbitColors.Ink),
                maxLines = 1,
            )
            BasicText(
                subtitle(note, fraction),
                style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 12.sp, color = subColor),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Spacer(Modifier.width(8.dp))
    }
}

/** "12:41", "Yesterday 22:40", or "11 Nov 22:40" — from the file name (local time of the press). */
private fun title(note: Note): String {
    val t = runCatching { SimpleDateFormat("yyyy-MM-dd_HH-mm-ssZ", Locale.US).parse(note.stem)!! }.getOrNull() ?: return note.stem
    val day = Calendar.getInstance().apply { time = t }
    val today = Calendar.getInstance()
    val hm = SimpleDateFormat("HH:mm", Locale.US).format(t)
    fun sameDay(a: Calendar, b: Calendar) = a.get(Calendar.YEAR) == b.get(Calendar.YEAR) && a.get(Calendar.DAY_OF_YEAR) == b.get(Calendar.DAY_OF_YEAR)
    return when {
        sameDay(day, today) -> hm
        sameDay(day, (today.clone() as Calendar).apply { add(Calendar.DAY_OF_YEAR, -1) }) -> "Yesterday $hm"
        else -> SimpleDateFormat("d MMM HH:mm", Locale.US).format(t)
    }
}

private fun subtitle(note: Note, fraction: Float?): String {
    val ms = note.meta?.duration_ms ?: 0
    val s = ms / 1000
    val len = "%d:%02d".format(s / 60, s % 60)
    return when (note.state) {
        NoteState.WAITING -> "$len · waiting"
        NoteState.SENDING -> "$len · sending ${((fraction ?: 0f) * 100).toInt()}%"
        NoteState.SENT -> "$len · sent"
        NoteState.FAILED -> "$len · ${note.error ?: "failed"}"
    }
}
