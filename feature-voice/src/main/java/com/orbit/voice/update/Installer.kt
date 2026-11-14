package com.orbit.voice.update

import android.app.PendingIntent
import android.content.ActivityNotFoundException
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import com.orbit.voice.NoteStore
import com.orbit.voice.RecorderState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File
import java.util.concurrent.ConcurrentHashMap

/**
 * Installs a verified update with [PackageInstaller]. Android shows its own
 * confirmation; we never install silently. Installing replaces the app and kills
 * every Orbit process (including `:voice`), so it is refused while a recording is
 * being written or a Talk turn is in flight ([busyReason]).
 *
 * Same package, same signing key → the update keeps all data (files, prefs, queue).
 */
object Installer {
    private const val TAG = "OrbitUpdate"

    /** Live Talk screens report whether a turn is in flight (main process). */
    private val talkProbes = ConcurrentHashMap<Any, () -> Boolean>()

    fun registerTalk(owner: Any, inFlight: () -> Boolean) {
        talkProbes[owner] = inFlight
    }

    fun unregisterTalk(owner: Any) {
        talkProbes.remove(owner)
    }

    /** Why an install must wait now, or null if it is safe. */
    fun busyReason(context: Context): String? {
        val notes = NoteStore(context).dir.listFiles()?.map { it.name to it.lastModified() }.orEmpty()
        if (RecorderState.readMarker(context) != null || UpdateLogic.recordingActive(notes, System.currentTimeMillis())) {
            return "Recording in progress"
        }
        if (talkProbes.values.any { runCatching(it).getOrDefault(true) }) return "Talk is busy"
        return null
    }

    /** Android 8: the user must allow "Install unknown apps" for Orbit once. */
    fun canInstall(context: Context): Boolean = context.packageManager.canRequestPackageInstalls()

    /** Opens the "Install unknown apps → Orbit" switch (falls back to Orbit's app info). */
    fun openInstallPermission(context: Context) {
        val pkg = Uri.parse("package:${context.packageName}")
        try {
            context.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, pkg))
        } catch (_: ActivityNotFoundException) {
            context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, pkg))
        }
    }

    sealed interface Status {
        data object Idle : Status
        /** Session committed; Android's confirm dialog is (about to be) on screen. */
        data object Confirming : Status
        data class Failed(val message: String) : Status
    }

    private val _status = MutableStateFlow<Status>(Status.Idle)
    val status: StateFlow<Status> = _status.asStateFlow()

    /**
     * Streams [apk] into an install session and commits it; the result (and the
     * confirmation request) arrives at [InstallResultReceiver]. Blocking I/O.
     * Callers check [busyReason] and [canInstall] first; this re-checks busy.
     */
    fun install(context: Context, apk: File, info: UpdateInfo) {
        val app = context.applicationContext
        busyReason(app)?.let { error(it) }
        check(UpdateLogic.verify(apk, info)) { "update file is damaged" }
        val pi = app.packageManager.packageInstaller
        // A previous attempt the user walked away from must not linger (and confirm later at a bad moment).
        pi.mySessions.forEach { runCatching { pi.abandonSession(it.sessionId) } }
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(app.packageName)
            setSize(apk.length())
        }
        val id = pi.createSession(params)
        try {
            pi.openSession(id).use { session ->
                session.openWrite("orbit.apk", 0, apk.length()).use { out ->
                    apk.inputStream().use { it.copyTo(out) }
                    session.fsync(out)
                }
                // Mutable: the installer fills in the status extras.
                val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                    (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
                val sender = PendingIntent.getBroadcast(
                    app, id, Intent(app, InstallResultReceiver::class.java).putExtra(EXTRA_VERSION, info.version_name), flags,
                ).intentSender
                _status.value = Status.Confirming
                Log.i(TAG, "committing session $id for ${info.version_name}")
                session.commit(sender)
            }
        } catch (e: Exception) {
            runCatching { pi.abandonSession(id) }
            _status.value = Status.Failed("Could not start the install")
            throw e
        }
    }

    internal fun onResult(context: Context, intent: Intent) {
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
        Log.i(TAG, "install status $status ($message) for ${intent.getStringExtra(EXTRA_VERSION)}")
        when (status) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                context.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            // Usually never seen: replacing the app kills this process; the new one starts clean.
            PackageInstaller.STATUS_SUCCESS -> {
                _status.value = Status.Idle
                AppUpdates.clear(context)
            }
            PackageInstaller.STATUS_FAILURE_ABORTED -> _status.value = Status.Failed("Install cancelled")
            PackageInstaller.STATUS_FAILURE_CONFLICT, PackageInstaller.STATUS_FAILURE_INCOMPATIBLE ->
                _status.value = Status.Failed("Not installable over this build (signing key?)")
            PackageInstaller.STATUS_FAILURE_STORAGE -> _status.value = Status.Failed("Not enough storage")
            else -> _status.value = Status.Failed("Install failed${message?.let { ": $it" } ?: ""}")
        }
    }

    fun resetStatus() {
        _status.value = Status.Idle
    }

    private const val EXTRA_VERSION = "orbit_version"
}

/** PackageInstaller session results (explicit, not exported). */
class InstallResultReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) = Installer.onResult(context, intent)
}
