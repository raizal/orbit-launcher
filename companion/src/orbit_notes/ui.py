"""Local web UI: JSON under /ui/api and the built Svelte app under /ui/.

Everything here is loopback-only. The server listens on the LAN for the watch,
but transcripts, audio and the pairing token must not be readable from it.
"""

from __future__ import annotations

import io
import json
import re
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import actions, appupdate, diary, insights
from .config import Config
from .inbox import shared as shared_inbox
from .inbox_api import mount_inbox_ui
from .store import Note, Store
from .talk import PERSONA, reply_paths

WEB_DIR = Path(__file__).parent / "web"  # built by `npm run build` in companion/web
PREVIEW_TEXT = "Hi, I'm Orbit. This is how I'll sound when I answer your voice notes."
PREVIEW_MAX = 300
SETTINGS_KEYS = {"voice", "length_scale", "personality", "reply_length"}
AUDIO_TYPES = {".m4a": "audio/mp4", ".aac": "audio/aac", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav"}
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def require_local(request: Request) -> None:
    if (request.client.host if request.client else None) not in LOOPBACK:
        raise HTTPException(403, "the Orbit Notes UI is only available on this computer")


def note_json(store: Store, n: Note) -> dict:
    audio = store.audio_path(n)
    txt = audio.with_suffix(".txt")
    reply_txt, reply_wav = reply_paths(store, n)
    talk = {
        "talk_status": n.talk_status,
        "talk_error": n.talk_error,
        "reply": reply_txt.read_text().strip() if reply_txt.exists() else None,
        "reply_audio": reply_wav.exists(),
    } if n.is_talk else {}
    return {
        "id": n.id,
        "stem": n.stem,
        "started_at": n.started_at,
        "time": n.started.strftime("%H:%M"),
        "duration_ms": n.duration_ms,
        "transcript_status": n.transcript_status,
        "transcript": txt.read_text().strip() if txt.exists() else None,
        "size": audio.stat().st_size if audio.exists() else None,
        "mode": n.mode,
        **talk,
    }


def mount_ui(app: FastAPI, cfg: Config, store: Store, enqueue: Callable[[str], None] | None,
             tts: Callable[..., None] | None = None, memory=None) -> None:
    """`tts` is the Talker's synthesizer (shares loaded voices); without one, a PiperTTS is made on first preview.
    `memory` is the server's memory.Memory (one is made if not given)."""
    api = APIRouter(prefix="/ui/api", dependencies=[Depends(require_local)])
    settings_store = actions.SettingsStore(cfg.data_dir, cfg.tts_voice)
    synth: list[Callable[..., None]] = [tts] if tts else []

    def get_tts() -> Callable[..., None]:
        if not synth:
            from .tts import PiperTTS

            synth.append(PiperTTS(cfg).synthesize)
        return synth[0]

    def voices() -> dict[str, dict]:
        return actions.voice_catalog(cfg.data_dir, cfg.tts_voice)

    def parse_day(value: str) -> date:
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise HTTPException(422, "date must be YYYY-MM-DD") from None

    @api.get("/status")
    def status():
        days = store.days()
        return {
            "name": cfg.name,
            "port": cfg.port,
            "notes": sum(d["count"] for d in days),
            "pending_transcripts": len(store.pending_transcripts()),
            "worker": enqueue is not None,
            "whisper_model": cfg.whisper_model,
            "llm_provider": cfg.llm_provider,
            "diary_dir": str(cfg.diary_dir),
            "vault_dir": str(cfg.vault_dir) if cfg.vault_dir else None,
            "app": (rel := appupdate.latest(cfg.data_dir)) and rel.public(),  # published watch build, or null
        }

    @api.get("/days")
    def days():
        return store.days()

    @api.get("/notes")
    def notes(day: str):
        return [note_json(store, n) for n in store.notes_on(parse_day(day))]

    @api.get("/notes/{note_id}/audio")
    def audio(note_id: str):
        n = store.get(note_id) or _404()
        path = store.audio_path(n)
        if not path.exists():
            _404()
        return FileResponse(path, media_type="audio/aac", filename=path.name)

    @api.get("/notes/{note_id}/reply.wav")
    def reply_audio(note_id: str):
        n = store.get(note_id) or _404()
        _, wav = reply_paths(store, n)
        if not n.is_talk or not wav.exists():
            _404()
        return FileResponse(wav, media_type="audio/wav")

    @api.post("/notes/{note_id}/transcribe")
    def retranscribe(note_id: str):
        n = store.get(note_id) or _404()
        if enqueue is None:
            raise HTTPException(409, "transcription worker is off (serve --no-worker)")
        store.set_transcript_status(n.id, "pending")
        enqueue(n.id)
        return {"queued": True}

    @api.get("/diary/{day}")
    def diary_get(day: str):
        page = cfg.diary_dir / f"{parse_day(day).isoformat()}.md"
        return {"path": str(page), "markdown": page.read_text() if page.exists() else None}

    @api.post("/diary/{day}")
    def diary_build(day: str):
        try:
            page = diary.build(cfg, store, parse_day(day))
        except Exception as e:  # LLM errors etc. — show them in the UI
            raise HTTPException(500, f"diary build failed: {e}") from e
        return {"path": str(page), "markdown": page.read_text()}

    @api.get("/vault/{rel:path}")
    def vault_audio(rel: str):
        """Audio embedded in a diary page (`![[Diary/audio/x.m4a]]`), resolved inside the vault only."""
        if not cfg.vault_dir or Path(rel).suffix.lower() not in AUDIO_TYPES:
            _404()
        root = cfg.vault_dir.resolve()
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            _404()
        return FileResponse(path, media_type=AUDIO_TYPES[path.suffix.lower()])

    @api.get("/talk")
    def talk_state():
        """Talk assistant: pending action (confirmed by voice only), audit log, current voice/personality settings."""
        log = actions.ActionLog(cfg.data_dir)
        p = log.pending()
        pending = {**asdict(p), "expires_at": p.expires_at.isoformat(), "expired": p.expired()} if p else None
        return {
            "workspace": cfg.talk_workspace,
            "pending": pending,
            "audit": log.entries(limit=50),
            "settings": asdict(settings_store.get()),
            "voices": voices(),
            "base_persona": re.sub(r"\n(?!\d\.)\s*", " ", PERSONA),  # unwrap source lines, keep "1." / "2."
            "limits": {"length_scale": [actions.LENGTH_SCALE_MIN, actions.LENGTH_SCALE_MAX],
                       "reply_lengths": actions.REPLY_LENGTHS, "personality": actions.PERSONALITY_MAX},
        }

    @api.put("/talk/settings")
    def talk_settings_put(body: dict = Body(...)):
        """Owner edits from this computer (loopback only): applied directly, no spoken confirmation. The next
        Talk turn reads talk_settings.json again, so the change is heard on the very next reply."""
        unknown = set(body) - SETTINGS_KEYS
        if unknown:
            raise HTTPException(422, f"unknown setting(s): {', '.join(sorted(unknown))}")
        try:
            changes = actions.validate_settings(body, voices=voices())
        except actions.Invalid as e:
            raise HTTPException(422, str(e)) from None
        s = settings_store.apply(changes)
        actions.ActionLog(cfg.data_dir).audit("changed", kind="settings", summary=f"Changed in the web UI: {', '.join(changes)}",
                                              params=changes)
        return asdict(s)

    @api.post("/talk/preview")
    def talk_preview(body: dict = Body(...)):
        """WAV of a short sample in the given voice/rate (unsaved values allowed), to listen before choosing."""
        text = " ".join(str(body.get("text") or PREVIEW_TEXT).split())[:PREVIEW_MAX]
        params = {k: body[k] for k in ("voice", "length_scale") if k in body}
        try:
            p = actions.validate_settings(params, voices=voices()) if params else {}
        except actions.Invalid as e:
            raise HTTPException(422, str(e)) from None
        current = settings_store.get()
        with tempfile.TemporaryDirectory(prefix="orbit-preview-") as tmp:
            out = Path(tmp) / "preview.wav"
            try:
                get_tts()(text, out, voice=p.get("voice", current.voice),
                          length_scale=p.get("length_scale", current.length_scale), strict=True)
            except Exception as e:  # noqa: BLE001 - e.g. the voice download failed: say so in the UI
                raise HTTPException(502, f"could not synthesize the preview: {e}") from e
            data = out.read_bytes()
        return Response(data, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @api.get("/mood")
    def mood(days: int = 30):
        """Private mood/energy estimates (docs/talk.md -> Briefing, reflection, mood). Loopback only, like all of
        /ui/api; never sent to the watch."""
        days = max(1, min(365, days))
        first = date.today() - timedelta(days=days - 1)
        log = insights.MoodLog(cfg.data_dir)
        rows = log.since(first)
        return {"from": first.isoformat(), "days": insights.daily_averages(rows), "unrated": log.unrated(first),
                "notes": [{k: r[k] for k in ("note_id", "local_date", "started_at", "mode", "mood", "energy",
                                             "confidence", "rationale", "source")} for r in reversed(rows)]}

    @api.get("/pairing.png")
    def pairing_png():
        import qrcode

        payload = json.dumps({"name": cfg.name, "port": cfg.port, "token": cfg.token}, separators=(",", ":"))
        buf = io.BytesIO()
        qrcode.make(payload, border=2).save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    from .memory import Memory
    from .memory_api import mount_memory_api

    mount_memory_api(api, memory or Memory(cfg, store))
    mount_inbox_ui(api, *shared_inbox(cfg))  # docs/inbox.md
    app.include_router(api)

    @app.get("/", include_in_schema=False, dependencies=[Depends(require_local)])
    def root():
        return RedirectResponse("/ui/")

    if WEB_DIR.is_dir():
        # html=True serves index.html for /ui/. Guarded by a small middleware below.
        app.mount("/ui", StaticFiles(directory=WEB_DIR, html=True), name="ui")

        @app.middleware("http")
        async def local_only_ui(request: Request, call_next):
            if request.url.path.startswith("/ui") and (request.client.host if request.client else None) not in LOOPBACK:
                return Response("the Orbit Notes UI is only available on this computer", status_code=403)
            return await call_next(request)


def _404():
    raise HTTPException(404, "no such note")
