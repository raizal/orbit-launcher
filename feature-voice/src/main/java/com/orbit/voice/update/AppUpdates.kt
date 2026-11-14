package com.orbit.voice.update

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.util.Log
import androidx.core.content.pm.PackageInfoCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.orbit.voice.R
import com.orbit.voice.upload.Discovery
import com.orbit.voice.upload.OrbitClient
import com.orbit.voice.upload.ServerPrefs
import com.orbit.voice.upload.WifiNow
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Over-the-air updates from the companion (docs/voice-notes.md → App updates).
 *
 * No persistent connection: a check runs only at cheap moments — the home Wi-Fi
 * connecting, after an upload batch, every 6 h (WorkManager), and when the user
 * opens the Servers or Update screen. A newer build is downloaded over the
 * Wi-Fi-bound client into `files/update/`, its sha256 checked, and then *offered*
 * (notification + Servers row). Installing is always the user's tap plus
 * Android's own confirmation ([Installer]); nothing installs silently.
 */
object AppUpdates {
    private const val TAG = "OrbitUpdate"
    private const val PERIODIC = "update-periodic"
    private const val CHECK = "update-check"
    private const val CHANNEL = "updates"
    private const val NOTIFICATION_ID = 11

    /** The verified, downloaded update waiting for the user, or null. Main process only. */
    private val _ready = MutableStateFlow<UpdateInfo?>(null)
    val ready: StateFlow<UpdateInfo?> = _ready.asStateFlow()

    private val lock = Mutex()

    /** Call once from Application.onCreate in the main process. */
    fun init(context: Context) {
        val app = context.applicationContext
        val prefs = UpdatePrefs(app)
        // After an update (or a manual adb install) the stored offer may be old news.
        val stored = prefs.ready
        if (stored != null && (!UpdateLogic.isNewer(stored, installedCode(app)) || !apkFile(app, stored).isFile)) {
            Log.i(TAG, "dropping offer ${stored.version_name} (installed ${installedCode(app)})")
            clear(app)
        } else {
            _ready.value = stored
        }
        WorkManager.getInstance(app).enqueueUniquePeriodicWork(
            PERIODIC, ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<UpdateCheckWorker>(6, TimeUnit.HOURS)
                .setConstraints(unmetered())
                .setInitialDelay(1, TimeUnit.HOURS)
                .build(),
        )
        // Fires on every Wi-Fi connect (and once now if Wi-Fi is already up); rate-limited in checkSoon.
        app.getSystemService(ConnectivityManager::class.java).registerNetworkCallback(
            NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build(),
            object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) = checkSoon(app, delaySec = 20) // uploads go first
            },
        )
    }

    /** A cheap moment: check unless one ran in the last 30 min. `force` skips the rate limit. */
    fun checkSoon(context: Context, force: Boolean = false, delaySec: Long = 0) {
        val app = context.applicationContext
        if (!force && !UpdateLogic.dueForCheck(UpdatePrefs(app).lastCheckMs, System.currentTimeMillis())) return
        WorkManager.getInstance(app).enqueueUniqueWork(
            CHECK, ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<UpdateCheckWorker>()
                .setConstraints(unmetered())
                .setInitialDelay(delaySec, TimeUnit.SECONDS)
                .build(),
        )
    }

    sealed interface Check {
        /** [info] is verified on disk and newer than what is installed. */
        data class Ready(val info: UpdateInfo) : Check
        data object UpToDate : Check
        /** Nothing to do right now (not paired / not home / companion not found / network error). */
        data class Skipped(val reason: String) : Check
    }

    /**
     * One check (+ download when newer). Blocking network; runs on IO. Serialized so the
     * worker and an open Update screen never download the same file twice at once.
     */
    suspend fun check(context: Context): Check = withContext(Dispatchers.IO) {
        lock.withLock { checkLocked(context.applicationContext) }
    }

    private suspend fun checkLocked(app: Context): Check {
        val prefs = ServerPrefs(app)
        val pairing = prefs.pairing ?: return Check.Skipped("Not paired")
        val wifi = WifiNow.read(app) ?: return Check.Skipped("Not on Wi-Fi")
        val home = prefs.home
        if (home == null || !wifi.matches(home)) return Check.Skipped("Not home Wi-Fi")
        val client = OrbitClient(wifi.network.socketFactory)
        val ip = Discovery(app, client, prefs).find(wifi, pairing, allowScan = false)
            ?: return Check.Skipped("Companion not found")
        UpdatePrefs(app).lastCheckMs = System.currentTimeMillis()

        val dir = updateDir(app)
        val latestFile = File(dir, "latest.json")
        val info = try {
            client.download(ip, pairing.port, pairing.token, "/v1/app/latest", latestFile)
            UpdateLogic.parse(latestFile.readText()) ?: return Check.Skipped("Companion sent a bad update description")
        } catch (e: OrbitClient.HttpError) {
            if (e.code == 404) return upToDate(app, "nothing published")
            return Check.Skipped("Companion error ${e.code}")
        } catch (e: Exception) {
            Log.w(TAG, "check failed", e)
            return Check.Skipped("Connection lost")
        } finally {
            latestFile.delete()
        }
        if (!UpdateLogic.isNewer(info, installedCode(app))) return upToDate(app, "latest is ${info.version_name}")

        val apk = apkFile(app, info)
        if (!UpdateLogic.verify(apk, info)) {
            dir.listFiles()?.forEach { it.delete() } // older downloads / partial files
            // An older offer's file is gone now: withdraw it until this one is verified.
            if (_ready.value != null) {
                UpdatePrefs(app).ready = null
                _ready.value = null
            }
            Log.i(TAG, "downloading ${info.version_name} (${info.size} bytes)")
            try {
                client.download(ip, pairing.port, pairing.token, info.apk, apk)
            } catch (e: Exception) {
                Log.w(TAG, "download failed", e)
                apk.delete()
                return Check.Skipped("Download failed")
            }
            if (!UpdateLogic.verify(apk, info)) {
                Log.w(TAG, "checksum mismatch for ${info.version_name}; discarded")
                apk.delete()
                return Check.Skipped("Download damaged (checksum) — will retry")
            }
        }
        val isNew = _ready.value?.version_code != info.version_code
        UpdatePrefs(app).ready = info
        _ready.value = info
        if (isNew) notifyReady(app, info)
        Log.i(TAG, "update ready: ${info.version_name}")
        return Check.Ready(info)
    }

    private fun upToDate(app: Context, why: String): Check {
        Log.i(TAG, "up to date ($why)")
        clear(app)
        return Check.UpToDate
    }

    /** Forget the offer and its files (installed, withdrawn, or older than what we run). */
    fun clear(context: Context) {
        val app = context.applicationContext
        UpdatePrefs(app).ready = null
        _ready.value = null
        updateDir(app).listFiles()?.forEach { it.delete() }
        app.getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
    }

    fun installedCode(context: Context): Long =
        PackageInfoCompat.getLongVersionCode(context.packageManager.getPackageInfo(context.packageName, 0))

    fun installedName(context: Context): String =
        context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "?"

    fun apkFile(context: Context, info: UpdateInfo) = File(updateDir(context), "orbit-${info.version_code}.apk")

    private fun updateDir(context: Context) = File(context.filesDir, "update").apply { mkdirs() }

    private fun notifyReady(app: Context, info: UpdateInfo) {
        val nm = app.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "App updates", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(
            app, 0, Intent(app, UpdateActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE,
        )
        nm.notify(
            NOTIFICATION_ID,
            Notification.Builder(app, CHANNEL)
                .setSmallIcon(R.drawable.ic_check)
                .setContentTitle("Orbit update available")
                .setContentText("v${info.version_name} · tap to install")
                .setContentIntent(open)
                .setAutoCancel(true)
                .build(),
        )
    }

    private fun unmetered() = Constraints.Builder().setRequiredNetworkType(NetworkType.UNMETERED).build()
}

/** Periodic / opportunistic check. Never installs; only downloads and offers. */
class UpdateCheckWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val r = AppUpdates.check(applicationContext)
        Log.i("OrbitUpdate", "check: $r")
        // Wi-Fi that just connected may not have an address yet.
        if (r is AppUpdates.Check.Skipped && r.reason == "Not on Wi-Fi" && WifiNow.connecting(applicationContext) && runAttemptCount < 3) {
            return Result.retry()
        }
        return Result.success()
    }
}

/** What is offered, persisted so a restarted process still shows it. */
internal class UpdatePrefs(context: Context) {
    private val prefs = context.getSharedPreferences("update", Context.MODE_PRIVATE)

    var ready: UpdateInfo?
        get() = prefs.getString("ready", null)?.let(UpdateLogic::parse)
        set(v) = prefs.edit().putString("ready", v?.let(UpdateLogic::encode)).apply()

    var lastCheckMs: Long
        get() = prefs.getLong("last_check", 0)
        set(v) = prefs.edit().putLong("last_check", v).apply()
}
