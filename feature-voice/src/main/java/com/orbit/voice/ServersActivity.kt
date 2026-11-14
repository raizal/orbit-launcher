package com.orbit.voice

import android.Manifest
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.lifecycleScope
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import com.orbit.core.CurvedText
import com.orbit.core.OrbitColors
import com.orbit.core.OrbitFonts
import com.orbit.core.RoundScreen
import com.orbit.core.SvgIcon
import com.orbit.voice.update.AppUpdates
import com.orbit.voice.update.UpdateActivity
import com.orbit.voice.update.UpdateInfo
import com.orbit.voice.upload.Discovery
import com.orbit.voice.upload.FoundServer
import com.orbit.voice.upload.OrbitClient
import com.orbit.voice.upload.Pairing
import com.orbit.voice.upload.Progress
import com.orbit.voice.upload.ServerPrefs
import com.orbit.voice.upload.Uploads
import com.orbit.voice.upload.WifiNow
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Choose / pair the companion — `design/VoiceServers.dc.html`. */
class ServersActivity : ComponentActivity() {

    private lateinit var prefs: ServerPrefs
    private var status by mutableStateOf("Scanning home Wi-Fi…")
    private var paired by mutableStateOf<Pairing?>(null)
    private var pairedHost by mutableStateOf<FoundServer?>(null)
    private var others by mutableStateOf<List<FoundServer>>(emptyList())

    private val scanQr = registerForActivityResult(ScanContract()) { result ->
        result.contents?.let(::pair)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true)
        prefs = ServerPrefs(this)
        paired = prefs.pairing
        // Debug builds only: `adb shell am start -n com.orbit/com.orbit.voice.ServersActivity --es pair '<qr json>'`.
        val debuggable = applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0
        if (debuggable) intent.getStringExtra("pair")?.let(::pair)
        // SSID/BSSID are only readable with location permission on Android 8.1.
        if (checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.ACCESS_COARSE_LOCATION), 1)
        }
        setContent {
            val update by AppUpdates.ready.collectAsState()
            RoundScreen {
                ServersScreen(
                    status = status,
                    paired = paired,
                    pairedHost = pairedHost,
                    others = others,
                    update = update,
                    onRefresh = { refresh(byUser = true) },
                    onScanQr = ::startQrScan,
                    onUpdate = { startActivity(Intent(this, UpdateActivity::class.java)) },
                )
            }
        }
        refresh()
    }

    private fun startQrScan() {
        scanQr.launch(
            ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE).setPrompt("Scan the Orbit Notes QR")
                .setBeepEnabled(false).setOrientationLocked(true)
        )
    }

    /** Store the pairing, remember this Wi-Fi as home, and start sending. */
    private fun pair(text: String) {
        val p = ServerPrefs.parsePairing(text)
        if (p == null) {
            status = "Not an Orbit Notes QR"
            return
        }
        val wifi = WifiNow.read(this)
        if (wifi == null) {
            status = "Join home Wi-Fi first"
            return
        }
        prefs.pairing = p
        prefs.home = wifi.toHome()
        prefs.lastIp = null
        paired = p
        Progress.changed() // "sent" is per companion: screens re-read the queue for this one
        Log.i("OrbitUpload", "paired ${p.name} on ${wifi.ssid ?: wifi.subnet}")
        refresh()
    }

    /** [byUser]: the paired row was tapped — also check for an app update now, past the 30-min limit. */
    private fun refresh(byUser: Boolean = false) {
        lifecycleScope.launch {
            val wifi = WifiNow.read(this@ServersActivity)
            if (wifi == null) {
                status = "Not on Wi-Fi"
                return@launch
            }
            val home = prefs.home
            if (home != null && !wifi.matches(home)) {
                status = "Not home Wi-Fi · ${wifi.ssid ?: wifi.subnet}"
                return@launch
            }
            status = "Scanning home Wi-Fi…"
            val p = prefs.pairing
            val client = OrbitClient(wifi.network.socketFactory)
            val found = withContext(Dispatchers.IO) {
                val ip = p?.let { Discovery(this@ServersActivity, client, prefs).find(wifi, it, allowScan = false) }
                val ours = ip?.let { client.health(it, p.port, p.token) }
                // User-opened screen: always scan so unpaired companions show up.
                val all = Discovery(this@ServersActivity, client, prefs).scan(wifi, p?.port ?: 8765, p?.token)
                (ours ?: all.firstOrNull { it.verified }) to all.filter { !it.verified }
            }
            pairedHost = found.first?.also { prefs.lastIp = it.ip }
            others = found.second
            status = "Home Wi-Fi · ${listOfNotNull(found.first).size + found.second.size} found"
            if (pairedHost != null) {
                Uploads.kick(this@ServersActivity, fresh = true)
                AppUpdates.checkSoon(this@ServersActivity, force = byUser) // companion just answered: a cheap moment
            }
        }
    }
}

private const val MONITOR = "M4 4h16v12H4zM8 20h8M12 16v4"
private const val WIFI = "M5 13a10 10 0 0 1 14 0M8.5 16.5a5 5 0 0 1 7 0M2 8.82a15 15 0 0 1 20 0M12 20h.01"
private const val SCAN = "M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 12h10"
private const val CHECK = "M20 6 9 17l-5-5"

@Composable
private fun ServersScreen(
    status: String,
    paired: Pairing?,
    pairedHost: FoundServer?,
    others: List<FoundServer>,
    update: UpdateInfo?,
    onRefresh: () -> Unit,
    onScanQr: () -> Unit,
    onUpdate: () -> Unit,
) {
    Box(Modifier.fillMaxSize()) {
        CurvedText("SEND NOTES TO", top = true, color = OrbitColors.Muted, sizeSp = 13f, letterSpacingPx = 2.5f)
        Row(
            Modifier.offset(y = 72.dp).width(400.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SvgIcon(WIFI, 16.dp, OrbitColors.Info, strokeWidth = 2.2f)
            Spacer(Modifier.width(8.dp))
            BasicText(status, style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 13.sp, color = OrbitColors.Info))
        }
        var top = 104
        if (paired != null) {
            ServerRow(
                top, paired.name,
                pairedHost?.let { "${it.ip} · verified" } ?: "paired · not found yet",
                highlighted = true, onClick = onRefresh,
            )
            top += 74
        }
        // One row fits above the Pair button: an offered update wins over an unpaired scan result.
        if (update != null) {
            ServerRow(top, "Update available", "v${update.version_name} · tap to install", highlighted = false, onClick = onUpdate)
        } else {
            others.firstOrNull()?.let {
                ServerRow(top, "${it.ip}:${it.port}", "Found by scan · not paired", highlighted = false, onClick = onScanQr)
            }
        }
        Box(
            Modifier
                .offset(76.dp, 252.dp)
                .size(248.dp, 56.dp)
                .clip(RoundedCornerShape(50))
                .drawBehind {
                    drawRoundRect(
                        Color(0xFF4A4A46), cornerRadius = CornerRadius(size.height / 2),
                        style = Stroke(2.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(6.dp.toPx(), 4.dp.toPx()))),
                    )
                }
                .clickable(onClick = onScanQr),
            contentAlignment = Alignment.Center,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                SvgIcon(SCAN, 18.dp, OrbitColors.Ink, strokeWidth = 2.2f)
                Spacer(Modifier.width(10.dp))
                BasicText("Pair: scan QR", style = TextStyle(fontFamily = OrbitFonts.Inter, fontWeight = FontWeight.SemiBold, fontSize = 15.sp, color = OrbitColors.Ink))
            }
        }
        BasicText(
            "Home Wi-Fi only · last IP → mDNS → scan",
            Modifier.offset(y = 324.dp).width(400.dp),
            style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 12.sp, color = OrbitColors.Muted, textAlign = TextAlign.Center),
        )
    }
}

@Composable
private fun ServerRow(top: Int, title: String, sub: String, highlighted: Boolean, onClick: () -> Unit) {
    Row(
        Modifier
            .offset(56.dp, top.dp)
            .size(288.dp, 64.dp)
            .clip(RoundedCornerShape(50))
            .background(if (highlighted) OrbitColors.Surface else Color(0xFF141413))
            .then(if (highlighted) Modifier.border(2.dp, OrbitColors.Accent, RoundedCornerShape(50)) else Modifier)
            .clickable(onClick = onClick)
            .padding(start = if (highlighted) 8.dp else 10.dp, end = 18.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(
            Modifier.size(44.dp).clip(CircleShape).background(if (highlighted) OrbitColors.Accent else Color(0xFF232321)),
            contentAlignment = Alignment.Center,
        ) {
            SvgIcon(MONITOR, 20.dp, if (highlighted) OrbitColors.OnAccent else OrbitColors.Ink, strokeWidth = if (highlighted) 2.4f else 2.2f)
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            BasicText(title, style = TextStyle(fontFamily = OrbitFonts.Grotesk, fontWeight = FontWeight.SemiBold, fontSize = 17.sp, color = OrbitColors.Ink), maxLines = 1)
            BasicText(sub, style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 12.sp, color = OrbitColors.Muted), maxLines = 1)
        }
        if (highlighted) SvgIcon(CHECK, 18.dp, OrbitColors.Accent, strokeWidth = 2.6f)
    }
}
