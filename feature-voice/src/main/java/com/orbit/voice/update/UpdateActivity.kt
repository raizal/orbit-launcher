package com.orbit.voice.update

import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.lifecycleScope
import com.orbit.core.CurvedText
import com.orbit.core.OrbitColors
import com.orbit.core.OrbitFonts
import com.orbit.core.RoundScreen
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * "Update available — vX": shows the offered build and installs it on tap
 * (docs/voice-notes.md → App updates). Opened from the update notification and
 * the Servers screen row. With nothing offered yet, opening it checks the companion right away.
 */
class UpdateActivity : ComponentActivity() {

    private var checking by mutableStateOf(false)
    private var checkNote by mutableStateOf<String?>(null)
    private var busy by mutableStateOf<String?>(null)
    private var allowed by mutableStateOf(true)
    private var working by mutableStateOf(false)
    private var poll: Job? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val installed = AppUpdates.installedName(this)
        setContent {
            val ready by AppUpdates.ready.collectAsState()
            val status by Installer.status.collectAsState()
            RoundScreen {
                UpdateScreen(
                    installed = installed,
                    ready = ready,
                    view = viewOf(ready, status),
                    onAction = { onAction(ready) },
                )
            }
        }
        Installer.resetStatus()
        // A ready offer was verified when it was downloaded; only look for one when there is none.
        if (AppUpdates.ready.value == null) check()
    }

    override fun onResume() {
        super.onResume()
        allowed = Installer.canInstall(this) // back from the "Install unknown apps" switch
        // Recording / Talk can start and end while we are open: keep the button honest.
        poll = lifecycleScope.launch {
            while (isActive) {
                busy = withContext(Dispatchers.IO) { Installer.busyReason(this@UpdateActivity) }
                delay(1_000)
            }
        }
    }

    override fun onPause() {
        poll?.cancel()
        super.onPause()
    }

    private fun check() {
        if (checking) return
        checking = true
        lifecycleScope.launch {
            checkNote = when (val r = AppUpdates.check(this@UpdateActivity)) {
                is AppUpdates.Check.Ready -> null
                AppUpdates.Check.UpToDate -> "Orbit is up to date"
                is AppUpdates.Check.Skipped -> r.reason
            }
            checking = false
        }
    }

    private fun onAction(ready: UpdateInfo?) {
        when {
            ready == null -> check()
            !allowed -> Installer.openInstallPermission(this)
            busy != null || working -> Unit
            else -> install(ready)
        }
    }

    private fun install(info: UpdateInfo) {
        working = true
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { Installer.install(this@UpdateActivity, AppUpdates.apkFile(this@UpdateActivity, info), info) }
            } catch (e: Exception) {
                Log.w("OrbitUpdate", "install failed to start", e)
                busy = withContext(Dispatchers.IO) { Installer.busyReason(this@UpdateActivity) }
                val damaged = withContext(Dispatchers.IO) { !UpdateLogic.verify(AppUpdates.apkFile(this@UpdateActivity, info), info) }
                if (busy == null && damaged) {
                    // Damaged / missing file: drop it and fetch again.
                    AppUpdates.clear(this@UpdateActivity)
                    checkNote = "Update file damaged — downloading again"
                    check()
                }
            } finally {
                working = false
            }
        }
    }

    private data class View(val status: String, val statusColor: Color, val action: String, val enabled: Boolean)

    private fun viewOf(ready: UpdateInfo?, status: Installer.Status): View = when {
        ready == null -> View(
            if (checking) "Checking the companion…" else checkNote ?: "Orbit is up to date",
            if (checking) OrbitColors.Info else OrbitColors.Muted,
            "Check now", !checking,
        )
        !allowed -> View("Allow Orbit to install apps once: switch it on, then come back", OrbitColors.Accent, "Allow installs", true)
        busy != null -> View("$busy — install when it ends", OrbitColors.Rec, "Install", false)
        working -> View("Preparing…", OrbitColors.Info, "Install", false)
        status is Installer.Status.Confirming -> View("Confirm on the next screen", OrbitColors.Info, "Install", true)
        status is Installer.Status.Failed -> View(status.message, OrbitColors.Rec, "Try again", true)
        else -> View("Ready · your notes and settings stay", OrbitColors.Info, "Install", true)
    }

    @Composable
    private fun UpdateScreen(installed: String, ready: UpdateInfo?, view: View, onAction: () -> Unit) {
        Box(Modifier.size(400.dp)) {
            CurvedText(if (ready != null) "UPDATE AVAILABLE" else "ORBIT UPDATES", top = true, color = OrbitColors.Muted, sizeSp = 13f, letterSpacingPx = 2.5f)
            val (main, sub) = (ready?.version_name ?: installed).split('-', limit = 2).let { it[0] to it.getOrNull(1) }
            centered(if (ready != null) "Installed $installed" else "Installed", 88, 13, OrbitColors.Muted)
            centered("v$main", 116, 40, if (ready != null) OrbitColors.Accent else OrbitColors.Ink, OrbitFonts.Grotesk, FontWeight.SemiBold)
            centered(sub ?: " ", 166, 13, OrbitColors.Muted)
            BasicText(
                view.status,
                Modifier.offset(72.dp, 196.dp).width(256.dp),
                style = TextStyle(fontFamily = OrbitFonts.Inter, fontSize = 14.sp, color = view.statusColor, textAlign = TextAlign.Center),
                maxLines = 3,
            )
            Box(
                Modifier
                    .offset(76.dp, 268.dp)
                    .size(248.dp, 72.dp)
                    .clip(RoundedCornerShape(50))
                    .background(if (view.enabled) OrbitColors.Accent else OrbitColors.Track)
                    .clickable(enabled = view.enabled, onClick = onAction),
                contentAlignment = Alignment.Center,
            ) {
                BasicText(
                    view.action,
                    style = TextStyle(
                        fontFamily = OrbitFonts.Grotesk, fontWeight = FontWeight.SemiBold, fontSize = 20.sp,
                        color = if (view.enabled) OrbitColors.OnAccent else OrbitColors.Muted,
                    ),
                )
            }
        }
    }

    @Composable
    private fun centered(
        text: String, top: Int, size: Int, color: Color,
        font: androidx.compose.ui.text.font.FontFamily = OrbitFonts.Inter, weight: FontWeight = FontWeight.Normal,
    ) = BasicText(
        text,
        Modifier.offset(y = top.dp).width(400.dp),
        style = TextStyle(fontFamily = font, fontWeight = weight, fontSize = size.sp, color = color, textAlign = TextAlign.Center),
        maxLines = 1,
    )
}
