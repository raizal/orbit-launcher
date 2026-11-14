# Orbit Notes — companion spec

Python 3.11+, FastAPI + uvicorn, Typer CLI, uv. One package `orbit_notes`, one CLI `orbit-notes`.

## Commands

```
orbit-notes serve                 # HTTP API + mDNS advertise + background transcription worker
orbit-notes pair [--name home-pc] # print pairing QR in the terminal (and save PNG)
orbit-notes transcribe [--all]    # transcribe notes that have no transcript yet
orbit-notes diary 2026-11-13      # build the diary page for one day
orbit-notes diary --yesterday     # for a daily cron / scheduled task
```

## Config (`config.toml` + env overrides)

Local network only — no public URL, no tunnel. Bind `0.0.0.0:8765`; tell Raiz to allow port 8765 in the OS firewall for the private network only.

`name`, `port` (default 8765), `data_dir`, `vault_dir`, `diary_folder`, `token` (generated on first run), `whisper_model`, `diary_language`, `llm_provider`, `llm_model`. Secrets via env (`ORBIT_TOKEN`, `ANTHROPIC_API_KEY`, …).

## API

Exactly as in `docs/voice-notes.md` → Upload API. Bearer token on every route except `/v1/health`, which must return `proof = hex(HMAC-SHA256(token, nonce))` so the watch can verify it is the real server. Rate-limit `/v1/health` per IP. QR payload: `{"name","port","token"}` (no IP). Verify sha256 of the received audio against `meta.sha256` before storing; reject mismatches with 422.

## Storage

```
data/notes/2026/11/13/2026-11-13_12-08-42+0700.aac
data/notes/2026/11/13/2026-11-13_12-08-42+0700.json
data/notes/2026/11/13/2026-11-13_12-08-42+0700.txt     # transcript
data/notes/2026/11/13/2026-11-13_12-08-42+0700.segments.json
data/index.sqlite                                        # id → path, status, sha256
```

Date folder comes from `started_at` in the sidecar, not upload time.

## mDNS

Advertise `_orbitnotes._tcp` on the LAN with TXT `name=<name>`, `api=1` using `zeroconf`.

## Transcription

`faster-whisper`, language auto-detect (Raiz may speak Indonesian, English, or mixed). Run as a background queue inside `serve`; also runnable via CLI. Store plain text + segments with timestamps.

## Diary

For one local date: collect that day's transcripts in time order → one LLM call → Markdown page at `<vault_dir>/<diary_folder>/2026-11-13.md`:

```markdown
---
date: 2026-11-13
notes: 3
---
# Thursday, 13 November 2026

<diary text written from the notes>

## Notes
- 07:02 · 0:48 — [[audio/2026-11-13_07-02-10+0700.aac]] — <one-line summary>
- …
```

Rules: never invent events not in the transcripts; keep the raw transcripts available (collapsed section or linked file); re-running for a date overwrites the generated part only. Language from config (open decision).

Also on the page (docs/talk.md -> "Briefing, reflection, mood"): a `## Reflection` section saved from Talk
after a spoken yes, between `<!-- orbit:reflection:begin/end -->` markers **after** the orbit block, so a
rebuild keeps it (and puts it back from `data_dir/reflections/<date>.md` if the page was deleted). The same
diary LLM call also returns a private mood/energy estimate per note (`summaries[].mood`), stored in the
`note_mood` table of `index.sqlite` — never written to the page.

## Tests

pytest for: idempotent PUT, 409 on sha mismatch, auth, storage path from `started_at` across midnight and time zones, diary grouping by local date.
