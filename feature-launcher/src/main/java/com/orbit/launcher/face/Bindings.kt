package com.orbit.launcher.face

import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale

/** Live values a face can bind to. Null = not available on this watch → the layer is hidden. */
data class FaceData(
    val now: Calendar,
    val batteryLevel: Int?,
    val weatherTemp: Int?,
    val weatherCode: Int?,
    val notifUnread: Int?,
    val wifi: Boolean,
    val lte: Boolean,
) {
    /** `{binding}` → value, or null if unknown / unavailable (no step counter on the H10). */
    fun lookup(key: String): String? = when (key) {
        "time.HH" -> fmt("HH")
        "time.hh" -> fmt("hh")
        "time.mm" -> fmt("mm")
        "time.ss" -> fmt("ss")
        "date.EEE" -> fmt("EEE")
        "date.EEEE" -> fmt("EEEE")
        "date.d" -> fmt("d")
        "date.MMM" -> fmt("MMM")
        "battery.level" -> batteryLevel?.toString()
        "battery.progress" -> batteryLevel?.let { (it / 100f).toString() }
        "day.progress" -> dayProgress().toString()
        "weather.temp" -> weatherTemp?.let { "$it°" }
        "notif.unread" -> notifUnread?.toString()
        "net.wifi" -> if (wifi) "1" else "0"
        "net.lte" -> if (lte) "1" else "0"
        else -> null // steps.* and unknown keys
    }

    /** Replaces every `{key}`; null if any binding is unavailable. */
    fun resolve(template: String): String? {
        var missing = false
        val out = BINDING.replace(template) { m -> lookup(m.groupValues[1]) ?: run { missing = true; "" } }
        return if (missing) null else out
    }

    /** A 0..1 value: a binding like `{battery.progress}` or a literal number. */
    fun fraction(value: String?): Float? = value?.let { resolve(it)?.toFloatOrNull()?.coerceIn(0f, 1f) }

    fun dayProgress(): Float =
        (now.get(Calendar.HOUR_OF_DAY) * 3600 + now.get(Calendar.MINUTE) * 60 + now.get(Calendar.SECOND)) / 86_400f

    private fun fmt(p: String) = SimpleDateFormat(p, Locale.US).format(now.time)

    companion object {
        private val BINDING = Regex("""\{([a-zA-Z]+\.[a-zA-Z]+)\}""")

        fun usesSeconds(face: Face): Boolean {
            fun Layer.secs(): Boolean = text.contains("time.ss") || (type == "hands" && hand == "second") || layers.any { it.secs() }
            return face.layers.any { it.secs() }
        }
    }
}
