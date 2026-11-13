package com.orbit.probe

import android.Manifest
import android.content.Context
import android.content.Intent
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.media.MediaPlayer
import android.net.ConnectivityManager
import android.net.wifi.WifiManager
import android.os.Bundle
import android.util.Log
import android.view.KeyEvent
import android.view.MotionEvent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.io.File

/**
 * Phase 0 device probe. Everything is logged to logcat under tag "OrbitProbe"
 * (read with `adb logcat -s OrbitProbe`) and the last lines are shown on screen.
 */
class ProbeActivity : ComponentActivity(), SensorEventListener {

    private val lines = mutableStateListOf<String>()
    private lateinit var sensorManager: SensorManager
    private var rotary: Sensor? = null
    private var player: MediaPlayer? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Probe only: lets adb-driven tests run while the secure lock screen is up.
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        rotary = sensorManager.getSensorList(Sensor.TYPE_ALL)
            .firstOrNull { it.stringType == ROTARY_STRING_TYPE || it.type == ROTARY_TYPE }
        requestPermissions(
            arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.ACCESS_COARSE_LOCATION), 1
        )
        setContent { ProbeScreen() }
        logDisplay()
        handleCommand(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleCommand(intent)
    }

    /** adb hook: `am start -n com.orbit/.probe.ProbeActivity --es cmd rec_m4a|rec_aac|stop` */
    private fun handleCommand(intent: Intent?) {
        when (intent?.getStringExtra("cmd")) {
            "rec_m4a" -> record(ProbeRecordService.FORMAT_M4A)
            "rec_aac" -> record(ProbeRecordService.FORMAT_AAC)
            "stop" -> stopRecording()
            "net" -> logNetwork()
            "sensors" -> logSensors()
        }
    }

    override fun onResume() {
        super.onResume()
        rotary?.let { sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME) }
        log("rotary sensor: ${rotary?.let { "${it.name} type=${it.type} '${it.stringType}'" } ?: "NOT FOUND"}")
    }

    override fun onPause() {
        sensorManager.unregisterListener(this)
        super.onPause()
    }

    override fun onDestroy() {
        player?.release()
        super.onDestroy()
    }

    // --- Inputs -------------------------------------------------------------

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        val action = if (event.action == KeyEvent.ACTION_DOWN) "DOWN" else "UP"
        log(
            "key ${KeyEvent.keyCodeToString(event.keyCode)} $action scan=${event.scanCode} " +
                "repeat=${event.repeatCount} long=${event.isLongPress} src=0x${event.source.toString(16)} dev=${event.deviceId}"
        )
        // Consume everything so BACK/F9 does not close the probe while testing.
        return true
    }

    override fun dispatchGenericMotionEvent(ev: MotionEvent): Boolean {
        log(
            "motion ${MotionEvent.actionToString(ev.action)} src=0x${ev.source.toString(16)} " +
                "scroll=${ev.getAxisValue(MotionEvent.AXIS_SCROLL)} vscroll=${ev.getAxisValue(MotionEvent.AXIS_VSCROLL)} " +
                "hscroll=${ev.getAxisValue(MotionEvent.AXIS_HSCROLL)}"
        )
        return super.dispatchGenericMotionEvent(ev)
    }

    override fun onSensorChanged(event: SensorEvent) {
        log("rotary values=${event.values.joinToString(",")} acc=${event.accuracy} t=${event.timestamp / 1_000_000}ms")
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    // --- Probes -------------------------------------------------------------

    private fun logDisplay() {
        val m = resources.displayMetrics
        log(
            "display ${m.widthPixels}x${m.heightPixels}px density=${m.density} dpi=${m.densityDpi} " +
                "xdpi=${m.xdpi} sw=${resources.configuration.smallestScreenWidthDp}dp isRound=${resources.configuration.isScreenRound}"
        )
    }

    private fun logSensors() {
        sensorManager.getSensorList(Sensor.TYPE_ALL).forEach {
            log("sensor ${it.type} '${it.stringType}' ${it.name} / ${it.vendor} wakeUp=${it.isWakeUpSensor}")
        }
    }

    @Suppress("DEPRECATION")
    private fun logNetwork() {
        val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val info = wifi.connectionInfo
        log("wifi ssid=${info?.ssid} bssid=${info?.bssid} rssi=${info?.rssi} freq=${info?.frequency}")
        val cm = getSystemService(ConnectivityManager::class.java)
        val net = cm.activeNetwork
        val caps = net?.let { cm.getNetworkCapabilities(it) }
        val lp = net?.let { cm.getLinkProperties(it) }
        log("net caps=$caps")
        log("net addrs=${lp?.linkAddresses} routes=${lp?.routes} dns=${lp?.dnsServers} metered=${cm.isActiveNetworkMetered}")
    }

    private fun record(format: String) {
        startForegroundService(
            Intent(this, ProbeRecordService::class.java)
                .setAction(ProbeRecordService.ACTION_START)
                .putExtra(ProbeRecordService.EXTRA_FORMAT, format)
        )
        log("requested record $format")
    }

    private fun stopRecording() {
        startService(Intent(this, ProbeRecordService::class.java).setAction(ProbeRecordService.ACTION_STOP))
        log("requested stop")
    }

    private fun playLatest() {
        val latest = File(getExternalFilesDir(null), "probe").listFiles()?.maxByOrNull { it.lastModified() }
        if (latest == null) {
            log("no probe files")
            return
        }
        player?.release()
        player = try {
            MediaPlayer().apply {
                setDataSource(latest.absolutePath)
                prepare()
                start()
            }.also { log("playing ${latest.name} ${latest.length()}B duration=${it.duration}ms") }
        } catch (e: Exception) {
            log("play FAILED ${latest.name} ${latest.length()}B: $e")
            null
        }
    }

    private fun log(msg: String) {
        Log.i(ProbeRecordService.TAG, msg)
        lines.add(0, msg)
        if (lines.size > 60) lines.removeAt(lines.lastIndex)
    }

    // --- UI -----------------------------------------------------------------

    @Composable
    private fun ProbeScreen() {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 40.dp, vertical = 36.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            ButtonRow("Rec m4a" to { record(ProbeRecordService.FORMAT_M4A) }, "Rec aac" to { record(ProbeRecordService.FORMAT_AAC) })
            ButtonRow("Stop" to ::stopRecording, "Play" to ::playLatest)
            ButtonRow("Sensors" to ::logSensors, "Net" to ::logNetwork)
            lines.forEach { Text(it, color = Color(0xFFF2F1EC), fontSize = 10.sp, lineHeight = 12.sp) }
        }
    }

    @Composable
    private fun ButtonRow(vararg buttons: Pair<String, () -> Unit>) {
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            buttons.forEach { (label, onClick) -> Button(onClick = onClick) { Text(label, fontSize = 11.sp) } }
        }
    }

    private companion object {
        const val ROTARY_STRING_TYPE = "android.sensor.rotary_encoder"
        const val ROTARY_TYPE = 36
    }
}
