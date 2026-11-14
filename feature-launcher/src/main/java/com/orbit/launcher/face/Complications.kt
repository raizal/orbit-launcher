package com.orbit.launcher.face

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.orbit.core.OrbitColors
import com.orbit.core.OrbitFonts
import com.orbit.core.SvgIcon

/** Complication types a slot can show (FaceCustomize offers these). */
object Complication {
    const val WEATHER = "weather"
    const val VOICE_NOTE = "voice_note"
    const val BATTERY = "battery"
    const val DATE = "date"
    const val NOTIF = "notif"
    const val NONE = "none"
    val ALL = listOf(WEATHER, VOICE_NOTE, BATTERY, DATE, NOTIF, NONE)

    fun label(type: String) = when (type) {
        WEATHER -> "Weather"
        VOICE_NOTE -> "Voice note"
        BATTERY -> "Battery"
        DATE -> "Date"
        NOTIF -> "Notifications"
        else -> "Empty"
    }

    fun icon(type: String) = when (type) {
        WEATHER -> SUN
        VOICE_NOTE -> MIC
        BATTERY -> BATTERY_ICON
        DATE -> CALENDAR
        NOTIF -> BELL
        else -> ""
    }
}

const val SUN = "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"
const val CLOUD = "M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9z"
const val RAIN = "M20 16.58A5 5 0 0 0 18 7h-1.26A8 8 0 1 0 4 15.25M8 19v2M8 13v2M16 19v2M16 13v2M12 21v2M12 15v2"
const val STORM = "M19 16.9A5 5 0 0 0 18 7h-1.26a8 8 0 1 0-11.62 9M13 11l-4 6h6l-4 6"
const val MIC = "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3zM19 10v2a7 7 0 0 1-14 0v-2M12 19v3"
const val BATTERY_ICON = "M4 7h13a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2zM22 11v2"
const val CALENDAR = "M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"
const val BELL = "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0"

/** WMO weather code (Open-Meteo) → icon path. */
fun weatherIcon(code: Int?): String = when (code) {
    null, 0, 1 -> SUN
    in 51..67, in 80..82 -> RAIN
    in 95..99 -> STORM
    else -> CLOUD
}

/** All `complication` layers (also inside groups), positioned by their (x, y) center. */
@Composable
fun ComplicationLayers(
    layers: List<Layer>,
    faceSize: Float,
    colors: FaceColors,
    data: FaceData,
    slotType: (Int, String) -> String,
    onTap: (String) -> Unit,
    dx: Float = 0f,
    dy: Float = 0f,
) {
    val k = 400f / faceSize
    layers.forEach { l ->
        when (l.type) {
            "complication" -> {
                val type = slotType(l.slot, l.default)
                if (type != Complication.NONE) {
                    val d = if (l.style == "bare") 72 else 60
                    Box(
                        Modifier
                            .offset(((l.x + dx) * k - d / 2).dp, ((l.y + dy) * k - d / 2).dp)
                            .size(d.dp)
                            .clip(CircleShape)
                            .then(if (l.style == "bare") Modifier else Modifier.background(Color(0xFF161615)))
                            .clickable { onTap(type) },
                        contentAlignment = Alignment.Center,
                    ) { ComplicationContent(type, data, colors) }
                }
            }
            "group" -> ComplicationLayers(l.layers, faceSize, colors, data, slotType, onTap, dx + l.x - 200, dy + l.y - 200)
        }
    }
}

@Composable
private fun ComplicationContent(type: String, data: FaceData, colors: FaceColors) {
    val value = TextStyle(fontFamily = OrbitFonts.Grotesk, fontWeight = FontWeight.SemiBold, fontSize = 17.sp, color = OrbitColors.Ink)
    val small = TextStyle(fontFamily = OrbitFonts.Inter, fontWeight = FontWeight.SemiBold, fontSize = 11.sp, letterSpacing = 0.08.em, color = OrbitColors.Ink)
    Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(2.dp)) {
        when (type) {
            Complication.WEATHER -> {
                SvgIcon(weatherIcon(data.weatherCode), 16.dp, OrbitColors.Accent, strokeWidth = 2.2f)
                BasicText(data.weatherTemp?.let { "$it°" } ?: "--", style = value)
            }
            Complication.VOICE_NOTE -> {
                SvgIcon(MIC, 18.dp, OrbitColors.Rec, strokeWidth = 2.2f)
                BasicText("NOTE", style = small)
            }
            Complication.BATTERY -> {
                SvgIcon(BATTERY_ICON, 16.dp, OrbitColors.Ink, strokeWidth = 2.2f)
                BasicText(data.batteryLevel?.toString() ?: "--", style = value)
            }
            Complication.DATE -> {
                BasicText(data.resolve("{date.EEE}")!!.uppercase(), style = small.copy(color = OrbitColors.Muted, fontWeight = FontWeight.Normal, letterSpacing = 0.1.em))
                BasicText(data.resolve("{date.d}")!!, style = value.copy(fontSize = 20.sp))
            }
            Complication.NOTIF -> {
                SvgIcon(BELL, 16.dp, colors["accent"], strokeWidth = 2.2f)
                BasicText(data.notifUnread?.toString() ?: "–", style = value)
            }
        }
    }
}
