"""HTTP API (see docs/voice-notes.md -> Upload API)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import defaultdict, deque
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from . import appupdate
from .config import Config
from .store import TALK_ACTIVE, Store, parse_started_at
from .talk import reply_paths

HEALTH_LIMIT = 30  # requests per IP per window
HEALTH_WINDOW_S = 60.0


def proof(token: str, nonce: str) -> str:
    return hmac.new(token.encode(), nonce.encode(), hashlib.sha256).hexdigest()


class RateLimiter:
    def __init__(self, limit: int, window_s: float):
        self.limit, self.window_s = limit, window_s
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


def create_app(
    cfg: Config,
    store: Store,
    on_stored: Callable[[str], None] = lambda note_id: None,
    lifespan=None,
) -> FastAPI:
    app = FastAPI(title="Orbit Notes", lifespan=lifespan)
    limiter = RateLimiter(HEALTH_LIMIT, HEALTH_WINDOW_S)

    def require_token(request: Request) -> None:
        scheme, _, given = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip().encode(), cfg.token.encode()):
            raise HTTPException(401, "invalid or missing token", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/v1/health")
    def health(request: Request, nonce: str = Query(min_length=1, max_length=128)):
        if not limiter.allow(request.client.host if request.client else "?"):
            raise HTTPException(429, "too many requests")
        return {"name": cfg.name, "api": 1, "proof": proof(cfg.token, nonce)}

    @app.head("/v1/notes/{note_id}", dependencies=[Depends(require_token)])
    def head_note(note_id: str):
        return Response(status_code=200 if store.get(note_id) else 404)

    @app.put("/v1/notes/{note_id}", dependencies=[Depends(require_token)])
    async def put_note(note_id: str, audio: UploadFile = File(...), meta: str = Form(...)):
        try:
            meta_obj = json.loads(meta)
            assert isinstance(meta_obj, dict)
            parse_started_at(meta_obj["started_at"])
            claimed = str(meta_obj["sha256"]).lower()
        except (ValueError, KeyError, TypeError, AssertionError) as e:
            raise HTTPException(422, f"bad meta: {e!r}")
        if meta_obj.get("id") != note_id:
            raise HTTPException(422, "meta.id does not match the URL id")

        data = await audio.read()
        actual = hashlib.sha256(data).hexdigest()
        if actual != claimed:
            raise HTTPException(422, f"sha256 mismatch: got {actual}")

        body = {"id": note_id, "sha256": actual}
        with store.lock:
            existing = store.get(note_id)
            if existing:
                if existing.sha256 == actual:
                    return JSONResponse(body, status_code=200)
                raise HTTPException(409, "id already stored with a different sha256")
            store.add(note_id, data, meta.encode(), meta_obj, audio.filename)
        on_stored(note_id)
        return JSONResponse(body, status_code=201)

    def talk_note(note_id: str):
        note = store.get(note_id)
        if not note or not note.is_talk:
            raise HTTPException(404, "no such talk note")
        return note

    @app.get("/v1/notes/{note_id}/reply", dependencies=[Depends(require_token)])
    def get_reply(note_id: str):
        """Talk feedback progress (docs/talk.md): 202 while working, 200 when done or failed."""
        note = talk_note(note_id)
        txt, wav = reply_paths(store, note)
        if note.talk_status == "done" and txt.exists() and wav.exists():
            transcript = store.audio_path(note).with_suffix(".txt")
            return {"status": "done", "text": txt.read_text().strip(), "audio": f"/v1/notes/{note_id}/reply.wav",
                    "transcript": transcript.read_text().strip() if transcript.exists() else ""}
        if note.talk_status == "error":
            return {"status": "error", "error": note.talk_error or "unknown error"}
        status = note.talk_status if note.talk_status in TALK_ACTIVE else "queued"
        return JSONResponse({"status": status}, status_code=202)

    @app.get("/v1/notes/{note_id}/reply.wav", dependencies=[Depends(require_token)])
    def get_reply_wav(note_id: str):
        _, wav = reply_paths(store, talk_note(note_id))
        if not wav.exists():
            raise HTTPException(404, "no reply audio (yet)")
        return FileResponse(wav, media_type="audio/wav")

    @app.get("/v1/talk/history", dependencies=[Depends(require_token)])
    def talk_history(limit: int = Query(20, ge=1, le=100)):
        """The latest conversation, oldest -> newest (docs/talk.md -> Conversations & history)."""
        conversation = store.latest_conversation()
        turns = []
        for note in store.conversation_turns(conversation, limit) if conversation else []:
            txt, wav = reply_paths(store, note)
            transcript = store.audio_path(note).with_suffix(".txt")
            done = note.talk_status == "done" and txt.exists()
            turns.append({
                "id": note.id,
                "started_at": note.started_at,
                "transcript": transcript.read_text().strip() if transcript.exists() else None,
                "status": note.talk_status or "queued",
                "reply": txt.read_text().strip() if done else None,
                "error": note.talk_error if note.talk_status == "error" else None,
                "audio": f"/v1/notes/{note.id}/reply.wav" if done and wav.exists() else None,
            })
        return {"conversation": conversation, "turns": turns}

    app.include_router(appupdate.router(cfg.data_dir, require_token))  # OTA watch app updates
    return app


def serve_lifespan(cfg: Config, worker, advertise: bool = True):
    """Start mDNS + the transcription worker with the server; stop them on shutdown."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from . import mdns

        adv = mdns.Advertiser(cfg.name, cfg.port) if advertise else None
        if adv:
            await adv.start()
        if worker:
            worker.start()
        try:
            yield
        finally:
            if worker:
                worker.stop()
            if adv:
                await adv.stop()

    return lifespan
