package com.orbit.voice.upload

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

/** A host that answered /v1/health. `verified` = its proof matched our token. */
data class FoundServer(val ip: String, val port: Int, val name: String, val verified: Boolean)

/**
 * Finds the paired companion on the current Wi-Fi:
 * last known IP → mDNS (`_orbitnotes._tcp`, ~3 s) → /24 scan (≤ once per 10 min).
 * Only a server whose HMAC proof matches is ever returned by [find].
 */
class Discovery(private val context: Context, private val client: OrbitClient, private val prefs: ServerPrefs) {

    suspend fun find(wifi: WifiNow, pairing: Pairing, allowScan: Boolean = true): String? {
        prefs.lastIp?.let { ip ->
            if (client.health(ip, pairing.port, pairing.token)?.verified == true) return ip
        }
        mdns(pairing.name)?.let { ip ->
            if (client.health(ip, pairing.port, pairing.token)?.verified == true) return ip.also { prefs.lastIp = it }
        }
        val now = System.currentTimeMillis()
        if (!allowScan || now - prefs.lastScanMs < SCAN_INTERVAL_MS) return null
        prefs.lastScanMs = now
        return scan(wifi, pairing.port, pairing.token).firstOrNull { it.verified }?.ip?.also { prefs.lastIp = it }
    }

    /** Every host on the /24 answering on [port]. With a token, `verified` tells ours apart. */
    suspend fun scan(wifi: WifiNow, port: Int, token: String?): List<FoundServer> = withContext(Dispatchers.IO) {
        val base = wifi.ip.address.let { "${it[0].toInt() and 0xff}.${it[1].toInt() and 0xff}.${it[2].toInt() and 0xff}." }
        val own = wifi.ip.hostAddress
        val gate = Semaphore(32)
        coroutineScope {
            (1..254).map { "$base$it" }.filter { it != own }.map { ip ->
                async { gate.withPermit { client.health(ip, port, token, connectTimeoutMs = 600) } }
            }.awaitAll().filterNotNull()
        }
    }

    /** Resolves the companion by mDNS; NsdManager is flaky on Android 8, so give it ~3 s. */
    private suspend fun mdns(name: String): String? = withTimeoutOrNull(3_000) {
        val nsd = context.getSystemService(NsdManager::class.java)
        val service = suspendCancellableCoroutine<NsdServiceInfo?> { cont ->
            val listener = object : NsdManager.DiscoveryListener {
                override fun onServiceFound(info: NsdServiceInfo) {
                    // Instance name is the companion's name; TXT isn't exposed by NsdManager on API 27.
                    if (info.serviceName.equals(name, ignoreCase = true) && cont.isActive) cont.resume(info)
                }
                override fun onStartDiscoveryFailed(t: String, e: Int) { if (cont.isActive) cont.resume(null) }
                override fun onDiscoveryStarted(t: String) = Unit
                override fun onDiscoveryStopped(t: String) = Unit
                override fun onServiceLost(info: NsdServiceInfo) = Unit
                override fun onStopDiscoveryFailed(t: String, e: Int) = Unit
            }
            nsd.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, listener)
            cont.invokeOnCancellation { runCatching { nsd.stopServiceDiscovery(listener) } }
        } ?: return@withTimeoutOrNull null
        suspendCancellableCoroutine<String?> { cont ->
            nsd.resolveService(service, object : NsdManager.ResolveListener {
                override fun onServiceResolved(info: NsdServiceInfo) {
                    if (cont.isActive) cont.resume((info.host as? java.net.Inet4Address)?.hostAddress)
                }
                override fun onResolveFailed(info: NsdServiceInfo, e: Int) { if (cont.isActive) cont.resume(null) }
            })
        }
    }.also { Log.i(TAG, "mdns → $it") }

    companion object {
        private const val TAG = "OrbitUpload"
        private const val SERVICE_TYPE = "_orbitnotes._tcp"
        private const val SCAN_INTERVAL_MS = 10 * 60 * 1000L
    }
}
