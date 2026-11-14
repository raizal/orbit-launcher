package com.orbit.launcher.notifs

import android.content.Context
import android.util.LruCache
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.PointerInputScope
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.graphics.drawable.toBitmap
import com.orbit.core.CurvedText
import com.orbit.core.OrbitColors
import com.orbit.core.OrbitFonts
import com.orbit.core.RotaryEffect
import com.orbit.core.SvgIcon
import com.orbit.launcher.LauncherEnv
import com.orbit.launcher.LauncherNav
import com.orbit.launcher.Screen
import com.orbit.launcher.data.NotifItem
import com.orbit.launcher.data.Notifs
import com.orbit.launcher.face.BELL
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.math.abs

private const val CHAT = "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
private const val OPEN = "M7 17 17 7M7 7h10v10"
private const val CLOSE = "M18 6 6 18M6 6l12 12"

/** Sideways card swipe (design units) past which a clearable notification is dismissed. */
private const val DISMISS_AT = 110f
/** Vertical drag per step through the stack (design units). */
private const val STEP = 70f

/**
 * Notifications — `design/Notifications.dc.html`. One notification at a time as a card,
 * the next one previewed in a pill at the bottom.
 *
 * Tap card / accent button → open it. ✕ button or sideways swipe on a clearable one → dismiss.
 * Rotary or vertical drag → step through. Drag down on the newest → back to the face.
 */
@Composable
fun NotificationsScreen(nav: LauncherNav, env: LauncherEnv) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val all by Notifs.items.collectAsState()
    val connected by Notifs.connected.collectAsState()
    // Keys dismissed here, hidden at once instead of waiting for the listener round-trip.
    var hidden by remember { mutableStateOf(emptySet<String>()) }
    val items = remember(all, hidden) { all.filter { it.key !in hidden } }
    var focus by remember { mutableIntStateOf(0) }
    val last = (items.size - 1).coerceAtLeast(0)
    if (focus > last) focus = last
    val current = items.getOrNull(focus)
    val accent = remember { accentOf(env) }
    val swipe = remember { Animatable(0f) }

    LaunchedEffect(connected) { if (!connected) withContext(Dispatchers.IO) { NotifListener.ensureBound(context) } }
    RotaryEffect { focus = (focus + it).coerceIn(0, last) }

    fun dismiss(item: NotifItem, direction: Float) {
        scope.launch {
            swipe.animateTo(if (direction < 0) -420f else 420f, tween(160))
            hidden = hidden + item.key
            NotifListener.dismiss(item.key)
            swipe.snapTo(0f)
        }
    }

    // The gesture detector is installed once; it reads the latest values through these.
    val state = rememberUpdatedState(current)
    val lastState = rememberUpdatedState(last)
    Box(
        Modifier.fillMaxSize().pointerInput(Unit) {
            panelGestures(
                atTop = { focus == 0 },
                onCardDrag = { dx ->
                    val c = state.value ?: return@panelGestures
                    scope.launch { swipe.snapTo(if (c.clearable) dx else dx * 0.25f) }
                },
                onCardRelease = { dx ->
                    val c = state.value
                    if (c != null && c.clearable && abs(dx) > DISMISS_AT) dismiss(c, dx)
                    else scope.launch { swipe.animateTo(0f, tween(160)) }
                },
                onStep = { focus = (focus + it).coerceIn(0, lastState.value) },
                onClose = { nav.go(Screen.FACE) },
            )
        },
    ) {
        CurvedText(
            NotifFormat.header(items.size, focus),
            top = true, color = if (items.isEmpty()) OrbitColors.Muted else accent, sizeSp = 13f, letterSpacingPx = 2.5f,
        )
        when {
            !connected -> AccessHint()
            current == null -> Empty()
            else -> {
                Card(
                    current,
                    Modifier
                        .offset(56.dp, 70.dp)
                        .graphicsLayer {
                            translationX = swipe.value * density
                            alpha = 1f - (abs(swipe.value) / 300f).coerceAtMost(0.7f)
                        },
                    onOpen = { NotifListener.open(context, current) },
                )
                val openX = if (current.clearable) 128 else 170
                RoundButton(openX, accent, OPEN, OrbitColors.OnAccent, 24) { NotifListener.open(context, current) }
                if (current.clearable) {
                    RoundButton(212, Color(0xFF232321), CLOSE, OrbitColors.Ink, 22) { dismiss(current, 1f) }
                }
                items.getOrNull(focus + 1)?.let { next -> NextPill(next) { focus = (focus + 1).coerceAtMost(last) } }
            }
        }
    }
}

/**
 * Down outside the top 40 units (system shade). Horizontal drags move the card,
 * vertical drags step through the stack; a downward drag that starts on the
 * newest card closes the panel. Consuming the move cancels taps underneath.
 */
private suspend fun PointerInputScope.panelGestures(
    atTop: () -> Boolean,
    onCardDrag: (Float) -> Unit,
    onCardRelease: (Float) -> Unit,
    onStep: (Int) -> Unit,
    onClose: () -> Unit,
) = awaitEachGesture {
    val unit = size.width / 400f
    val down = awaitFirstDown(requireUnconsumed = false)
    if (down.position.y < 40 * unit) return@awaitEachGesture
    val startedAtTop = atTop()
    var dx = 0f
    var dy = 0f
    var stepped = 0f
    var axis = 0 // 0 undecided, 1 horizontal, 2 vertical
    while (true) {
        val change = awaitPointerEvent().changes.firstOrNull { it.id == down.id } ?: break
        if (!change.pressed) break
        val d = change.positionChange()
        dx += d.x / unit
        dy += d.y / unit
        if (axis == 0 && (abs(dx) > 12f || abs(dy) > 12f)) axis = if (abs(dx) > abs(dy)) 1 else 2
        if (axis == 1) {
            change.consume()
            onCardDrag(dx)
        } else if (axis == 2) {
            change.consume()
            while (dy - stepped <= -STEP) { onStep(1); stepped -= STEP }
            while (dy - stepped >= STEP) {
                if (startedAtTop) { onClose(); return@awaitEachGesture }
                onStep(-1); stepped += STEP
            }
        }
    }
    if (axis == 1) onCardRelease(dx)
}

@Composable
private fun Card(item: NotifItem, modifier: Modifier, onOpen: () -> Unit) {
    Column(
        modifier
            .width(288.dp)
            .clip(RoundedCornerShape(28.dp))
            .background(OrbitColors.Surface)
            .clickable(onClick = onOpen)
            .padding(horizontal = 18.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            AppBadge(item.pkg)
            BasicText(
                "${item.app} · ${NotifFormat.age(item.postedAt, System.currentTimeMillis())}",
                style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 13.sp, color = OrbitColors.Muted),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        BasicText(
            item.title,
            style = TextStyle(fontFamily = OrbitFonts.Grotesk, fontWeight = FontWeight.SemiBold, fontSize = 20.sp, color = OrbitColors.Ink),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        if (item.text.isNotEmpty()) {
            BasicText(
                item.text,
                style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 15.sp, lineHeight = 21.sp, color = Color(0xFFD8D7D1)),
                maxLines = 3, // card ends above the buttons at y = 254
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

/** The app's own icon (loaded off the main thread, cached); the design's cyan chat badge until then. */
@Composable
private fun AppBadge(pkg: String) {
    val context = LocalContext.current
    val icon by produceState(AppIcons.cached(pkg), pkg) { value = AppIcons.load(context, pkg) }
    val bmp = icon
    if (bmp != null) {
        Image(bmp, contentDescription = null, modifier = Modifier.size(22.dp))
    } else {
        Box(Modifier.size(22.dp).clip(CircleShape).background(OrbitColors.Info), contentAlignment = Alignment.Center) {
            SvgIcon(CHAT, 12.dp, OrbitColors.OnAccent, strokeWidth = 2.6f)
        }
    }
}

/** 60 px round button from the design, inside a 64 px touch target. */
@Composable
private fun RoundButton(x: Int, bg: Color, path: String, fg: Color, iconSize: Int, onClick: () -> Unit) {
    Box(
        Modifier.offset((x - 2).dp, 252.dp).size(64.dp).clip(CircleShape).clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Box(Modifier.size(60.dp).clip(CircleShape).background(bg), contentAlignment = Alignment.Center) {
            SvgIcon(path, iconSize.dp, fg, strokeWidth = 2.4f)
        }
    }
}

/** Bottom pill: a peek at the next notification; tap to step to it. */
@Composable
private fun NextPill(next: NotifItem, onClick: () -> Unit) {
    Row(
        Modifier
            .offset(92.dp, 334.dp)
            .size(216.dp, 44.dp)
            .clip(RoundedCornerShape(22.dp))
            .background(Color(0xFF141413))
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        SvgIcon(BELL, 14.dp, OrbitColors.Muted, strokeWidth = 2.2f)
        BasicText(
            next.title,
            style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 13.sp, color = OrbitColors.Muted),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
private fun Empty() {
    Column(
        Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        SvgIcon(BELL, 32.dp, OrbitColors.Muted, strokeWidth = 2f)
        BasicText(
            "No notifications",
            Modifier.padding(top = 12.dp),
            style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 15.sp, color = OrbitColors.Muted),
        )
    }
}

/** Listener not bound: say how to grant access (there is no settings screen for it on this watch). */
@Composable
private fun AccessHint() {
    Column(
        Modifier.offset(72.dp, 0.dp).width(256.dp).fillMaxSize(),
        verticalArrangement = Arrangement.spacedBy(10.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        SvgIcon(BELL, 28.dp, OrbitColors.Accent, strokeWidth = 2f)
        BasicText(
            "Notification access is off",
            style = TextStyle(fontFamily = OrbitFonts.Grotesk, fontWeight = FontWeight.SemiBold, fontSize = 18.sp, color = OrbitColors.Ink, textAlign = TextAlign.Center),
        )
        BasicText(
            "Grant it with adb:\ncmd notification allow_listener com.orbit/com.orbit.launcher.notifs.NotifListener",
            style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 12.sp, lineHeight = 16.sp, color = OrbitColors.Muted, textAlign = TextAlign.Center),
        )
    }
}

private fun accentOf(env: LauncherEnv): Color =
    env.prefs.accent(env.prefs.faceId)
        ?.let { runCatching { Color(android.graphics.Color.parseColor(it)) }.getOrNull() }
        ?: OrbitColors.Accent

/** App icons for the card badge, decoded once at badge size. */
private object AppIcons {
    private val cache = LruCache<String, ImageBitmap>(24)

    fun cached(pkg: String): ImageBitmap? = cache.get(pkg)

    suspend fun load(context: Context, pkg: String): ImageBitmap? = cache.get(pkg) ?: withContext(Dispatchers.IO) {
        runCatching {
            context.packageManager.getApplicationIcon(pkg).toBitmap(44, 44).asImageBitmap()
        }.getOrNull()?.also { cache.put(pkg, it) }
    }
}
