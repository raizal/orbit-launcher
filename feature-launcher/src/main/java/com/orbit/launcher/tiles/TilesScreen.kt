package com.orbit.launcher.tiles

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.PointerInputScope
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
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
import com.orbit.launcher.data.Weather
import com.orbit.launcher.data.WeatherRepo
import com.orbit.launcher.face.BATTERY_ICON
import com.orbit.launcher.face.MIC
import com.orbit.launcher.face.weatherIcon
import com.orbit.voice.upload.NoteState
import com.orbit.voice.upload.Progress
import com.orbit.voice.upload.Queue
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import kotlin.math.abs

private const val TILE_COUNT = 3 // weather, battery, voice notes
private const val PAGE = 400f
/** Horizontal drag (design units) that flips a tile / closes from the first one. */
private const val FLIP_AT = 80f

private val TRACK = Color(0xFF262624)
private val DOT_OFF = Color(0xFF4A4A46)
private val SOFT = Color(0xFFC9C8C2)
private val LIME = Color(0xFFB9F26B)

/**
 * Tiles — `design/Tiles.dc.html`. One-metric tiles in a horizontal pager, weather first,
 * then battery and today's voice notes (only data this watch really has: no heart
 * rate, no steps). Rotary / horizontal drag step through; drag right on the first
 * tile → face. Page dots on the bottom rim.
 */
@Composable
fun TilesScreen(nav: LauncherNav, env: LauncherEnv) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var index by remember { mutableIntStateOf(0) }
    val scroll = remember { Animatable(0f) } // design units; tile i sits at i * 400

    LaunchedEffect(Unit) { WeatherRepo.refresh(context) }

    fun goTo(i: Int) {
        index = i.coerceIn(0, TILE_COUNT - 1)
        scope.launch { scroll.animateTo(index * PAGE, tween(220)) }
    }
    RotaryEffect { goTo(index + it) }

    Box(
        Modifier.fillMaxSize().pointerInput(Unit) {
            pagerGestures(
                onDrag = { dx ->
                    val atEdge = (index == 0 && dx > 0) || (index == TILE_COUNT - 1 && dx < 0)
                    scope.launch { scroll.snapTo(index * PAGE - if (atEdge) dx * 0.35f else dx) }
                },
                onRelease = { dx ->
                    when {
                        index == 0 && dx > FLIP_AT -> { goTo(0); nav.go(Screen.FACE) }
                        dx < -FLIP_AT -> goTo(index + 1)
                        dx > FLIP_AT -> goTo(index - 1)
                        else -> goTo(index)
                    }
                },
            )
        },
    ) {
        for (i in 0 until TILE_COUNT) {
            Box(
                Modifier.fillMaxSize().graphicsLayer {
                    translationX = (i * PAGE - scroll.value) * density
                },
            ) {
                when (i) {
                    0 -> WeatherTile(env)
                    1 -> BatteryTile(env)
                    2 -> NotesTile(nav)
                }
            }
        }
        PageDots(index)
    }
}

/** Ignores downs in the top 40 units (system shade); consuming the drag cancels taps underneath. */
private suspend fun PointerInputScope.pagerGestures(onDrag: (Float) -> Unit, onRelease: (Float) -> Unit) = awaitEachGesture {
    val unit = size.width / PAGE
    val down = awaitFirstDown(requireUnconsumed = false)
    if (down.position.y < 40 * unit) return@awaitEachGesture
    var dx = 0f
    var dy = 0f
    var horizontal = false
    while (true) {
        val change = awaitPointerEvent().changes.firstOrNull { it.id == down.id } ?: break
        if (!change.pressed) break
        val d = change.positionChange()
        dx += d.x / unit
        dy += d.y / unit
        if (!horizontal) {
            if (abs(dy) > 16f && abs(dy) > abs(dx)) return@awaitEachGesture // vertical: not ours
            if (abs(dx) > 12f) horizontal = true
        }
        if (horizontal) {
            change.consume()
            onDrag(dx)
        }
    }
    if (horizontal) onRelease(dx)
}

@Composable
private fun PageDots(active: Int) {
    Canvas(Modifier.fillMaxSize()) {
        val u = size.width / PAGE
        for (i in 0 until TILE_COUNT) {
            val (x, y) = TileMath.pageDot(i, TILE_COUNT)
            drawCircle(if (i == active) OrbitColors.Ink else DOT_OFF, (if (i == active) 4f else 3f) * u, Offset(x * u, y * u))
        }
    }
}

/** Dotted 270° rim track with a [fraction] of it filled in [color]; optional dot at the fill's end. */
@Composable
private fun RimArc(fraction: Float?, color: Color, endDot: Boolean) {
    Box(
        Modifier.fillMaxSize().drawWithCache {
            // Strokes / offsets built once per size, not per frame.
            val u = size.width / PAGE
            val topLeft = Offset((200 - TileMath.ARC_R) * u, (200 - TileMath.ARC_R) * u)
            val box = Size(2 * TileMath.ARC_R * u, 2 * TileMath.ARC_R * u)
            val dotted = Stroke(3 * u, cap = StrokeCap.Round, pathEffect = PathEffect.dashPathEffect(floatArrayOf(2 * u, 6 * u)))
            val solid = Stroke(3 * u, cap = StrokeCap.Round)
            val dot = fraction?.let { TileMath.arcPoint(it) }?.let { (x, y) -> Offset(x * u, y * u) }
            onDrawBehind {
                drawArc(TRACK, TileMath.ARC_START, TileMath.ARC_SWEEP, false, topLeft, box, style = dotted)
                if (fraction != null && fraction > 0f) {
                    drawArc(color, TileMath.ARC_START, TileMath.ARC_SWEEP * fraction.coerceAtMost(1f), false, topLeft, box, style = solid)
                    if (endDot && dot != null) drawCircle(color, 7 * u, dot)
                }
            }
        },
    )
}

/** The design's center block at (80, 76), 240 wide: small caps label, icon + big number, one line under it. */
@Composable
private fun Headline(label: String, icon: String, iconColor: Color, value: String, line: String, modifier: Modifier = Modifier) {
    Column(
        Modifier.offset(80.dp, 76.dp).width(240.dp).then(modifier),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        BasicText(label, style = TextStyle(fontFamily = OrbitFonts.Inter, fontWeight = FontWeight.SemiBold, fontSize = 12.sp, letterSpacing = 0.12.em, color = OrbitColors.Muted))
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            SvgIcon(icon, 40.dp, iconColor, strokeWidth = 2f)
            BasicText(value, style = TextStyle(fontFamily = OrbitFonts.Grotesk, fontWeight = FontWeight.SemiBold, fontSize = 72.sp, lineHeight = 72.sp, letterSpacing = (-0.02).em, color = OrbitColors.Ink))
        }
        BasicText(line, style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 14.sp, color = SOFT, textAlign = TextAlign.Center), maxLines = 1)
    }
}

private val small = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 11.sp, color = OrbitColors.Muted)
private val lineStyle = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 13.sp, color = OrbitColors.Info, textAlign = TextAlign.Center)

// --- Weather ----------------------------------------------------------------

@Composable
private fun WeatherTile(env: LauncherEnv) {
    val context = LocalContext.current
    val w by WeatherRepo.weather.collectAsState()
    val data by env.data.collectAsState()
    val tint = OrbitColors.Accent
    val now = data.now.get(Calendar.HOUR_OF_DAY) * 60 + data.now.get(Calendar.MINUTE)
    val weather = w

    if (weather == null) {
        WeatherMissing(context)
        return
    }
    val sun = TileMath.sunFraction(weather.sunrise, weather.sunset, now)
    RimArc(sun, tint, endDot = true)
    if (weather.sunrise != null && weather.sunset != null) {
        BasicText(weather.sunrise, Modifier.offset(52.dp, 330.dp), style = small)
        BasicText(weather.sunset, Modifier.offset(316.dp, 330.dp), style = small)
    }
    val age = System.currentTimeMillis() - weather.fetchedAt
    val label = if (age < 90 * 60_000L) "WEATHER" else "AS OF " + SimpleDateFormat("HH:mm", Locale.US).format(Date(weather.fetchedAt))
    Headline(label, weatherIcon(weather.code), tint, "${weather.temp}°", TileMath.summary(weather))
    HourColumns(weather)
    TileMath.rainLine(weather)?.let {
        BasicText(it, Modifier.offset(0.dp, 300.dp).width(400.dp), style = lineStyle)
    }
}

@Composable
private fun HourColumns(weather: Weather) {
    Row(Modifier.offset(84.dp, 238.dp).width(232.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        for (h in TileMath.columns(weather.hourly)) {
            Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(4.dp)) {
                BasicText("%02d".format(h.hour), style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 12.sp, color = OrbitColors.Muted))
                BasicText("${h.temp}°", style = TextStyle(fontFamily = OrbitFonts.Grotesk, fontWeight = FontWeight.SemiBold, fontSize = 17.sp, color = OrbitColors.Ink))
            }
        }
    }
}

/** No weather yet: say why. Tap asks for the location permission (or retries). */
@Composable
private fun WeatherMissing(context: Context) {
    var asked by remember { mutableIntStateOf(0) }
    val permitted by produceState(WeatherRepo.hasLocationPermission(context), asked) {
        // After the system dialog: poll briefly for the grant, then load.
        repeat(30) {
            value = WeatherRepo.hasLocationPermission(context)
            if (value) return@produceState
            delay(1_000)
        }
    }
    LaunchedEffect(permitted) { if (permitted) WeatherRepo.refresh(context) }
    RimArc(null, OrbitColors.Accent, endDot = false)
    Headline(
        "WEATHER", weatherIcon(null), OrbitColors.Muted, "--°",
        if (permitted) "Finding location…" else "Location not allowed",
        Modifier.clickable {
            if (!WeatherRepo.hasLocationPermission(context)) {
                (context as? Activity)?.requestPermissions(arrayOf(Manifest.permission.ACCESS_COARSE_LOCATION), 41)
            }
            asked++
        },
    )
    if (!permitted) BasicText("Tap to allow", Modifier.offset(0.dp, 300.dp).width(400.dp), style = lineStyle)
}

// --- Battery ----------------------------------------------------------------

private class BatteryInfo(val charging: Boolean, val tempC: Int?)

private fun batteryInfo(context: Context): BatteryInfo? =
    context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))?.let {
        val status = it.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        val temp = it.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE)
        BatteryInfo(
            charging = status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL,
            tempC = if (temp == Int.MIN_VALUE) null else temp / 10,
        )
    }

@Composable
private fun BatteryTile(env: LauncherEnv) {
    val context = LocalContext.current
    val data by env.data.collectAsState()
    val level = data.batteryLevel
    val info by produceState<BatteryInfo?>(null, data.now.timeInMillis) {
        value = withContext(Dispatchers.IO) { batteryInfo(context) }
    }
    val charging = info?.charging == true
    val color = when {
        charging -> LIME
        level != null && level <= 15 -> OrbitColors.Rec
        else -> OrbitColors.Accent
    }
    RimArc(level?.let { it / 100f }, color, endDot = false)
    val state = if (charging) "Charging" else "On battery"
    Headline(
        "BATTERY", BATTERY_ICON, color, level?.let { "$it%" } ?: "--",
        info?.tempC?.let { "$state · $it °C" } ?: state,
    )
}

// --- Voice notes ------------------------------------------------------------

private class NotesToday(val count: Int, val toSend: Int, val last: String?)

@Composable
private fun NotesTile(nav: LauncherNav) {
    val context = LocalContext.current
    val version by Progress.version.collectAsState()
    val notes by produceState<NotesToday?>(null, version) {
        value = withContext(Dispatchers.IO) {
            val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
            val list = Queue(context).notes().filter { it.stem.startsWith(today) } // newest first
            NotesToday(
                count = list.size,
                toSend = list.count { it.state != NoteState.SENT },
                // stem: yyyy-MM-dd_HH-mm-ss+zzzz → "HH:mm"
                last = list.firstOrNull()?.stem?.takeIf { it.length >= 16 }?.let { it.substring(11, 13) + ":" + it.substring(14, 16) },
            )
        }
    }
    val n = notes
    val count = n?.count ?: 0
    RimArc(if (n != null && count > 0) (count - n.toSend).toFloat() / count else null, OrbitColors.Info, endDot = false)
    val line = when {
        n == null -> ""
        count == 0 -> "No notes yet"
        n.toSend == 0 -> if (count == 1) "1 note · sent" else "$count notes · all sent"
        else -> "${if (count == 1) "1 note" else "$count notes"} · ${n.toSend} to send"
    }
    // Headline opens the inbox; the red button records a note right away.
    Headline("TODAY", MIC, OrbitColors.Rec, if (n == null) "--" else "$count", line, Modifier.clickable { nav.openInbox() })
    Box(
        Modifier.offset(168.dp, 234.dp).size(64.dp).clip(CircleShape).clickable { nav.recordNow() },
        contentAlignment = Alignment.Center,
    ) {
        Box(Modifier.size(60.dp).clip(CircleShape).background(OrbitColors.Rec), contentAlignment = Alignment.Center) {
            SvgIcon(MIC, 24.dp, OrbitColors.Ink, strokeWidth = 2.2f)
        }
    }
    n?.last?.let { BasicText("Last at $it", Modifier.offset(0.dp, 308.dp).width(400.dp), style = lineStyle.copy(color = OrbitColors.Muted)) }
}
