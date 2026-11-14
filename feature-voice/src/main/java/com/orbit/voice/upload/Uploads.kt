package com.orbit.voice.upload

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.orbit.voice.NoteStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Upload scheduling. One queue worker sends every unsent note, oldest first, so
 * the slow watch never runs parallel uploads. It is (re)triggered by: a saved
 * note, the home Wi-Fi connecting (while our process lives), and a 15-min
 * periodic sweep (covers the process being dead).
 */
object Uploads {
    private const val QUEUE = "upload-queue"
    private const val SWEEP = "upload-sweep"
    private const val CLEANUP = "sent-cleanup"
    const val KEEP_SENT_MS = 7L * 24 * 60 * 60 * 1000

    /** Call once from Application.onCreate in the main process. */
    fun init(context: Context) {
        val wm = WorkManager.getInstance(context)
        wm.enqueueUniquePeriodicWork(
            SWEEP, ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<SweepWorker>(15, TimeUnit.MINUTES).setConstraints(unmetered()).build(),
        )
        wm.enqueueUniquePeriodicWork(
            CLEANUP, ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<CleanupWorker>(1, TimeUnit.DAYS).build(),
        )
        val cm = context.getSystemService(ConnectivityManager::class.java)
        cm.registerNetworkCallback(
            NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build(),
            object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) = kick(context, fresh = true)
            },
        )
        kick(context)
    }

    /**
     * Runs the queue now if anything is unsent. `fresh` replaces a waiting
     * (backed-off) run so a home Wi-Fi connect uploads right away.
     */
    fun kick(context: Context, fresh: Boolean = false) {
        val req = OneTimeWorkRequestBuilder<UploadWorker>()
            .setConstraints(unmetered())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            QUEUE, if (fresh) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.KEEP, req,
        )
    }

    private fun unmetered() = Constraints.Builder().setRequiredNetworkType(NetworkType.UNMETERED).build()
}

/** Sends the queue. Never deletes anything. */
class UploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val queue = Queue(applicationContext)
        val prefs = ServerPrefs(applicationContext)
        val pending = queue.unsent()
        if (pending.isEmpty()) return@withContext Result.success()
        val pairing = prefs.pairing
            ?: return@withContext Result.success().also { Log.i(TAG, "not paired; ${pending.size} waiting") }
        val wifi = WifiNow.read(applicationContext)
        if (wifi == null) {
            // Wi-Fi that just connected may not have an address yet: try again shortly.
            return@withContext if (WifiNow.connecting(applicationContext)) {
                Log.i(TAG, "Wi-Fi has no IPv4 yet; retry")
                Result.retry()
            } else {
                Log.i(TAG, "no Wi-Fi; ${pending.size} waiting")
                Result.success()
            }
        }
        val home = prefs.home
        if (home == null || !wifi.matches(home)) {
            Log.i(TAG, "not on home Wi-Fi (${wifi.ssid}); waiting")
            return@withContext Result.success() // the Wi-Fi callback / sweep will try again
        }
        val client = OrbitClient(wifi.network.socketFactory)
        val ip = Discovery(applicationContext, client, prefs).find(wifi, pairing)
            ?: return@withContext Result.retry().also { Log.i(TAG, "server not found") }

        var transient = false
        for (note in pending) {
            val meta = note.meta
            val metaFile = File(NoteStore(applicationContext).dir, note.stem + ".json")
            if (meta == null) {
                queue.markFailed(note.stem, "Missing sidecar")
                continue
            }
            try {
                Progress.set(note.stem, 0f)
                Progress.changed()
                if (!client.has(ip, pairing.port, pairing.token, meta.id)) {
                    val sha = client.put(ip, pairing.port, pairing.token, meta.id, note.audio, metaFile) {
                        Progress.set(note.stem, it)
                    }
                    if (!sha.equals(meta.sha256, ignoreCase = true)) {
                        queue.markFailed(note.stem, "Checksum mismatch")
                        transient = true
                        continue
                    }
                }
                // HEAD 200: the server stored this id earlier, and it only stores after
                // checking the audio against meta.sha256 — so it is safely there.
                prefs.lastIp = ip
                queue.markSent(note.stem)
                Log.i(TAG, "sent ${note.stem}")
            } catch (e: OrbitClient.HttpError) {
                val msg = when (e.code) {
                    401, 403 -> "Not authorized — pair again"
                    409 -> "Server has a different file"
                    422 -> "Server rejected checksum"
                    else -> "Server error ${e.code}"
                }
                queue.markFailed(note.stem, msg)
                if (e.code >= 500) transient = true
            } catch (e: Exception) {
                Log.w(TAG, "upload ${note.stem} failed", e)
                queue.markFailed(note.stem, "Connection lost")
                transient = true
            } finally {
                Progress.done(note.stem)
                Progress.changed()
            }
        }
        if (transient) Result.retry() else Result.success()
    }

    private companion object {
        const val TAG = "OrbitUpload"
    }
}

/** Periodic safety net: re-run the queue (failed notes keep retrying). */
class SweepWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        if (Queue(applicationContext).unsent().isNotEmpty()) Uploads.kick(applicationContext)
        return Result.success()
    }
}

/** Deletes notes the server confirmed more than 7 days ago. Unsent notes are never touched. */
class CleanupWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val n = Queue(applicationContext).deleteSentOlderThan(Uploads.KEEP_SENT_MS)
        if (n > 0) Log.i("OrbitUpload", "cleanup deleted $n sent notes")
        return Result.success()
    }
}

/** The `:voice` process tells the main process a note was saved (explicit broadcast). */
class NoteSavedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        Progress.changed()
        Uploads.kick(context)
    }

    companion object {
        fun notify(context: Context) =
            context.sendBroadcast(Intent(context, NoteSavedReceiver::class.java))
    }
}
