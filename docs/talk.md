# Talk — journaling with spoken feedback

You talk to the watch like a diary; the companion answers with short spoken feedback
(reflection, maybe one gentle question). Not a fast chat: ~30 s from "stop" to hearing the
reply is fine. Every talk turn is also a normal voice note, so it lands in the diary.

## Flow

1. **Watch** — Talk screen: tap to start, tap to stop (toggle, not hold). Recording uses the
   normal `RecorderService` (same ADTS file, same never-lose rules). The sidecar gets
   `"mode": "talk"`. After stop the upload queue is kicked right away (`Uploads.kick(fresh)`).
2. **Companion** — `PUT /v1/notes/{id}` stores it as usual. If `meta.mode == "talk"`:
   transcribe it first (ahead of the normal queue) → feedback text via Codex → speech via Piper
   → files next to the note. Progress is visible through the reply endpoint.
3. **Watch** — polls the reply endpoint (every 1.5 s, give up after 3 min), shows the status,
   then plays the reply audio and shows the text. "Talk again" starts the next turn.

Not on home Wi-Fi / companion not found → the note simply stays queued like any note and the
screen says "Saved — feedback when you're home". No feedback is generated later for it unless
it is still the same day (then it is, on upload; the watch just won't be waiting).

## API (bearer token, like the other `/v1` routes)

```
GET /v1/notes/{id}/reply
  404                                   unknown id, or not a talk note
  202 {"status": "queued" | "transcribing" | "thinking" | "speaking"}
  200 {"status": "done", "text": "…", "audio": "/v1/notes/{id}/reply.wav", "transcript": "…"}
  200 {"status": "error", "error": "…"}        (e.g. codex failed; the note itself is safe)

GET /v1/notes/{id}/reply.wav            audio/wav (Piper output, mono)
```

## Companion

- Sidecar field `mode` (optional; absent = normal note). Store it in the index (`mode` column)
  and a `talk_status` / `talk_error` column.
- Files: `<stem>.reply.txt`, `<stem>.reply.wav` next to the note.
- **Feedback** — `codex exec` in lean mode: `--skip-git-repo-check --ignore-user-config
  --ignore-rules -s read-only -c model_reasoning_effort=low`, working dir `data/talk/`.
  **One Codex session per local date**: first talk turn of the day starts a session (capture its
  id from `--json` output), later turns use `codex exec resume <id>` (resume takes no `-s`/`-C`: it gets `-c sandbox_mode="read-only"` and runs in `data/talk/`). Session ids stored in the
  index (`talk_sessions(local_date, session_id)`). First turn's prompt includes the companion
  persona, yesterday's diary text (if a page exists), and today's earlier transcripts.
- Persona: warm, thoughtful journaling companion. Reply in **English**, 2–4 short sentences,
  plain spoken text (no markdown, lists or emoji — it is read aloud). Reflect back what was
  said; at most one gentle question; never invent facts about the user's life.
- **TTS** — Piper (`piper-tts`), English voice from config (`tts_voice`, default
  `en_US-lessac-medium`), voice files downloaded once into `data_dir/voices/`.
- Config keys: `talk_provider = "codex"`, `tts_voice`, `talk_timeout_s = 120`.
- Diary page: a talk note's transcript callout also shows `**Feedback:** <reply text>`.
- Web UI: talk notes show a "Talk" badge, the reply text, and a second player for the reply.
- Tests: reply endpoint states; per-day session reuse (fake `codex` on PATH, like the diary
  tests); TTS stubbed.

## Watch

- `TalkActivity` in `:feature-voice` (own design, same visual language as `design/VoiceRecord`:
  400-unit grid, `RoundScreen`, Orbit colors/fonts). States:
  idle ("Tap to talk") → listening (red level ring + timer, tap to stop) →
  sending / thinking (cyan, status text from the server) → speaking (accent ring, reply text,
  audio playing) → done (reply text + "Talk again"). Rotary scrolls long reply text.
- Entry: a "Talk" pill on the recorder's idle screen, and a `com.orbit.action.TALK` action.
- Sidecar `mode: "talk"` — `NoteStore.finalize` gets an optional mode; `NoteMeta.mode: String? = null`.
- Reply playback with `MediaPlayer` from the companion (bearer header), last known IP from
  `ServerPrefs`, bound to the Wi-Fi network.

## Conversations & history (continue where you left off)

Opening Talk shows the latest conversation and talking continues it. Codex keeps one session per
**conversation** (not per day): a conversation continues until the user starts a new one, across
midnight too. Diary grouping is unchanged (by the note's local date).

### Sidecar
- `"mode": "talk"` as before.
- `"conversation": "new"` (optional) on the first turn after the user tapped **New** — the companion
  starts a new conversation (new Codex session) with this turn. Absent → continue the latest one.

### API (bearer)
```
GET /v1/talk/history?limit=20
  200 {
    "conversation": "c-…" | null,            # id of the latest conversation (null = none yet)
    "turns": [                               # that conversation, oldest → newest (≤ limit, newest kept)
      { "id": "<note id>", "started_at": "2026-11-14T21:22:42+07:00",
        "transcript": "…" | null,            # null while not transcribed yet
        "status": "queued|transcribing|thinking|speaking|done|error",
        "reply": "…" | null, "error": "…" | null,
        "audio": "/v1/notes/<id>/reply.wav" | null }
    ]
  }
```
`/v1/notes/{id}/reply` and `/reply.wav` are unchanged.

### Watch
- **On open**: show the locally cached conversation immediately, then refresh from
  `/v1/talk/history` when the companion is reachable (merge by note id; the server is the
  source of truth for transcript/reply/status).
- **Layout**: scrollable history (crown + drag), newest at the bottom, auto-scrolled to the end;
  each turn = your words (2 lines max, muted) and the reply (full text, ink) with a ▶ button
  that replays its audio. Below the history: the big talk button ("Tap to talk"); a small
  **New** button starts a fresh conversation (clears the view; the next turn's sidecar carries
  `"conversation": "new"`).
- **Local cache**: when a reply arrives, keep its text and audio on the watch
  (`files/talk/<stem>.reply.json`, `<stem>.reply.wav`, internal storage — not the notes folder,
  so the notes folder stays audio + sidecars only) plus a small `files/talk/conversation.json`
  (current conversation id + ordered note stems). Replays use the cached audio, so they work
  away from home. Keep the last 50 turns; delete older cached replies (never the notes themselves).
- A turn recorded away from home shows as "Saved — feedback when you're home" in the history and
  fills in once the reply exists.
