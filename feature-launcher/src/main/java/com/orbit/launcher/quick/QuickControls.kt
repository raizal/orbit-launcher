package com.orbit.launcher.quick

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationManager
import android.bluetooth.BluetoothAdapter
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.database.ContentObserver
import android.location.LocationManager
import android.media.AudioManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.telephony.TelephonyManager
import android.util.Log

/** Snapshot of everything the panel shows. */
data class QuickState(
    val wifi: Boolean = false,
    val bt: Boolean = false,
    val dnd: Boolean = false,
    val sound: Boolean = true,
    val location: Boolean = false,
    val wifiConnected: Boolean = false,
    val lte: Boolean = false,
    val simAbsent: Boolean = true,
    val brightness: Int = 128,
    val battery: Int? = null,
    val charging: Boolean = false,
    val remaining: String? = null,
)

/** What a toggle tap did. Anything but [OK] shows a short hint instead of crashing. */
enum class ToggleResult { OK, OPENED_SETTINGS, NEEDS_ADB_GRANT, UNAVAILABLE, FAILED }

/**
 * Reads and flips the Android 8.1 system toggles behind the quick settings panel.
 * Every call is guarded: vendor ROMs throw SecurityException in odd places.
 */
class QuickControls(private val context: Context) {
    private val wifiManager = context.applicationContext.getSystemService(WifiManager::class.java)
    private val notifications = context.getSystemService(NotificationManager::class.java)
    private val audio = context.getSystemService(AudioManager::class.java)
    private val connectivity = context.getSystemService(ConnectivityManager::class.java)
    private val telephony = context.getSystemService(TelephonyManager::class.java)
    private val batteryManager = context.getSystemService(BatteryManager::class.java)
    @Suppress("DEPRECATION")
    private val bluetooth: BluetoothAdapter? = runCatching { BluetoothAdapter.getDefaultAdapter() }.getOrNull()
    private val batteryPrefs = context.getSharedPreferences("quick_battery", Context.MODE_PRIVATE)
    private val prefs = context.getSharedPreferences("quick", Context.MODE_PRIVATE)

    fun read(): QuickState {
        val batteryIntent = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = batteryIntent?.let {
            val l = it.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
            val s = it.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
            if (l < 0 || s <= 0) null else l * 100 / s
        }
        val plugged = (batteryIntent?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
        return QuickState(
            wifi = guard(false) { wifiManager.wifiState.let { it == WifiManager.WIFI_STATE_ENABLED || it == WifiManager.WIFI_STATE_ENABLING } },
            bt = guard(false) { bluetooth?.state.let { it == BluetoothAdapter.STATE_ON || it == BluetoothAdapter.STATE_TURNING_ON } },
            dnd = guard(false) {
                val f = notifications.currentInterruptionFilter
                f != NotificationManager.INTERRUPTION_FILTER_ALL && f != NotificationManager.INTERRUPTION_FILTER_UNKNOWN
            },
            sound = guard(true) { audio.ringerMode == AudioManager.RINGER_MODE_NORMAL },
            location = guard(false) { locationMode() != Settings.Secure.LOCATION_MODE_OFF },
            wifiConnected = guard(false) { hasTransport(NetworkCapabilities.TRANSPORT_WIFI) },
            lte = guard(false) { hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) },
            simAbsent = guard(true) { telephony == null || telephony.simState == TelephonyManager.SIM_STATE_ABSENT },
            brightness = guard(128) { Settings.System.getInt(context.contentResolver, Settings.System.SCREEN_BRIGHTNESS) },
            battery = level,
            charging = plugged,
            remaining = if (plugged) {
                batteryPrefs.edit().clear().apply() // next unplug starts a fresh discharge history
                "charging"
            } else {
                level?.let { remaining(it) }
            },
        )
    }

    private fun hasTransport(t: Int) =
        connectivity.allNetworks.any { connectivity.getNetworkCapabilities(it)?.hasTransport(t) == true }

    @Suppress("DEPRECATION")
    private fun locationMode() = Settings.Secure.getInt(context.contentResolver, Settings.Secure.LOCATION_MODE, Settings.Secure.LOCATION_MODE_OFF)

    /**
     * Battery estimate. Prefers our own discharge history (first reading after the last unplug is
     * the anchor); falls back to the fuel gauge's charge counter / average current; null if neither
     * gives a believable number.
     */
    private fun remaining(level: Int): String? {
        val now = System.currentTimeMillis()
        var anchorLevel = batteryPrefs.getInt("level", -1)
        var anchorMs = batteryPrefs.getLong("at", 0L)
        if (anchorLevel < 0 || level > anchorLevel) {
            anchorLevel = level
            anchorMs = now
            batteryPrefs.edit().putInt("level", level).putLong("at", now).apply()
        }
        val hours = QuickLogic.hoursLeftFromHistory(anchorLevel, anchorMs, level, now)
            ?: guard(null) {
                QuickLogic.hoursLeftFromCurrent(
                    batteryManager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CHARGE_COUNTER),
                    batteryManager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_AVERAGE)
                        .takeIf { it != 0 && it != Int.MIN_VALUE }
                        ?: batteryManager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW),
                )
            }
        return hours?.let(QuickLogic::formatRemaining)
    }

    // --- toggles ---------------------------------------------------------------

    @Suppress("DEPRECATION")
    fun setWifi(on: Boolean): ToggleResult = attempt { wifiManager.setWifiEnabled(on) }

    @SuppressLint("MissingPermission")
    @Suppress("DEPRECATION")
    fun setBluetooth(on: Boolean): ToggleResult {
        val bt = bluetooth ?: return ToggleResult.UNAVAILABLE
        return attempt { if (on) bt.enable() else bt.disable() }
    }

    /** DND = priority-only (alarms + priority). Needs notification-policy access, else opens its settings. */
    fun setDnd(on: Boolean, launch: (Intent) -> Unit): ToggleResult {
        if (!notifications.isNotificationPolicyAccessGranted) {
            launch(Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS))
            return ToggleResult.OPENED_SETTINGS
        }
        return attempt {
            notifications.setInterruptionFilter(
                if (on) NotificationManager.INTERRUPTION_FILTER_PRIORITY else NotificationManager.INTERRUPTION_FILTER_ALL,
            )
            true
        }
    }

    /** Sound on = ringer normal; off = vibrate. */
    fun setSound(on: Boolean, launch: (Intent) -> Unit): ToggleResult {
        return try {
            audio.ringerMode = if (on) AudioManager.RINGER_MODE_NORMAL else AudioManager.RINGER_MODE_VIBRATE
            ToggleResult.OK
        } catch (e: SecurityException) {
            // While DND is on, ringer changes need policy access too.
            Log.w(TAG, "ringer", e)
            launch(Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS))
            ToggleResult.OPENED_SETTINGS
        }
    }

    /** Needs WRITE_SECURE_SETTINGS (adb pm grant). Off → on restores the last mode we saw (default high accuracy). */
    @Suppress("DEPRECATION")
    fun setLocation(on: Boolean): ToggleResult {
        if (context.checkSelfPermission(Manifest.permission.WRITE_SECURE_SETTINGS) != PackageManager.PERMISSION_GRANTED) {
            return ToggleResult.NEEDS_ADB_GRANT
        }
        return try {
            val cr = context.contentResolver
            val mode = if (on) {
                prefs.getInt("lastLocationMode", Settings.Secure.LOCATION_MODE_HIGH_ACCURACY)
            } else {
                locationMode().takeIf { it != Settings.Secure.LOCATION_MODE_OFF }
                    ?.let { prefs.edit().putInt("lastLocationMode", it).apply() }
                Settings.Secure.LOCATION_MODE_OFF
            }
            if (Settings.Secure.putInt(cr, Settings.Secure.LOCATION_MODE, mode)) ToggleResult.OK else ToggleResult.FAILED
        } catch (e: SecurityException) {
            Log.w(TAG, "location", e)
            ToggleResult.NEEDS_ADB_GRANT
        } catch (e: RuntimeException) {
            Log.w(TAG, "location", e)
            ToggleResult.FAILED
        }
    }

    /** Writes the system brightness (manual mode). Without WRITE_SETTINGS opens its settings page. */
    fun setBrightness(value: Int, launch: (Intent) -> Unit): ToggleResult {
        if (!Settings.System.canWrite(context)) {
            launch(Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS, Uri.parse("package:${context.packageName}")))
            return ToggleResult.OPENED_SETTINGS
        }
        return attempt {
            val cr = context.contentResolver
            Settings.System.putInt(cr, Settings.System.SCREEN_BRIGHTNESS_MODE, Settings.System.SCREEN_BRIGHTNESS_MODE_MANUAL)
            Settings.System.putInt(cr, Settings.System.SCREEN_BRIGHTNESS, value)
        }
    }

    // --- live updates ----------------------------------------------------------

    /** Calls [onChange] on the main thread when any shown state changes. Returns the unregister action. */
    fun watch(onChange: () -> Unit): () -> Unit {
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) = onChange()
        }
        val filter = IntentFilter().apply {
            addAction(WifiManager.WIFI_STATE_CHANGED_ACTION)
            addAction(BluetoothAdapter.ACTION_STATE_CHANGED)
            addAction(NotificationManager.ACTION_INTERRUPTION_FILTER_CHANGED)
            addAction(AudioManager.RINGER_MODE_CHANGED_ACTION)
            addAction(LocationManager.MODE_CHANGED_ACTION)
            @Suppress("DEPRECATION")
            addAction(ConnectivityManager.CONNECTIVITY_ACTION)
            addAction(Intent.ACTION_BATTERY_CHANGED)
        }
        context.registerReceiver(receiver, filter)
        val observer = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean) = onChange()
        }
        context.contentResolver.registerContentObserver(Settings.System.getUriFor(Settings.System.SCREEN_BRIGHTNESS), false, observer)
        return {
            runCatching { context.unregisterReceiver(receiver) }
            context.contentResolver.unregisterContentObserver(observer)
        }
    }

    private inline fun <T> guard(fallback: T, block: () -> T): T =
        try { block() } catch (e: Exception) { Log.w(TAG, "read", e); fallback }

    private inline fun attempt(block: () -> Boolean): ToggleResult =
        try { if (block()) ToggleResult.OK else ToggleResult.FAILED } catch (e: Exception) { Log.w(TAG, "toggle", e); ToggleResult.FAILED }

    private companion object { const val TAG = "OrbitQuick" }
}
