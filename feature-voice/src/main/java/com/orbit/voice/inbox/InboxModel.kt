package com.orbit.voice.inbox

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.time.Duration
import java.time.OffsetDateTime

/** One inbox item as the companion sends it (docs/inbox.md → API). */
@Serializable
data class InboxItem(
    val id: String,
    val rev: Long = 0,
    val created_at: String,
    val kind: String = "note",
    val title: String,
    val body: String = "",
    val ref: String? = null,
    val read_at: String? = null,
) {
    val unread: Boolean get() = read_at == null
}

/** `GET /v1/inbox?since=…` */
@Serializable
data class InboxResponse(val cursor: Long, val unread: Int = 0, val items: List<InboxItem> = emptyList())

/**
 * What the watch keeps (`files/inbox/state.json`): the items it has seen, newest first, the
 * server cursor, and reads made here that the companion has not acknowledged yet.
 */
@Serializable
data class InboxState(
    val cursor: Long = 0,
    val items: List<InboxItem> = emptyList(),
    val pendingReads: Set<String> = emptySet(),
    val lastCheckMs: Long = 0,
) {
    val unreadCount: Int get() = items.count { it.unread }
    fun item(id: String): InboxItem? = items.firstOrNull { it.id == id }
}

/** Pure inbox rules (unit tested): parsing, merging a poll into the local state, local reads. */
object InboxLogic {
    const val KEEP = 100

    private val JSON = Json { ignoreUnknownKeys = true; explicitNulls = false }

    /** Null when the body is not an inbox response. Items without id/title/created_at are dropped. */
    fun parse(body: String): InboxResponse? = runCatching {
        val r = JSON.decodeFromString(InboxResponse.serializer(), body)
        r.copy(items = r.items.filter { it.id.isNotBlank() && it.title.isNotBlank() && it.created_at.isNotBlank() })
    }.getOrNull()

    /** [state] after a poll + the items to announce (notification + buzz). */
    data class Merge(val state: InboxState, val announce: List<InboxItem>)

    /**
     * Upserts by id (the companion is the source of truth, except a read made here and not yet
     * acknowledged stays read). Newest first, at most [KEEP]. New unread items are announced —
     * except on the very first sync (cursor 0), which only fills the list: no burst of old news.
     */
    fun merge(state: InboxState, resp: InboxResponse, nowMs: Long): Merge {
        val known = state.items.associateBy { it.id }
        val byId = LinkedHashMap(known)
        var pending = state.pendingReads
        for (item in resp.items) {
            val localRead = known[item.id]?.read_at
            byId[item.id] = when {
                item.read_at != null -> item.also { pending = pending - item.id }
                item.id in pending -> item.copy(read_at = localRead ?: item.created_at)
                else -> item
            }
        }
        val items = byId.values.sortedWith(compareByDescending<InboxItem> { it.created_at }.thenByDescending { it.rev }).take(KEEP)
        val kept = items.mapTo(HashSet()) { it.id }
        val first = state.cursor == 0L && state.items.isEmpty()
        val announce = if (first) emptyList() else resp.items.filter { it.id !in known && it.unread && it.id in kept }
        return Merge(
            InboxState(cursor = maxOf(state.cursor, resp.cursor), items = items, pendingReads = pending.filterTo(HashSet()) { it in kept }, lastCheckMs = nowMs),
            announce.sortedByDescending { it.created_at },
        )
    }

    /** Marks [id] read here; the companion is told on the next sync ([InboxState.pendingReads]). */
    fun markRead(state: InboxState, id: String, nowIso: String): InboxState {
        val item = state.item(id) ?: return state
        if (!item.unread) return state
        return state.copy(
            items = state.items.map { if (it.id == id) it.copy(read_at = nowIso) else it },
            pendingReads = state.pendingReads + id,
        )
    }

    /** The companion acknowledged the read (or no longer knows the item). */
    fun readAcked(state: InboxState, id: String): InboxState = state.copy(pendingReads = state.pendingReads - id)

    /** "just now", "5 min", "3 h", "2 d" — the age of an item for a one-line subtitle. */
    fun age(createdAt: String, nowMs: Long): String {
        val t = runCatching { OffsetDateTime.parse(createdAt).toInstant().toEpochMilli() }.getOrNull() ?: return ""
        val m = Duration.ofMillis((nowMs - t).coerceAtLeast(0)).toMinutes()
        return when {
            m < 1 -> "just now"
            m < 60 -> "$m min"
            m < 36 * 60 -> "${(m + 30) / 60} h"
            else -> "${(m + 720) / 1440} d"
        }
    }
}
