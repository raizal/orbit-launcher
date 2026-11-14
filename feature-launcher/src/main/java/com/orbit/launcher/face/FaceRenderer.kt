package com.orbit.launcher.face

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.orbit.core.OrbitFonts
import java.util.Calendar
import kotlin.math.cos
import kotlin.math.sin

/** Resolves a face color: theme key, or `#RRGGBB` / `#AARRGGBB`. */
class FaceColors(private val theme: Map<String, String>) {
    operator fun get(key: String?): Color {
        val raw = key?.let { theme[it] ?: it } ?: return Color.Unspecified
        return runCatching { Color(android.graphics.Color.parseColor(raw)) }.getOrDefault(Color.Magenta)
    }
}

/**
 * Draws a [Face] on one Canvas inside a 400 × 400 dp box (1 dp = 1 design unit,
 * see RoundScreen). Complications are composables so they can be tapped.
 */
@Composable
fun FaceView(
    face: Face,
    data: FaceData,
    accentOverride: String?,
    slotType: (slot: Int, default: String) -> String,
    onComplication: (type: String) -> Unit,
) {
    val theme = if (accentOverride != null) face.theme + ("accent" to accentOverride) else face.theme
    val colors = FaceColors(theme)
    val measurer = rememberTextMeasurer(cacheSize = 32)
    Box(Modifier.fillMaxSize()) {
        Canvas(Modifier.fillMaxSize()) {
            drawRect(colors[theme["bg"] ?: "#000000"])
            val unit = size.width / face.size
            face.layers.forEach { drawLayer(it, unit, colors, data, measurer) }
        }
        ComplicationLayers(face.layers, face.size, colors, data, slotType, onComplication)
    }
}

private fun DrawScope.drawLayer(l: Layer, unit: Float, colors: FaceColors, data: FaceData, measurer: TextMeasurer) {
    val cx = l.x * unit
    val cy = l.y * unit
    when (l.type) {
        "ticks" -> {
            for (i in 0 until l.count) {
                val isMajor = l.major > 0 && i % l.major == 0
                val len = (if (isMajor) l.majorLen ?: l.len * 2 else l.len) * unit
                val w = (if (isMajor) l.majorWidth ?: l.width * 2 else l.width) * unit
                val color = colors[if (isMajor) l.majorColor ?: l.color else l.color]
                val a = Math.toRadians(i * 360.0 / l.count - 90)
                val r1 = l.r * unit - len / 2
                val r2 = l.r * unit + len / 2
                drawLine(
                    color,
                    Offset(cx + r1 * cos(a).toFloat(), cy + r1 * sin(a).toFloat()),
                    Offset(cx + r2 * cos(a).toFloat(), cy + r2 * sin(a).toFloat()),
                    strokeWidth = w,
                )
            }
        }
        "arc", "circle" -> {
            // circle: optional fill; arc/circle: optional track, then a value or a plain outline.
            val r = l.r * unit
            val topLeft = Offset(cx - r, cy - r)
            val sz = Size(r * 2, r * 2)
            val stroke = Stroke(l.width * unit, cap = StrokeCap.Round)
            l.fill?.let { drawCircle(colors[it], r, Offset(cx, cy)) }
            l.track?.let { drawArc(colors[it], l.start, l.sweep, false, topLeft, sz, style = stroke) }
            when {
                l.value != null -> {
                    val f = data.fraction(l.value) ?: return
                    if (f > 0f) drawArc(colors[l.color], l.start, l.sweep * f, false, topLeft, sz, style = stroke)
                }
                l.fill == null && l.track == null -> drawArc(colors[l.color], l.start, l.sweep, false, topLeft, sz, style = stroke)
            }
        }
        "text" -> {
            val text = data.resolve(l.text)?.let { if (l.upper) it.uppercase() else it } ?: return
            val style = TextStyle(
                color = colors[l.color],
                fontSize = (l.size * unit / density).sp, // face units, independent of font scale
                fontWeight = FontWeight(l.weight),
                fontFamily = if (l.font == "grotesk") OrbitFonts.Grotesk else OrbitFonts.Inter,
                letterSpacing = l.spacing.em,
            )
            val layout = measurer.measure(text, style)
            // (x, y) is the center of the text box.
            drawText(layout, topLeft = Offset(cx - layout.size.width / 2f, cy - layout.size.height / 2f))
        }
        "hands" -> {
            val c = data.now
            val sec = c.get(Calendar.SECOND)
            val min = c.get(Calendar.MINUTE) + sec / 60f
            val angle = when (l.hand) {
                "hour" -> (c.get(Calendar.HOUR) + min / 60f) * 30f
                "second" -> sec * 6f
                else -> min * 6f
            }
            rotate(angle, Offset(cx, cy)) {
                drawLine(
                    colors[l.color],
                    Offset(cx, cy + l.tail * unit),
                    Offset(cx, cy - l.len * unit),
                    strokeWidth = l.width * unit,
                    cap = StrokeCap.Round,
                )
            }
        }
        "dot" -> drawCircle(colors[l.color], l.r * unit, Offset(cx, cy))
        "group" -> translate((l.x - 200) * unit, (l.y - 200) * unit) {
            l.layers.forEach { drawLayer(it, unit, colors, data, measurer) }
        }
        else -> Unit // complication (composable), image (later), unknown types: ignored
    }
}
