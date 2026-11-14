package com.orbit.launcher.notifs

import android.app.Notification
import android.app.PendingIntent
import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.provider.Settings
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import com.orbit.launcher.data.NotifItem
import com.orbit.launcher.data.Notifs

/**
 * Feeds [Notifs] with the active notifications. Granted with adb:
 * `cmd notification allow_listener com.orbit/com.orbit.launcher.notifs.NotifListener`.
 *
 * Android binds this service itself while access is granted; callbacks arrive on
 * the main thread. PendingIntents stay here (keyed by notification key) so the
 * shared [NotifItem] model remains plain data.
 */
class NotifListener : NotificationListenerService() {

    override fun onListenerConnected() {
        instance = this
        Notifs.connected.value = true
        publish()
    }

    override fun onListenerDisconnected() {
        if (instance === this) instance = null
        Notifs.connected.value = false
        actions = emptyMap()
        Notifs.set(emptyList())
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) = publish()
    override fun onNotificationRemoved(sbn: StatusBarNotification?) = publish()

    override fun onDestroy() {
        if (instance === this) {
            instance = null
            Notifs.connected.value = false
        }
        super.onDestroy()
    }

    private fun publish() {
        val active = runCatching { activeNotifications }.getOrNull() ?: return // not bound (yet)
        val raws = active.map { it.toRaw() }
        val keep = visibleKeys(raws, packageName)
        val items = ArrayList<NotifItem>(keep.size)
        val acts = HashMap<String, Action>(keep.size)
        for (sbn in active) {
            if (sbn.key !in keep) continue
            val n = sbn.notification
            val app = label(sbn.packageName)
            val extras = n.extras
            val title = extras?.getCharSequence(Notification.EXTRA_TITLE)?.toString()?.trim().orEmpty()
            val text = (
                extras?.getCharSequence(Notification.EXTRA_BIG_TEXT)
                    ?: extras?.getCharSequence(Notification.EXTRA_TEXT)
                    ?: extras?.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)?.lastOrNull()
                )?.toString()?.trim().orEmpty()
            items += NotifItem(
                key = sbn.key,
                pkg = sbn.packageName,
                app = app,
                title = title.ifEmpty { app },
                text = text,
                postedAt = sbn.postTime,
                clearable = sbn.isClearable,
            )
            acts[sbn.key] = Action(n.contentIntent, n.flags and Notification.FLAG_AUTO_CANCEL != 0)
        }
        actions = acts
        Notifs.set(items)
    }

    private fun StatusBarNotification.toRaw() = Raw(
        key = key,
        pkg = packageName,
        groupKey = groupKey,
        summary = notification.flags and Notification.FLAG_GROUP_SUMMARY != 0,
        ongoing = notification.flags and (Notification.FLAG_ONGOING_EVENT or Notification.FLAG_FOREGROUND_SERVICE) != 0,
    )

    private fun label(pkg: String): String = labels.getOrPut(pkg) {
        runCatching {
            val pm: PackageManager = packageManager
            pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
        }.getOrDefault(pkg)
    }

    /** What a tap on a notification does. */
    private class Action(val content: PendingIntent?, val autoCancel: Boolean)

    /** The fields the filter needs, so it can be unit-tested without Android. */
    internal data class Raw(val key: String, val pkg: String, val groupKey: String?, val summary: Boolean, val ongoing: Boolean)

    companion object {
        private const val TAG = "OrbitNotifs"

        @Volatile private var instance: NotifListener? = null
        @Volatile private var actions: Map<String, Action> = emptyMap()
        private val labels = HashMap<String, String>()

        /**
         * Keys to show: drops our own ongoing / foreground-service notifications (the recorder)
         * and group summaries whose children are also posted (they would show twice).
         */
        internal fun visibleKeys(raws: List<Raw>, ownPkg: String): Set<String> {
            val groupsWithChildren = raws.filter { !it.summary && it.groupKey != null }.mapTo(HashSet()) { it.groupKey }
            return raws.filter { r ->
                !(r.pkg == ownPkg && r.ongoing) &&
                    !(r.summary && r.groupKey != null && r.groupKey in groupsWithChildren)
            }.mapTo(LinkedHashSet()) { it.key }
        }

        /**
         * Opens the notification: sends its content intent (and removes it when the
         * app asked for auto-cancel). Without one, opens the app instead.
         * Returns false if nothing could be opened.
         */
        fun open(context: Context, item: NotifItem): Boolean {
            val act = actions[item.key]
            val sent = act?.content?.let { pi ->
                runCatching { pi.send(); true }.getOrElse { Log.w(TAG, "send failed", it); false }
            } ?: false
            if (sent) {
                if (act?.autoCancel == true) dismiss(item.key)
                return true
            }
            val launch = context.packageManager.getLaunchIntentForPackage(item.pkg) ?: return false
            return runCatching {
                context.startActivity(launch.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)); true
            }.getOrDefault(false)
        }

        /** Removes a (clearable) notification, as if swiped away in the shade. */
        fun dismiss(key: String) {
            runCatching { instance?.cancelNotification(key) }.onFailure { Log.w(TAG, "cancel failed", it) }
        }

        @Volatile private var lastKick = 0L

        /** True if the user (or adb) granted notification access to this listener. */
        fun isGranted(context: Context): Boolean {
            val me = ComponentName(context, NotifListener::class.java)
            val list = Settings.Secure.getString(context.contentResolver, "enabled_notification_listeners").orEmpty()
            return list.split(':').any { ComponentName.unflattenFromString(it) == me }
        }

        /**
         * Gets the system to bind us if access is granted but we are not connected.
         *
         * On this ROM the vendor launch gate (Sprd `BackgroundCleanHelper.judgeAppLaunchAllowed`)
         * refuses to *start our process* for the system's bind, so every bind attempted while
         * we are not running fails ("Unable to bind notification listener service", e.g. right
         * after install or at boot), and the system does not retry. A bind made while our
         * process is alive is allowed. `requestRebind` cannot help: on 8.1 it is a no-op unless
         * the listener was snoozed. So we toggle the component off and on (without killing
         * ourselves): the resulting PACKAGE_CHANGED makes the system rebind its listeners now,
         * while we are running. See docs/device-facts.md.
         */
        fun ensureBound(context: Context) {
            if (Notifs.connected.value || !isGranted(context)) return
            val now = android.os.SystemClock.elapsedRealtime()
            if (lastKick != 0L && now - lastKick < 30_000) return
            lastKick = now
            val me = ComponentName(context, NotifListener::class.java)
            val pm = context.packageManager
            runCatching {
                pm.setComponentEnabledSetting(me, PackageManager.COMPONENT_ENABLED_STATE_DISABLED, PackageManager.DONT_KILL_APP)
                pm.setComponentEnabledSetting(me, PackageManager.COMPONENT_ENABLED_STATE_DEFAULT, PackageManager.DONT_KILL_APP)
                Log.i(TAG, "listener not bound; toggled component to make the system rebind")
            }.onFailure { Log.w(TAG, "rebind kick failed", it) }
        }
    }
}
