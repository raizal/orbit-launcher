package com.orbit.voice.upload

import android.annotation.SuppressLint
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.net.Inet4Address
import java.security.MessageDigest

/** What the companion's pairing QR contains (no IP: discovery finds it). */
@Serializable
data class Pairing(val name: String, val port: Int = 8765, val token: String) {
    /**
     * Identifies this companion without revealing its token: first 16 hex of
     * sha256(name + "\n" + token). Recorded with every "sent" so a note sent to
     * one companion never counts as sent to another (docs/voice-notes.md).
     * The port is left out: the same companion moved to another port is the same server.
     */
    val fingerprint: String get() = fingerprintOf(name, token)

    companion object {
        fun fingerprintOf(name: String, token: String): String =
            MessageDigest.getInstance("SHA-256").digest("$name\n$token".toByteArray(Charsets.UTF_8))
                .joinToString("") { "%02x".format(it) }.take(16)
    }
}

/**
 * The paired companion and the "home" Wi-Fi it lives on. Plain private prefs
 * (EncryptedSharedPreferences is deprecated); acceptable for a home LAN token.
 */
class ServerPrefs(context: Context) {
    private val prefs = context.getSharedPreferences("server", Context.MODE_PRIVATE)

    var pairing: Pairing?
        get() = prefs.getString("pairing", null)?.let { runCatching { JSON.decodeFromString(Pairing.serializer(), it) }.getOrNull() }
        set(v) = prefs.edit().putString("pairing", v?.let { JSON.encodeToString(Pairing.serializer(), it) }).apply()

    var lastIp: String?
        get() = prefs.getString("last_ip", null)
        set(v) = prefs.edit().putString("last_ip", v).apply()

    var lastScanMs: Long
        get() = prefs.getLong("last_scan", 0)
        set(v) = prefs.edit().putLong("last_scan", v).apply()

    var home: HomeWifi?
        get() = prefs.getString("home", null)?.let { runCatching { JSON.decodeFromString(HomeWifi.serializer(), it) }.getOrNull() }
        set(v) = prefs.edit().putString("home", v?.let { JSON.encodeToString(HomeWifi.serializer(), it) }).apply()

    fun unpair() = prefs.edit().clear().apply()

    companion object {
        private val JSON = Json { ignoreUnknownKeys = true }

        /** Parses a pairing QR / adb payload; null if it is not ours. */
        fun parsePairing(text: String): Pairing? =
            runCatching { JSON.decodeFromString(Pairing.serializer(), text) }.getOrNull()
                ?.takeIf { it.token.length >= 16 && it.name.isNotBlank() && it.port in 1..65535 }
    }
}

/** Home network fingerprint. SSID/BSSID need location; gateway+subnet is the fallback. */
@Serializable
data class HomeWifi(val ssid: String?, val bssid: String?, val gateway: String?, val subnet: String?)

/** Current Wi-Fi as seen by the app. */
data class WifiNow(val network: Network, val ssid: String?, val bssid: String?, val ip: Inet4Address, val prefix: Int, val gateway: String?) {
    val subnet: String get() = subnetOf(ip, prefix)

    fun toHome() = HomeWifi(ssid, bssid, gateway, subnet)

    fun matches(home: HomeWifi): Boolean = when {
        bssid != null && home.bssid != null && bssid.equals(home.bssid, ignoreCase = true) -> true
        ssid != null && home.ssid != null -> ssid == home.ssid
        // Location off → no SSID: fall back to same gateway and subnet.
        else -> gateway != null && gateway == home.gateway && subnet == home.subnet
    }

    companion object {
        @SuppressLint("MissingPermission")
        @Suppress("DEPRECATION")
        fun read(context: Context): WifiNow? {
            val cm = context.getSystemService(ConnectivityManager::class.java)
            val net = cm.allNetworks.firstOrNull { n ->
                cm.getNetworkCapabilities(n)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
            } ?: return null
            val lp = cm.getLinkProperties(net) ?: return null
            val la = lp.linkAddresses.firstOrNull { it.address is Inet4Address } ?: return null
            val gw = lp.routes.firstOrNull { it.isDefaultRoute && it.gateway is Inet4Address }?.gateway?.hostAddress
            val info = (context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager).connectionInfo
            val ssid = info?.ssid?.removeSurrounding("\"")?.takeIf { it.isNotBlank() && it != "<unknown ssid>" }
            val bssid = info?.bssid?.takeIf { it != "02:00:00:00:00:00" }
            return WifiNow(net, ssid, bssid, la.address as Inet4Address, la.prefixLength, gw)
        }

        /** A Wi-Fi network exists but [read] could not use it yet. */
        fun connecting(context: Context): Boolean {
            val cm = context.getSystemService(ConnectivityManager::class.java)
            return cm.allNetworks.any { cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true }
        }

        fun subnetOf(ip: Inet4Address, prefix: Int): String {
            val b = ip.address
            val v = (b[0].toInt() and 0xff shl 24) or (b[1].toInt() and 0xff shl 16) or (b[2].toInt() and 0xff shl 8) or (b[3].toInt() and 0xff)
            val mask = if (prefix == 0) 0 else -1 shl (32 - prefix)
            val n = v and mask
            return "${n ushr 24 and 0xff}.${n ushr 16 and 0xff}.${n ushr 8 and 0xff}.${n and 0xff}/$prefix"
        }
    }
}
