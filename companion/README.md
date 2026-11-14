# Orbit Notes (companion)

Receives voice notes from the Orbit watch app over the home LAN, transcribes them with
faster-whisper and writes one Markdown diary page per day (Obsidian friendly).
Spec: `../docs/companion.md`, `../docs/voice-notes.md`.

## Install

```sh
cd companion
uv sync            # Python 3.11+ is fetched by uv if needed
uv run pytest      # optional
```

## Run

```sh
uv run orbit-notes serve              # API on 0.0.0.0:8765 + mDNS (_orbitnotes._tcp) + transcription worker
uv run orbit-notes pair [--name home-pc]   # QR in the terminal + PNG at <data_dir>/pairing-qr.png
uv run orbit-notes transcribe [--all] # transcribe notes without a transcript (--all: redo every note)
uv run orbit-notes diary 2026-11-13   # build/rebuild one day's page
uv run orbit-notes diary --yesterday
```

All commands take `--config PATH`. The first `faster-whisper` use downloads the model
(`small` ≈ 500 MB) from Hugging Face; the server itself starts without it.

**Firewall:** allow inbound **TCP 8765 on the private/home network only** (e.g.
`sudo ufw allow from 192.168.1.0/24 to any port 8765 proto tcp`). mDNS uses UDP 5353.
Never expose this port to the internet; traffic is plain HTTP.

## Config

Looked up at `--config`, else `./config.toml`, else `~/.config/orbit-notes/config.toml`
(created on first run, mode 600). Relative paths are resolved against the config file's folder.
Every key can be overridden with `ORBIT_<KEY>` (e.g. `ORBIT_TOKEN`, `ORBIT_PORT`).

| key | default | |
|---|---|---|
| `name` | hostname | shown on the watch; mDNS instance name |
| `port` / `host` | `8765` / `0.0.0.0` | |
| `token` | generated on first run | bearer token + health proof key; in the pairing QR |
| `data_dir` | `~/.local/share/orbit-notes` | `notes/YYYY/MM/DD/*.aac/.json/.txt/.segments.json`, `index.sqlite` |
| `vault_dir` | unset | Obsidian vault; unset → pages go to `<data_dir>/diary/` |
| `diary_folder` | `Diary` | folder inside the vault |
| `whisper_model` | `small` | also `whisper_device` (`cpu`), `whisper_compute_type` (`int8`) |
| `diary_language` | `auto` | `auto` = language of the notes, or e.g. `English`, `Indonesian` |
| `llm_provider` | `none` | `none` (list + transcripts only) or `anthropic` (needs `ANTHROPIC_API_KEY`) |
| `llm_model` | `claude-sonnet-5-5` | |

**Diary pages:** only the part between `<!-- orbit:begin -->` and `<!-- orbit:end -->` (and the
`date`/`notes` frontmatter keys) is rewritten; your own text around it is kept.
**Audio links:** if the audio file is inside `vault_dir` (i.e. you put `data_dir` inside the vault)
the page uses a vault-relative `[[notes/…/x.aac]]` wikilink; otherwise an absolute `file://` link,
which Obsidian opens in the system player. Audio is not copied.

## Cron

```cron
30 4 * * * cd /path/to/orbit-launcher/companion && $HOME/.local/bin/uv run orbit-notes diary --yesterday >> /tmp/orbit-diary.log 2>&1
```

## Web UI (this computer only)

`orbit-notes serve` also serves a small page at **http://localhost:8765/ui/**: notes per day with audio
player and transcript, re-transcribe, build/view the diary page, and the pairing QR. It only answers
requests from this computer (the LAN gets 403), because it exposes transcripts and the token.

The page is a Svelte app in `web/`, built into `src/orbit_notes/web/` (committed, so serving needs no Node).
After changing it: `cd web && npm install && npm run build`. For live editing: `npm run dev` (proxies the API
to a running server on 8765).
