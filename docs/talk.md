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
  idle ("Tap to talk") → listening (red level ring + timer, tap to stop; ✕ = cancel: the recording
  is deleted on the watch, nothing reaches the companion, Talk closes — docs/voice-notes.md) →
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

## Workspace assistant (BB)

Talk is also Raiz's voice window into his BB workspace: he can ask how projects are going, and have the
companion start/message threads, create/update/comment tasks, save memories, or change Talk's voice and
personality. **Nothing changes without a spoken "yes" on a later turn.** Watch side: no change — it plays
whatever reply text/audio comes back.

Code: `companion/src/orbit_notes/workspace.py` (bb reads + execution), `actions.py` (allowlist, pending
action, audit, settings), `talk.py` (the turn). Tests: `companion/tests/test_workspace.py` (fake `bb` and
fake `codex` on PATH).

### Who does what

- The **companion** runs `bb … --json` as the owner (it runs natively on the host; `bb` must be on the
  service's PATH). It strips `BB_PROJECT_ID` / `BB_THREAD_ID` / `BB_ENVIRONMENT_ID` from the env so a
  companion started from inside a BB thread never acts as that thread.
- **Codex** stays read-only with no network: it only reads the evidence text and answers with JSON
  (`--output-schema`, also on `exec resume`). It never calls bb and never builds a command.
- Still one Codex session per local date. The persona goes in the first turn; the **rules, the current talk
  settings, the pending action and the last 5 audit entries go with every turn**, so a resumed session never
  runs on a stale persona/settings.

### Turn

1. While whisper transcribes, the worker calls `Talker.warm()` → the workspace **index** is built in the
   background (cached 3 min; `project list`, `thread list`, `tasks project list`, `tasks list` per task
   project, in parallel, ~3 s).
   Index = snapshot time; projects (id, name, repo path); the 30 most recently updated threads (title,
   project, status, "updated 3 h ago (Nov 14 07:10)", waiting-for-input); open tasks (≤ 40) and tasks
   finished in the last 7 days (≤ 15). Unreadable parts are listed as "NOT AVAILABLE (treat as no
   evidence)". An unchanged snapshot is not resent within the same session ("unchanged since 10:32").
2. Codex answers with:
   ```json
   {"reply": "…",
    "need_details": {"threads": ["thr_…"], "tasks": ["ABC-12"], "memory": [{"query": "…", "project": ""}], "projects": ["orbit-launcher"]},
    "confirmation": "confirm | cancel | none",
    "proposed_action": {"kind": "none | thread_spawn | thread_tell | task_create | task_update | task_comment | memory_add | settings",
                        "summary": "…", "params_json": "{…}"}}
   ```
3. **One detail round** at most: if `need_details` is non-empty the companion fetches it (≤ 4 threads →
   `thread log --limit=2`; ≤ 4 tasks → `tasks show` incl. subtasks, agent threads, last comments; ≤ 3
   memory searches, across every project unless one is named; ≤ 2 projects → `git log --all` + branch +
   uncommitted count), parallel, 45 s deadline, every item with its time, then resumes Codex once. A second
   `need_details` is ignored.
4. Confirmation / proposal handling (below), then Piper speaks the final text with the current settings.

Progress answers (prompt rules): say what is **done**, what **remains**, what is **uncertain**, each with
how recent the evidence is; an idle thread is not proof of completion; never claim more than the evidence;
say plainly when there is none.

### Confirm before acting

- `proposed_action` is validated against a fixed allowlist + per-kind param schema (`actions.validate`):
  unknown kinds or keys, unknown project / thread / task prefix, bad enums, lengths, dates → **rejected**;
  the reply is replaced by "I understood that you want me to …. But I can't: <reason>. Nothing was changed."
- A valid proposal becomes the **one pending action** (`data_dir/talk_pending.json`: id, kind, params,
  summary, created_at; expires after **10 min**). The reply says what was understood and what will change,
  and ends with a yes/no question (the companion appends "Shall I go ahead? Say yes to confirm, or no to
  cancel." if Codex didn't ask). A new proposal replaces the pending one, and the reply says so.
- Later turn, `confirmation: "confirm"` + a pending action that was shown to Codex this turn + not expired →
  the pending entry is cleared first (never runs twice), then the companion runs the exact command it builds
  from the validated params and appends the **real result** ("Done: I started the thread “X” in
  orbit-launcher, id thr_…" / "I tried, but it did not work: <bb error>."). Free text goes through stdin
  (`--prompt-file -`, `--message-file -`) or `--key=value`, so it can never be read as a flag:

  | kind | command |
  |---|---|
  | thread_spawn | `bb thread spawn --project=<id> --prompt-file - [--title=…] --json` |
  | thread_tell | `bb thread tell <thr_id> --message-file - --json` |
  | task_create | `bb tasks create --project=<PREFIX> --title=… [--description=… --priority=… --due=…] --json` |
  | task_update | `bb tasks update <KEY> [--status=… --priority=… --title=… --due=…] --json` (no description rewrite) |
  | task_comment | `bb tasks comment <KEY> --body=… --author=Orbit Talk --json` |
  | memory_add | `bb memory add --scope=… --name=… --summary=… --details=… --reason=… [--kind=…] --json` (project scope: env `BB_PROJECT_ID`) |
  | settings | local: `data_dir/talk_settings.json` |

- `cancel` → pending cleared, "cancelled". Expired → dropped at the start of the next turn and Codex is told;
  a late "yes" gets "That request expired after 10 minutes, so I did not do it". "Yes" with nothing pending
  → "There was nothing waiting for confirmation".
- Every `proposed / rejected / replaced / confirmed / executed / failed / cancelled / expired / ignored`
  event is appended to `data_dir/talk_audit.jsonl` (executed entries include the argv and bb's JSON).

### Voice & personality settings

`data_dir/talk_settings.json` (defaults: `tts_voice` from the config, rate 1.0, normal length, no extra
personality). Changed only through the same confirm flow (`kind: settings`); the confirming reply is
already spoken with the new settings.

- `voice`: curated English Piper voices — en_US-lessac-medium, en_US-amy-medium, en_US-ryan-high,
  en_US-joe-medium, en_US-kristin-medium, en_GB-alan-medium, en_GB-jenny_dioco-medium, en_GB-cori-high
  (all checked in piper-voices' `voices.json`). Downloaded into `data_dir/voices/` on first use; if a voice
  can't be loaded the reply falls back to the config voice.
- `length_scale` 0.7–1.6 (Piper; higher = slower), `reply_length` short / normal / long, `personality`
  free text ≤ 600 chars layered on the base persona (the fixed rules — no invented facts, confirm before
  acting — always stay).
- Not controllable here; the reply explains instead: watch volume (watch buttons / quick settings), wake
  word, interrupting a reply, non-English voices, the AI model / its knowledge, anything on the watch UI.

### Config, web UI

- `talk_workspace = "bb"` (default) or `"none"` (Talk stays a journaling companion, no bb calls).
- Web UI → **Talk assistant**: the pending action (with "confirm or cancel by voice — not from here"), the
  current settings, and the audit log (`GET /ui/api/talk`, loopback only).

### Measured (live check 2026-11-14, second instance on :8799, host under heavy load)

Upload → reply done: progress question with a detail round 40 s (whisper 13.6 s, index prefetched, Codex
7.5 s + details 1.3 s + Codex 9.5 s, TTS 7.3 s); proposal 39 s; cancel 41 s; settings proposal 26 s and
confirm 22 s; unsupported (volume) 82 s, of which whisper 66 s (machine load average ~20).
