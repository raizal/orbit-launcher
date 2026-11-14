package com.orbit.core

import android.graphics.Paint
import android.graphics.Typeface
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import androidx.core.content.res.ResourcesCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner

/**
 * Rotary crown. On the H10 it is a sensor (type 36), not input events:
 * one event per detent, values[0] = 9 (clockwise) or 5 (counter-clockwise).
 * [onStep] gets +1 for clockwise, −1 for counter-clockwise.
 *
 * Listens only while the activity is resumed: a sensor listener is not tied to
 * focus, so an activity in the back stack (e.g. the launcher) would otherwise
 * react to the crown under the screen in front.
 */
@Composable
fun RotaryEffect(onStep: (Int) -> Unit) {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val step = rememberUpdatedState(onStep)
    DisposableEffect(lifecycle) {
        val sm = context.getSystemService(SensorManager::class.java)
        val sensor = sm.getSensorList(Sensor.TYPE_ALL)
            .firstOrNull { it.stringType == "android.sensor.rotary_encoder" || it.type == 36 }
        val listener = object : SensorEventListener {
            override fun onSensorChanged(e: SensorEvent) {
                when (e.values.firstOrNull()?.toInt()) {
                    9 -> step.value(1)
                    5 -> step.value(-1)
                }
            }
            override fun onAccuracyChanged(s: Sensor?, a: Int) = Unit
        }
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_RESUME -> sensor?.let { sm.registerListener(listener, it, SensorManager.SENSOR_DELAY_GAME) }
                Lifecycle.Event.ON_PAUSE -> sm.unregisterListener(listener)
                else -> Unit
            }
        }
        lifecycle.addObserver(observer) // replays ON_RESUME if already resumed
        onDispose {
            lifecycle.removeObserver(observer)
            sm.unregisterListener(listener)
        }
    }
}

/** A 24-unit SVG stroke icon (lucide-style paths from the design files). */
@Composable
fun SvgIcon(pathData: String, size: Dp, color: Color, strokeWidth: Float = 2f, modifier: Modifier = Modifier) {
    val path = remember(pathData) { PathParser().parsePathString(pathData).toPath() }
    Canvas(modifier.size(size)) {
        scale(this.size.width / 24f, pivot = androidx.compose.ui.geometry.Offset.Zero) {
            drawPath(path, color, style = Stroke(strokeWidth, cap = StrokeCap.Round, join = StrokeJoin.Round))
        }
    }
}

/**
 * Text along the rim, like the design's SVG `textPath` on an arc of radius [radius]
 * (design px) centered on the screen. [top] = along the top arc, else the bottom.
 */
@Composable
fun CurvedText(
    text: String,
    top: Boolean,
    color: Color,
    sizeSp: Float,
    letterSpacingPx: Float,
    radius: Float = 168f,
    fontRes: Int = R.font.inter_semibold,
) {
    val context = LocalContext.current
    val typeface: Typeface? = remember(fontRes) { ResourcesCompat.getFont(context, fontRes) }
    Canvas(Modifier.fillMaxSize()) {
        val unit = size.width / DESIGN_SIZE
        val c = size.width / 2
        val r = radius * unit
        val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            this.color = color.toArgb()
            textSize = sizeSp * unit
            this.typeface = typeface
            textAlign = Paint.Align.CENTER
            letterSpacing = letterSpacingPx / sizeSp
        }
        val path = android.graphics.Path()
        val oval = android.graphics.RectF(c - r, c - r, c + r, c + r)
        if (top) {
            path.addArc(oval, 180f, 180f) // left → right over the top; text sits above the arc
            drawContext.canvas.nativeCanvas.drawTextOnPath(text, path, 0f, 0f, paint)
        } else {
            path.addArc(oval, 180f, -180f) // left → right under the bottom; glyphs upright
            drawContext.canvas.nativeCanvas.drawTextOnPath(text, path, 0f, 0f, paint)
        }
    }
}
