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

`location` is optional and coarse (last known location only; never wait for a GPS fix).

## Queue & states

`recorded → waiting → sending (progress) → sent → (deleted after 7 days)`. Also `failed` with last error, which keeps retrying.

- **The server is local only.** Uploads happen only when the watch is on the paired home Wi-Fi — never on LTE, never on other Wi-Fi networks. Notes recorded outside just wait in the queue.
- One WorkManager unique work per note (`upload-<id>`), constraint `UNMETERED`, exponential backoff 30 s → 1 h. Also trigger the queue when the home Wi-Fi connects (`ConnectivityManager.NetworkCallback`).
- Mark `sent` only when the server returns the same sha256.
- **Never delete an unsent note.** Sent notes are kept 7 days, then deleted by a daily cleanup job.

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
