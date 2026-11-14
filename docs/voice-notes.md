# Orbit audio diary — watch spec (`:feature-voice`)

A built-in feature of the Orbit app, not a separate app. The watch only records, names, queues and sends. All transcription and diary work happens on the companion.

## Orbit shortcuts that start a recording

All of them call one internal entry point: `VoiceEntry.startRecording(context)` (also reachable as action `com.orbit.action.RECORD` for app shortcuts / adb testing: `adb shell am start -a com.orbit.action.RECORD`).

| Shortcut | Phase |
|---|---|
| Orbit app icon → recorder screen | 1 |
| Static app shortcut "New voice note" (long-press icon) → starts recording | 1 |
| Rotary turned on the Orbit face → opens the diary side panel | 3 |
| Mic slot (`voice_note` complication) on the face → starts recording | 3 |
| Center of the honeycomb app grid → recorder screen | 3 |
| Quick-settings / tile "Diary" with today's note count → inbox | 3 |
| Optional: long-press BACK (F9) — free to use, see `device-facts.md` | later |

Rule: one press from the face must start recording within 1 second, with a short vibration as confirmation.

## Recording

- `MediaRecorder`, output **`AAC_ADTS`** (`.aac`), encoder `AAC` (LC), 16 000 Hz, mono, 32 kbps. ≈15 MB per hour. (Android 8.1 cannot write Opus/Ogg.)
- Why ADTS, not MP4/`.m4a`: measured on the watch, a reboot mid-recording (same as an empty battery) leaves an `.m4a` **unplayable** (no `moov` index), while an ADTS file keeps everything up to the last ~1–2 s. See `docs/device-facts.md` → Audio.
- Runs in a **foreground Service** with an ongoing notification and a partial wake lock, so it continues with the screen off and if the UI is closed.
- Request `RECORD_AUDIO` at first launch (runtime permission).
- Write to a temp name while recording; rename to the final name only after `stop()` succeeds, then write the sidecar. A crash mid-recording must leave a recoverable file — on next start, find temp files and finalize them (an ADTS temp file is playable as-is: rename it, write the sidecar with `duration_ms` read via `MediaMetadataRetriever` or estimated from size, and set `"recovered": true`). Only flag it if it is empty or unreadable.
- **Cancel** (small grey ✕ left of Stop, recorder and Talk): the one user action that deletes unsent audio. The UI first writes `<stamp>.aac.cancel` next to `<stamp>.aac.rec`, then sends `ACTION_CANCEL` (with the file name) to the service, which stops the recorder and deletes the temp file, then the marker. Stop, orphan recovery and the waiting count all skip a marked temp file, so a cancelled recording is never finalized, queued or uploaded — even if `:voice` dies mid-cancel (the next service start deletes it). Cancel for a different file than the one recording, or with nothing recording, does nothing: a saved note is never deleted. If Stop was tapped first, Cancel is ignored. Cancel closes the screen.
- Recording state survives the UI being killed (service is the source of truth; UI binds to it).

## File naming

```
2026-11-13_12-08-42+0700.aac
2026-11-13_12-08-42+0700.json
```

- Time = the moment record was pressed, **local time with UTC offset**. No colons (filesystem-safe). Sorts by name.
- Storage: app-specific external dir `Android/data/<pkg>/files/notes/` (no storage permission needed on 8.1, and `adb pull`-able).

## Sidecar JSON (the truth — file times can change when copied)

```json
{
  "id": "uuid",
  "started_at": "2026-11-13T12:08:42+07:00",
  "duration_ms": 14230,
  "sha256": "hex of the .aac",
  "device": "H10",
  "app_version": "0.1.0",
  "location": { "lat": -7.26, "lon": 112.75, "acc_m": 900 }
}
```

`location` is optional and coarse (last known location only; never wait for a GPS fix). **Not written yet** (the watch has no last-known location without a fix). A sidecar may also carry `"recovered": true` (finalized after a crash). Talk turns add `"mode": "talk"`,
optionally `"conversation": "new"` (docs/talk.md) and `"about": "<inbox item id>"` (docs/inbox.md).

## Queue & states

`recorded → waiting → sending (progress) → sent → (deleted after 7 days)`. Also `failed` with last error, which keeps retrying.

- **The server is local only.** Uploads happen only when the watch is on the paired home Wi-Fi — never on LTE, never on other Wi-Fi networks. Notes recorded outside just wait in the queue.
- **As built:** one WorkManager unique work `upload-queue` that sends every unsent note oldest-first (no parallel uploads on the slow watch), constraint `UNMETERED`, exponential backoff from 30 s. It is triggered by: a saved note (explicit broadcast from the `:voice` process), the Wi-Fi connecting (`NetworkCallback`, replaces a backed-off run), and a 15-min periodic sweep. Not on home Wi-Fi → it ends quietly and waits for the next trigger. Measured: offline note uploads ~35 s after Wi-Fi returns.
- Upload state lives in `files/queue/<stem>.json` (main process only); the notes folder is the source of truth (no entry = waiting).
- Mark `sent` only when the server returns the same sha256.
- **"Sent" means sent to the companion paired now.** The entry records *which* companion confirmed the note: `"sentTo": {"<fingerprint>": <sentAtMs>}`, fingerprint = first 16 hex of sha256(`name + "\n" + token`) of the pairing (never the token itself; port left out). A note counts as `sent` only if the current pairing's fingerprint is in `sentTo`; otherwise it is `waiting` again and the queue re-sends it (HEAD first, so a companion that already has it is not uploaded to again; PUT is idempotent). Re-pairing back to a companion that already confirmed it shows it `sent` at once. Unpaired → nothing counts as sent.
- Entries we write have `state` `WAITING` or `FAILED` only (FAILED keeps `sentTo`). Legacy entries (`"state":"SENT"` + `sentAtMs`, no `sentTo`, from builds before per-server tracking) are stamped once with the pairing current at the first run after the update (marker file `files/queue/.sent-per-server-v1`; runs only while paired). A legacy entry that appears after that (an older build installed over ours) counts as unsent, never as sent to whoever is paired.
- **Never delete an unsent note.** Notes sent to the *current* companion are kept 7 days after it confirmed them, then deleted by a daily cleanup job. A note sent only to some other companion, or anything while unpaired, is never deleted.

## Finding a server

Local network only. Default port **8765**. Only run discovery when connected to the paired home Wi-Fi.

Order of attempts (stop at the first server that passes the proof check below):

1. **Last known IP** (cached after every successful upload) — one request, usually enough.
2. **mDNS:** resolve `_orbitnotes._tcp` via `NsdManager`, match `name` in TXT. (NsdManager is flaky on some Android 8 ROMs — give it ~3 s, then move on.)
3. **Subnet scan:** take the watch's IPv4 + netmask (`ConnectivityManager.getLinkProperties`), scan the /24 only (cap at 254 hosts even on bigger subnets), `GET http://<ip>:8765/v1/health?nonce=<random>`, 32 concurrent, 300 ms connect timeout. Full scan ≈ 3–5 s. Never scan more often than once per 10 min.

**Proof check (required):** a server is accepted only if `proof == HMAC-SHA256(token, nonce)` (hex). Any device on the network could answer `/v1/health`; only the real companion knows the token. Never send audio to a server that fails this.

**Home network check:** at pairing, store the Wi-Fi's SSID (and gateway IP as a fallback). Reading the SSID on 8.1 may need `ACCESS_COARSE_LOCATION` — if it returns `<unknown ssid>`, fall back to matching the gateway IP / subnet.

**Pairing (once):** companion shows a QR code containing `{"name":"home-pc","port":8765,"token":"…"}` (no IP — IPs change; discovery finds it). Watch scans with ZXing, stores it (EncryptedSharedPreferences if it works on 8.1, else private prefs), and records the current Wi-Fi as "home". The servers screen can also list hosts found by scan that are not paired yet — tapping one asks to scan its QR.

**Cleartext:** traffic is plain HTTP on the LAN. With targetSdk ≥ 28 add a network security config that permits cleartext (it cannot be limited by IP range, so permit it globally and only ever connect to LAN addresses that passed the proof check). Token in `Authorization` header; acceptable on a home LAN.

## Upload API (shared with companion)

```
GET  /v1/health?nonce=N    → 200 { "name": "home-pc", "api": 1, "proof": "<hex HMAC-SHA256(token, N)>" }
HEAD /v1/notes/{id}        → 200 already have it · 404 send it
PUT  /v1/notes/{id}        multipart/form-data: audio (the .aac), meta (the .json)
     Authorization: Bearer <token>
     → 201 { "id": "…", "sha256": "…" }   (200 with same body if already stored)
     → 409 if id exists with a different sha256
```

`PUT` with the note's own id is idempotent — a retry after a dropped connection never creates a duplicate. Always `HEAD` first to skip re-sending.

## App updates (over Wi-Fi from the companion)

The companion hosts the latest Orbit release; the watch offers it and installs it only after the user taps **Install** and confirms Android's own dialog. Never silent.

```
GET /v1/app/latest   Authorization: Bearer <token>
     → 200 { "version_code": 457890, "version_name": "0.1.0-abc1234", "sha256": "<hex>", "size": 2093120,
             "published_at": "2026-11-15T10:00:00+07:00", "apk": "/v1/app/apk" }
     → 404 nothing published
GET /v1/app/apk      Authorization: Bearer <token>  → the APK (application/vnd.android.package-archive)
```

- **Versioning:** `versionCode` = minutes since 2026-01-01T00:00Z at build time (`app/build.gradle.kts`; override `-PorbitVersionCode=N`), `versionName` = `0.1.0-<short commit>`. Newer build (any branch) = higher code; "newer" on the watch means a strictly higher versionCode. The watch is not debuggable, so a lower versionCode is never installable over a higher one (not even `adb install -r -d`).
- **When the watch checks** (no persistent connection; all over the Wi-Fi-bound client, home Wi-Fi + verified companion only): home Wi-Fi connects (20 s later, after uploads), after an upload batch, the Servers screen finding the companion, every 6 h (WorkManager, unmetered). Opportunistic checks run at most every 30 min; tapping the paired companion on the Servers screen, or opening the Update screen with nothing offered ("Check now"), checks at once.
- **Offer:** a newer `version_code` is downloaded to `files/update/`, size + sha256 verified (mismatch → deleted, retried next check), then offered: an "Orbit update available" notification and an "Update available" row on the Servers screen (in place of the unpaired-scan row) — both open the **Update** screen (installed vs offered version, Install button).
- **Install:** PackageInstaller session → Android's confirmation. First time, Android 8 needs "Install unknown apps" allowed for Orbit: the button turns into **Allow installs** and opens that switch (adb: `adb shell appops set com.orbit REQUEST_INSTALL_PACKAGES allow`).
- **Never during a recording or a Talk turn:** Install is disabled while a `.rec` file was written in the last 60 s / the recorder marker is live, or Talk is listening, sending/waiting for a reply, or speaking (incl. a history replay). It is re-enabled when that ends.
- **Data:** same package + same signing key (the debug keystore signs release builds) → files, prefs and the queue survive. After the update the launcher restarts as HOME and rebinds the notification listener as usual; a stale offer (≤ installed version) is dropped on start.

## Screens (see `design/VoiceRecord`, `VoiceInbox`, `VoiceServers`)

1. **Recorder:** idle (clock, big mic button 120 design-px, "2 notes waiting to send"), recording (red REC, mm:ss timer, file name, level ring around the rim from `getMaxAmplitude()`, big stop button), saved (check, length, "Queued · sends when online", New note).
2. **Inbox:** curved list, newest first, each row shows time, length and state; focused row shows upload progress.
3. **Servers:** list of found / paired servers, "Pair: scan QR".

Colors: background `#000000`, ink `#F2F1EC`, muted `#9A9A94`, surface `#1C1C1A`, accent `#FFB547`, recording red `#FF5A4E`, info cyan `#5CD6E0`. Fonts: Space Grotesk (numbers/titles), Inter (labels) — bundle as font resources.

## Acceptance tests (manual, on the watch)

1. 10-minute recording with screen off completes; file plays.
2. Force-stop the app during recording → on reopen the partial file is recovered or clearly flagged, never silently lost.
3. Away from home (LTE only) → record 3 notes → nothing uploads → join home Wi-Fi → all 3 upload exactly once within a minute.
3b. Change the PC's IP (or restart router) → watch finds it again by mDNS or scan.
3c. Run a fake server on another machine answering `/v1/health` without the right proof → watch never sends to it.
4. Kill network mid-upload → note resumes later and ends `sent`, no duplicate on server.
5. Server returns 409 → note goes `failed` with visible error, not deleted.
