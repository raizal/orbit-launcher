package com.orbit.launcher.data

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.location.LocationManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

/** Launcher settings: selected face, accent tweak per face, complication per slot. */
class LauncherPrefs(context: Context) {
    private val p = context.getSharedPreferences("launcher", Context.MODE_PRIVATE)

    var faceId: String
        get() = p.getString("face", "orbit")!!
        set(v) = p.edit().putString("face", v).apply()

    fun accent(faceId: String): String? = p.getString("accent:$faceId", null)
    fun setAccent(faceId: String, color: String?) = p.edit().putString("accent:$faceId", color).apply()

    fun slot(faceId: String, slot: Int): String? = p.getString("slot:$faceId:$slot", null)
    fun setSlot(faceId: String, slot: Int, type: String) = p.edit().putString("slot:$faceId:$slot", type).apply()
}

fun batteryLevel(context: Context): Int? =
    context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))?.let {
        val level = it.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = it.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
        if (level < 0) null else level * 100 / scale
    }

fun onWifi(context: Context): Boolean {
    val cm = context.getSystemService(ConnectivityManager::class.java)
    return cm.getNetworkCapabilities(cm.activeNetwork)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
}

/** Current weather + a short hourly forecast from Open-Meteo (no key), at city-level location. */
@Serializable
data class Weather(
    val temp: Int,
    val code: Int,
    val high: Int? = null,
    val low: Int? = null,
    val hourly: List<Hour> = emptyList(),
    val fetchedAt: Long = 0,
) {
    @Serializable
    data class Hour(val hour: Int, val temp: Int, val code: Int)
}

object WeatherRepo {
    private const val TAG = "OrbitWeather"
    private const val MAX_AGE_MS = 45 * 60 * 1000L
    private val json = Json { ignoreUnknownKeys = true }
    private val _weather = MutableStateFlow<Weather?>(null)
    val weather: StateFlow<Weather?> = _weather.asStateFlow()

    /** Loads the cache, then refreshes if older than 45 min. Safe to call often. */
    suspend fun refresh(context: Context) = withContext(Dispatchers.IO) {
        val prefs = context.getSharedPreferences("weather", Context.MODE_PRIVATE)
        if (_weather.value == null) {
            _weather.value = prefs.getString("cache", null)?.let { runCatching { json.decodeFromString(Weather.serializer(), it) }.getOrNull() }
        }
        val cached = _weather.value
        if (cached != null && System.currentTimeMillis() - cached.fetchedAt < MAX_AGE_MS) return@withContext
        val loc = lastLocation(context) ?: return@withContext Unit.also { Log.i(TAG, "no location yet") }
        runCatching { fetch(loc.first, loc.second) }
            .onSuccess {
                _weather.value = it
                prefs.edit().putString("cache", json.encodeToString(Weather.serializer(), it)).apply()
            }
            .onFailure { Log.w(TAG, "fetch failed", it) }
    }

    /** Last known coarse location; never waits for a fix (GPS on this watch is city-level anyway). */
    @SuppressLint("MissingPermission")
    private fun lastLocation(context: Context): Pair<Double, Double>? {
        val lm = context.getSystemService(LocationManager::class.java)
        val loc = runCatching {
            listOf(LocationManager.NETWORK_PROVIDER, LocationManager.PASSIVE_PROVIDER, LocationManager.GPS_PROVIDER)
                .mapNotNull { lm.getLastKnownLocation(it) }.maxByOrNull { it.time }
        }.getOrNull()
        val prefs = context.getSharedPreferences("weather", Context.MODE_PRIVATE)
        if (loc != null) {
            prefs.edit().putString("lat", loc.latitude.toString()).putString("lon", loc.longitude.toString()).apply()
            return loc.latitude to loc.longitude
        }
        val lat = prefs.getString("lat", null)?.toDoubleOrNull() ?: return null
        val lon = prefs.getString("lon", null)?.toDoubleOrNull() ?: return null
        return lat to lon
    }

    @Serializable
    private data class Resp(val current: Current, val hourly: Hourly, val daily: Daily) {
        @Serializable data class Current(val temperature_2m: Double, val weather_code: Int)
        @Serializable data class Hourly(val time: List<String>, val temperature_2m: List<Double>, val weather_code: List<Int>)
        @Serializable data class Daily(val temperature_2m_max: List<Double>, val temperature_2m_min: List<Double>)
    }

    private fun fetch(lat: Double, lon: Double): Weather {
        val url = "https://api.open-meteo.com/v1/forecast?latitude=%.2f&longitude=%.2f".format(java.util.Locale.US, lat, lon) +
            "&current=temperature_2m,weather_code&hourly=temperature_2m,weather_code" +
            "&daily=temperature_2m_max,temperature_2m_min&forecast_days=2&timezone=auto"
        val http = OkHttpClient.Builder().callTimeout(15, TimeUnit.SECONDS).build()
        val body = http.newCall(Request.Builder().url(url).build()).execute().use { it.body.string() }
        val r = json.decodeFromString(Resp.serializer(), body)
        val nowHour = java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY)
        val hours = r.hourly.time.indices.drop(nowHour + 1).take(6).map { i ->
            Weather.Hour(r.hourly.time[i].substring(11, 13).toInt(), r.hourly.temperature_2m[i].toInt(), r.hourly.weather_code[i])
        }
        return Weather(
            temp = Math.round(r.current.temperature_2m).toInt(),
            code = r.current.weather_code,
            high = r.daily.temperature_2m_max.firstOrNull()?.let { Math.round(it).toInt() },
            low = r.daily.temperature_2m_min.firstOrNull()?.let { Math.round(it).toInt() },
            hourly = hours,
            fetchedAt = System.currentTimeMillis(),
        )
    }
}

/** A posted notification as the launcher shows it. Filled by the notification listener. */
data class NotifItem(
    val key: String,
    val pkg: String,
    val app: String,
    val title: String,
    val text: String,
    val postedAt: Long,
    val clearable: Boolean,
)

object Notifs {
    private val _items = MutableStateFlow<List<NotifItem>>(emptyList())
    val items: StateFlow<List<NotifItem>> = _items.asStateFlow()

    /** True once the listener is connected; false → show "grant access" hint. */
    val connected = MutableStateFlow(false)

    fun set(items: List<NotifItem>) {
        _items.value = items.sortedByDescending { it.postedAt }
    }
}
