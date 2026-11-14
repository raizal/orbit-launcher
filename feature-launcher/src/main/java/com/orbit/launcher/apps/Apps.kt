package com.orbit.launcher.apps

import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.net.Uri
import android.content.IntentFilter
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.Drawable
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.PointerInputScope
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.input.pointer.util.VelocityTracker
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.text.Collator
import java.util.concurrent.ConcurrentHashMap

/** One launchable activity. [icon] is null until it has been rendered (labels show first). */
class AppEntry(val label: String, val component: ComponentName, val icon: ImageBitmap?) {
    fun intent(): Intent = Intent(Intent.ACTION_MAIN)
        .addCategory(Intent.CATEGORY_LAUNCHER)
        .setComponent(component)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)

    /** System "App info" page for this app (long-press on its icon). */
    fun infoIntent(): Intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
        .setData(Uri.fromParts("package", component.packageName, null))
}

/**
 * Process-wide list of launchable apps (MAIN + LAUNCHER), sorted by label, without Orbit itself.
 * Icons are rendered once to small bitmaps and kept, so reopening the grid / list is instant.
 * A package broadcast receiver marks the list stale and reloads it in the background.
 */
object AppCache {
    /** Icon bitmap edge in real pixels — larger than the biggest on-screen icon (72 · 1.3 zoom · 0.9 ≈ 84 px). */
    private const val ICON_PX = 88

    private val _apps = MutableStateFlow<List<AppEntry>?>(null)
    val apps: StateFlow<List<AppEntry>?> = _apps

    /** Last list scroll position, restored when the list reopens. */
    @Volatile var listPos = 0f

    private val icons = ConcurrentHashMap<ComponentName, ImageBitmap>()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val mutex = Mutex()
    @Volatile private var stale = true
    @Volatile private var watching = false

    /** Start loading in the background (e.g. at launcher start) so the first open is instant too. */
    fun warm(context: Context) {
        scope.launch { refresh(context) }
    }

    /** Loads the app list if it is missing or stale. Cheap no-op otherwise. Safe to call from any thread. */
    suspend fun refresh(context: Context) {
        val app = context.applicationContext
        watch(app)
        if (!stale && _apps.value != null) return
        mutex.withLock {
            if (!stale && _apps.value != null) return
            stale = false
            withContext(Dispatchers.Default) { runCatching { load(app) } }
        }
    }

    private fun load(context: Context) {
        val pm = context.packageManager
        val own = context.packageName
        val main = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val collator = Collator.getInstance().apply { strength = Collator.PRIMARY }
        val found = pm.queryIntentActivities(main, 0)
            .filter { it.activityInfo.packageName != own && it.activityInfo.packageName != "com.orbit" }
            .map { ri ->
                val ai = ri.activityInfo
                val label = runCatching { ri.loadLabel(pm)?.toString()?.trim() }.getOrNull()
                    .takeUnless { it.isNullOrEmpty() } ?: ai.packageName
                Triple(label, ComponentName(ai.packageName, ai.name), ri)
            }
            .sortedWith { a, b -> collator.compare(a.first, b.first) }

        fun publish() {
            _apps.value = found.map { (label, cn, _) -> AppEntry(label, cn, icons[cn]) }
        }
        publish() // labels (and any cached icons) first
        var rendered = 0
        for ((_, cn, ri) in found) {
            if (icons.containsKey(cn)) continue
            val drawable = runCatching { ri.loadIcon(pm) }.getOrNull() ?: continue
            runCatching { render(drawable) }.getOrNull()?.let { icons[cn] = it }
            if (++rendered % 12 == 0) publish()
        }
        if (rendered > 0) publish()
    }

    private fun render(d: Drawable): ImageBitmap {
        val bmp = Bitmap.createBitmap(ICON_PX, ICON_PX, Bitmap.Config.ARGB_8888)
        d.setBounds(0, 0, ICON_PX, ICON_PX)
        d.draw(Canvas(bmp))
        bmp.prepareToDraw()
        return bmp.asImageBitmap()
    }

    private fun watch(app: Context) {
        if (watching) return
        synchronized(this) {
            if (watching) return
            watching = true
        }
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_PACKAGE_ADDED)
            addAction(Intent.ACTION_PACKAGE_REMOVED)
            addAction(Intent.ACTION_PACKAGE_CHANGED)
            addAction(Intent.ACTION_PACKAGE_REPLACED)
            addDataScheme("package")
        }
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) {
                val pkg = i.data?.schemeSpecificPart
                if (pkg != null) icons.keys.removeAll { it.packageName == pkg }
                stale = true
                scope.launch { refresh(app) }
            }
        }
        ContextCompat.registerReceiver(app, receiver, filter, ContextCompat.RECEIVER_NOT_EXPORTED)
        // Labels follow the system language.
        ContextCompat.registerReceiver(
            app,
            object : BroadcastReceiver() {
                override fun onReceive(c: Context, i: Intent) {
                    stale = true
                    scope.launch { refresh(app) }
                }
            },
            IntentFilter(Intent.ACTION_LOCALE_CHANGED),
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
    }
}

/** Top band left to the system shade: gestures starting here are ignored (docs/launcher.md). */
internal const val TOP_GUARD = 40f

/**
 * One pointer: a tap, a long-press (held still past the system long-press timeout), or a drag
 * (after touch slop). Positions/deltas are reported in design units (RoundScreen makes
 * 1 dp = 1 design px), velocity in design units per second.
 */
internal suspend fun PointerInputScope.dragOrTap(
    onStart: () -> Unit,
    onDrag: (dx: Float, dy: Float) -> Unit,
    onEnd: (vx: Float, vy: Float) -> Unit,
    onTap: (x: Float, y: Float) -> Unit,
    onLongPress: ((x: Float, y: Float) -> Unit)? = null,
) = awaitEachGesture {
    val down = awaitFirstDown(requireUnconsumed = false)
    val k = 1f / density
    if (down.position.y * k < TOP_GUARD) return@awaitEachGesture
    val tracker = VelocityTracker()
    tracker.addPosition(down.uptimeMillis, down.position)
    var dragging = false
    var acc = Offset.Zero
    val longPressAt = down.uptimeMillis + viewConfiguration.longPressTimeoutMillis
    while (true) {
        val event = if (onLongPress != null && !dragging) {
            val wait = longPressAt - (currentEvent.changes.firstOrNull()?.uptimeMillis ?: down.uptimeMillis)
            withTimeoutOrNull(wait.coerceAtLeast(1)) { awaitPointerEvent() }
        } else {
            awaitPointerEvent()
        }
        if (event == null) {
            // Held still long enough: long-press, then swallow the rest of this gesture.
            onLongPress!!(down.position.x * k, down.position.y * k)
            do {
                val rest = awaitPointerEvent()
                rest.changes.forEach { it.consume() }
            } while (rest.changes.any { it.pressed })
            return@awaitEachGesture
        }
        val change = event.changes.firstOrNull { it.id == down.id } ?: break
        if (!change.pressed) {
            change.consume()
            if (dragging) {
                val v = tracker.calculateVelocity()
                onEnd(v.x * k, v.y * k)
            } else {
                onTap(change.position.x * k, change.position.y * k)
            }
            return@awaitEachGesture
        }
        tracker.addPosition(change.uptimeMillis, change.position)
        val delta = change.positionChange()
        if (dragging) {
            onDrag(delta.x * k, delta.y * k)
        } else {
            acc += delta
            if (acc.getDistance() > viewConfiguration.touchSlop) {
                dragging = true
                onStart()
                onDrag(acc.x * k, acc.y * k)
            }
        }
        change.consume()
    }
    if (dragging) onEnd(0f, 0f)
}
