package com.orbit.voice.inbox

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
import android.os.VibrationEffect
import android.os.Vibrator
import android.util.Log
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
import com.orbit.voice.RecorderState
import com.orbit.voice.talk.TalkActivity
import com.orbit.voice.upload.Discovery
import com.orbit.voice.upload.OrbitClient
import com.orbit.voice.upload.ServerPrefs
import com.orbit.voice.upload.WifiNow
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.concurrent.TimeUnit
import javax.net.SocketFactory

/**
 * Orbit Inbox on the watch (docs/inbox.md). Pull only: the watch asks the companion
 * `GET /v1/inbox?since=<cursor>` at cheap moments — home Wi-Fi connects, after uploads,
 * when the launcher face comes on (≥ 2 min apart), a 15-min unmetered periodic check, and
 * every ~10 s while Talk or the Inbox screen is open. No connection is kept open.
 *
 * Runs in the main process only (the `:voice` recording process never touches it).
 */
object InboxSync {
    private const val TAG = "OrbitInbox"
    private const val ONCE = "inbox-check"
    private const val PERIODIC = "inbox-periodic"
    const val FACE_MIN_INTERVAL_MS = 2 * 60_000L

    private val _state = MutableStateFlow(InboxState())
    /** What the Inbox / Talk screens show. Loaded from disk by [init] or the first [load]. */
    val state: StateFlow<InboxState> = _state
    private val lock = Mutex()
    @Volatile private var loaded = false

    /** Call once from Application.onCreate in the main process. */
    fun init(context: Context) {
        val app = context.applicationContext
        load(app)
        WorkManager.getInstance(app).enqueueUniquePeriodicWork(
            PERIODIC, ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<InboxWorker>(15, TimeUnit.MINUTES).setConstraints(unmetered()).build(),
        )
        app.getSystemService(ConnectivityManager::class.java).registerNetworkCallback(
            NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build(),
            object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) = kick(app)
            },
        )
    }

    /** A check soon (WorkManager, unmetered). Coalesces with one already waiting. */
    fun kick(context: Context) {
        WorkManager.getInstance(context).enqueueUniqueWork(
            ONCE, ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<InboxWorker>().setConstraints(unmetered()).build(),
        )
    }

    /** The launcher face came on: check unless the last check was under [FACE_MIN_INTERVAL_MS] ago. */
    fun maybeKick(context: Context, nowMs: Long = System.currentTimeMillis()) {
        load(context)
        val last = maxOf(_state.value.lastCheckMs, lastKickMs) // attempts count too (e.g. away from home)
        if (nowMs - last < FACE_MIN_INTERVAL_MS) return
        lastKickMs = nowMs
        kick(context)
    }

    @Volatile private var lastKickMs = 0L

    fun load(context: Context) {
        if (loaded) return
        synchronized(this) {
            if (loaded) return
            _state.value = InboxStore(context).read()
            loaded = true
        }
    }

    /**
     * Marks [id] read on the watch right away (list, notification) and tells the companion
     * now if it is reachable, else on the next sync. Returns the item.
     */
    suspend fun markRead(context: Context, id: String): InboxItem? {
        val app = context.applicationContext
        load(app)
        val item = lock.withLock {
            val next = InboxLogic.markRead(_state.value, id, OffsetDateTime.now(ZoneOffset.UTC).toString())
            save(app, next)
            next.item(id)
        }
        InboxNotifier.cancel(app, id)
        runCatching { sync(app, pollOnly = false) }.onFailure { Log.w(TAG, "read sync failed", it) }
        return item
    }

    /**
     * Sends pending reads, polls, merges, announces new items. Returns false when the companion
     * is not reachable now (not paired, not on home Wi-Fi, not found, or an HTTP error).
     */
    suspend fun sync(context: Context, pollOnly: Boolean = false): Boolean = withContext(Dispatchers.IO) {
        val app = context.applicationContext
        load(app)
        lock.withLock {
            val link = link(app) ?: return@withLock false
            var st = _state.value
            if (!pollOnly) {
                for (id in st.pendingReads) {
                    when (link.read(id)) {
                        200, 404 -> st = InboxLogic.readAcked(st, id) // 404: the companion dropped it; nothing to tell
                        else -> Unit
                    }
                }
            }
            val resp = link.poll(st.cursor) ?: return@withLock false.also { save(app, st) }
            val merge = InboxLogic.merge(st, resp, System.currentTimeMillis())
            save(app, merge.state)
            InboxNotifier.update(app, merge.state, merge.announce)
            if (merge.announce.isNotEmpty()) Log.i(TAG, "${merge.announce.size} new inbox item(s)")
            true
        }
    }

    private fun save(context: Context, s: InboxState) {
        _state.value = s
        runCatching { InboxStore(context).write(s) }.onFailure { Log.w(TAG, "inbox state write failed", it) }
    }

    /** The companion if reachable now: paired, on home Wi-Fi, known address (or found by mDNS). */
    private suspend fun link(context: Context): Link? {
        val prefs = ServerPrefs(context)
        val pairing = prefs.pairing ?: return null
        val wifi = WifiNow.read(context) ?: return null
        val home = prefs.home ?: return null
        if (!wifi.matches(home)) return null
        val ip = prefs.lastIp
            ?: Discovery(context, OrbitClient(wifi.network.socketFactory), prefs).find(wifi, pairing, allowScan = false)
            ?: return null
        return Link(wifi.network.socketFactory, "http://$ip:${pairing.port}", pairing.token)
    }

    private fun unmetered() = Constraints.Builder().setRequiredNetworkType(NetworkType.UNMETERED).build()

    /** The two inbox calls, bound to the Wi-Fi network like every companion call. */
    private class Link(socketFactory: SocketFactory, private val base: String, private val token: String) {
        private val http = OkHttpClient.Builder()
            .socketFactory(socketFactory)
            .connectTimeout(3, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS) // the companion may run its producers (bb) first
            .build()

        fun poll(since: Long): InboxResponse? = runCatching {
            http.newCall(Request.Builder().url("$base/v1/inbox?since=$since").header("Authorization", "Bearer $token").build())
                .execute().use { r -> if (r.code == 200) InboxLogic.parse(r.body.string()) else null.also { Log.w(TAG, "poll: HTTP ${r.code}") } }
        }.onFailure { Log.w(TAG, "poll failed: $it") }.getOrNull()

        /** HTTP status, or -1 on a network error. */
        fun read(id: String): Int = runCatching {
            http.newCall(
                Request.Builder().url("$base/v1/inbox/$id/read").header("Authorization", "Bearer $token")
                    .post(ByteArray(0).toRequestBody()).build()
            ).execute().use { it.code }
        }.getOrDefault(-1)
    }
}

/** `files/inbox/state.json`, written atomically. Only [InboxSync] (one process) writes it. */
class InboxStore(context: Context) {
    private val file = File(context.filesDir, "inbox/state.json")

    fun read(): InboxState = runCatching {
        JSON.decodeFromString(InboxState.serializer(), file.readText())
    }.getOrDefault(InboxState())

    fun write(s: InboxState) {
        file.parentFile?.mkdirs()
        val tmp = File(file.path + ".tmp")
        tmp.writeText(JSON.encodeToString(InboxState.serializer(), s))
        if (!tmp.renameTo(file)) error("rename failed")
    }

    private companion object {
        val JSON = Json { ignoreUnknownKeys = true }
    }
}

/** Periodic / one-off check. Never retries aggressively: the next cheap moment will come. */
class InboxWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        InboxSync.sync(applicationContext)
        return Result.success()
    }
}

/**
 * One Android notification per unread item (so it shows in the launcher's notifications
 * panel and the face's unread count, and opens Talk about the item), plus one short buzz per
 * batch — skipped while a recording is running, so the mic doesn't pick it up.
 */
object InboxNotifier {
    private const val CHANNEL = "inbox"

    fun update(context: Context, state: InboxState, announce: List<InboxItem>) {
        val nm = context.getSystemService(NotificationManager::class.java)
        for (item in state.items) if (!item.unread) nm.cancel(tag(item.id), 0)
        if (announce.isEmpty()) return
        // Silent channel: the buzz below is ours, short, and once per batch.
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "Inbox", NotificationManager.IMPORTANCE_DEFAULT).apply {
                setSound(null, null)
                enableVibration(false)
            }
        )
        for (item in announce.take(5)) nm.notify(tag(item.id), 0, build(context, item))
        if (RecorderState.readMarker(context) == null) {
            context.getSystemService(Vibrator::class.java)
                ?.vibrate(VibrationEffect.createOneShot(90, VibrationEffect.DEFAULT_AMPLITUDE))
        }
    }

    fun cancel(context: Context, id: String) =
        context.getSystemService(NotificationManager::class.java).cancel(tag(id), 0)

    private fun build(context: Context, item: InboxItem): Notification {
        val open = PendingIntent.getActivity(
            context, item.id.hashCode(), TalkActivity.aboutIntent(context, item.id),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val sent = runCatching { OffsetDateTime.parse(item.created_at).toInstant().toEpochMilli() }.getOrNull()
        return Notification.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_inbox)
            .setContentTitle(item.title)
            .setContentText(item.body)
            .setStyle(Notification.BigTextStyle().bigText(item.body))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_MESSAGE)
            .apply { if (sent != null) setWhen(sent).setShowWhen(true) }
            .build()
    }

    private fun tag(id: String) = "inbox:$id"
}
