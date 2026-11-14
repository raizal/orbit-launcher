# Orbit Inbox — the companion tells the watch things

The companion can leave short items for the watch ("the cancel-feature thread is done"). The watch
**pulls** them at cheap moments; there is no push channel and no connection kept open.

Code: companion `companion/src/orbit_notes/inbox.py` (store, producers, Talk context),
`inbox_api.py` (routes), web UI `companion/web/src/Inbox.svelte`; watch
`feature-voice/src/main/java/com/orbit/voice/inbox/` (`InboxModel.kt` pure logic, `InboxSync.kt`
sync/worker/notifications, `InboxListActivity.kt` screen).

## Item

```
{ "id": "in-3f9c0a1b2d",                 # companion-made, stable
  "rev": 12,                              # bumped on insert AND on every read-state change
  "created_at": "2026-11-15T09:12:44+00:00",   # UTC
  "kind": "note" | "thread_done" | "thread_input" | "thread_error" | "app_update" | …,
  "title": "Cancel Talk and voice-note recordings is done",   # ≤ 80 chars, spoken-friendly
  "body": "Cancel is in place in both screens. Unit tests pass.",   # a sentence or two (≤ 2000)
  "ref": "thr_jq23t7qzba" | null,         # e.g. the BB thread
  "read_at": null | "2026-11-15T09:20:01+00:00" }
```

## API (bearer token, like every `/v1` route)

```
GET  /v1/inbox?since=<cursor>&limit=50
  200 {"cursor": 12, "unread": 2, "items": [ …items with rev > since, newest first… ]}
  # cursor = highest rev; pass it back as `since`. since=0 → the newest `limit` items.
  # Producers (below) run here first, at most once a minute.
POST /v1/inbox/{id}/read     200 the item (read_at set) · 404 unknown id
```

Because a read bumps `rev`, a watch polling with its cursor also learns about items marked
read (or unread) elsewhere (web UI, CLI).

## Adding items (this computer only)

```
orbit-notes inbox add --title "Build is green" --body "All 105 tests pass." [--kind note] [--ref thr_…]
orbit-notes inbox list            # newest first, ● = unread
orbit-notes inbox read in-…       # [--unread]
curl -X POST localhost:8765/ui/api/inbox -H 'content-type: application/json' \
     -d '{"title": "…", "body": "…", "kind": "note", "ref": null}'     # loopback only (403 from the LAN)
GET  /ui/api/inbox                # {"unread", "items"} for the web UI
POST /ui/api/inbox/{id}/read      # {"read": true|false}
```

The CLI writes the sqlite file directly, so it works whether or not `serve` runs. Web UI: the
**Inbox** button in the header (accent dot = unread on the watch) lists items with their read
state and has a small "Send to watch" form.

## Storage

`<data_dir>/inbox.sqlite`: `items` (+ `mentioned_at`, `dedupe`) and `meta` (`rev`, producer state).
Keeps the newest 200 items. Writes use `BEGIN IMMEDIATE`, so the CLI and the server can write at once.

## Producers (lazy, pluggable)

A producer is a class with `name` and `run(inbox) -> list[NewItem]` (`NewItem.dedupe` = a key that
makes the same event never create two items). `Producers.refresh()` runs them when the watch polls
or a Talk turn starts, **at most once per 60 s**; nothing runs in the background. A failing producer
is logged and skipped. Add new ones in `inbox.default_producers()` (e.g. app updates later).

**threads** (only with `talk_workspace = "bb"`): threads that Talk spawned or messaged in the last
7 days (`talk_audit.jsonl`, `executed` `thread_spawn` / `thread_tell`). One `bb thread list` per run;
for a watched thread whose `updatedAt` moved past the spawn/tell time:
- `hasPendingInteraction` → "‹title› needs your input" (once per waiting period);
- status `error` → "‹title› stopped with an error";
- status `idle` → `bb thread output` → if the output changed: "‹title› is done", body = the first
  paragraph of the output, markdown stripped, cut at a sentence end (≤ 240 chars).
Completions from before the producer's first run are recorded silently (no backlog burst on deploy).

## Watch

- **When it checks** (`InboxSync`): home Wi-Fi connects (its own NetworkCallback); after the upload
  worker sent ≥ 1 note; the launcher face comes on (`LauncherActivity.onStart`, ≥ 2 min apart,
  attempts count); a 15-min periodic WorkManager job (unmetered); every 10 s while Talk or the Inbox
  screen is open. Checks need pairing + home Wi-Fi + the companion's last known IP (or mDNS);
  otherwise they end at once. No retries/backoff: the next cheap moment comes soon enough.
- **Local state**: `files/inbox/state.json` (cursor, ≤ 100 items newest first, reads not yet
  acknowledged, last check time, and which companion it belongs to: `name:port:<sha256(token)[:6]>`).
  Reads made on the watch are kept until the companion confirms them. Paired to another companion,
  or the companion's cursor went backwards (its data was reset) → the state starts afresh (cursor 0,
  quiet first sync, old notifications removed).
- **New items** (unread, not seen before; never on the very first sync) → one Android notification
  per item (silent channel "Inbox", up to 5 per batch) + one short 90 ms buzz per batch, skipped
  while a recording runs. Reading an item anywhere cancels its notification.
- **Where it shows** — chosen to be the least intrusive: the notification itself. The launcher's
  notifications panel already lists Orbit's own (non-ongoing) notifications and the face's unread
  count already counts them, so no new face complication or launcher screen is needed. Tapping it
  opens Talk about the item.
- **Inbox screen** (`InboxListActivity`, same visual language as the notes list): curved header
  "INBOX · 2 NEW", newest first, unread = accent dot + bright title + coloured icon (✓ done,
  ? needs input, ! error, bell otherwise), rotary/drag to move, tap the centered item → Talk about it.
  Reached from the **Inbox pill** on the Talk screen (top, next to New; shows "2 new" in accent).
- **Talk about an item**: Talk opens with the item as a "TALKING ABOUT" card at the bottom of the
  conversation (✕ drops it); the item becomes read. The **next** turn's sidecar carries
  `"about": "<item id>"` (`NoteMeta.about`), then it is cleared.

## Talk (companion)

For each talk turn (`Talker.inbox_context`, prompt section just before the voice note):
- sidecar `about` → the item's title/body and, for a thread item, `bb thread output <ref>` (≤ 4000
  chars) as evidence, so "tell me more" works;
- unread items not mentioned yet (last 24 h, at most 3) → Codex may mention them briefly once at the
  end of the reply ("by the way, …"). After the turn they (and the `about` item) are marked
  `mentioned_at` and never offered again. Failures here never fail the turn.
