"""Local web UI: JSON under /ui/api and the built Svelte app under /ui/.

Everything here is loopback-only. The server listens on the LAN for the watch,
but transcripts, audio and the pairing token must not be readable from it.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import diary
from .config import Config
from .store import Note, Store

WEB_DIR = Path(__file__).parent / "web"  # built by `npm run build` in companion/web
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def require_local(request: Request) -> None:
    if (request.client.host if request.client else None) not in LOOPBACK:
        raise HTTPException(403, "the Orbit Notes UI is only available on this computer")


def note_json(store: Store, n: Note) -> dict:
    audio = store.audio_path(n)
    txt = audio.with_suffix(".txt")
    return {
        "id": n.id,
        "stem": n.stem,
        "started_at": n.started_at,
        "time": n.started.strftime("%H:%M"),
        "duration_ms": n.duration_ms,
        "transcript_status": n.transcript_status,
        "transcript": txt.read_text().strip() if txt.exists() else None,
        "size": audio.stat().st_size if audio.exists() else None,
    }


def mount_ui(app: FastAPI, cfg: Config, store: Store, enqueue: Callable[[str], None] | None) -> None:
    api = APIRouter(prefix="/ui/api", dependencies=[Depends(require_local)])

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

    @api.get("/pairing.png")
    def pairing_png():
        import qrcode

        payload = json.dumps({"name": cfg.name, "port": cfg.port, "token": cfg.token}, separators=(",", ":"))
        buf = io.BytesIO()
        qrcode.make(payload, border=2).save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

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
