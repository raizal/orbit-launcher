"""Note storage: files under data/notes/YYYY/MM/DD/ + a sqlite index."""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import threading
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

STEM_RE = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}[+-]\d{4}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id                TEXT PRIMARY KEY,
    path              TEXT NOT NULL,      -- audio path relative to data_dir
    sha256            TEXT NOT NULL,
    started_at        TEXT NOT NULL,      -- ISO 8601 with the watch's offset
    started_utc       TEXT NOT NULL,      -- for ordering
    local_date        TEXT NOT NULL,      -- YYYY-MM-DD in the watch's offset
    duration_ms       INTEGER,
    status            TEXT NOT NULL DEFAULT 'stored',
    transcript_status TEXT NOT NULL DEFAULT 'pending',  -- pending / done / failed
    transcript_error  TEXT,
    received_at       TEXT NOT NULL,
    mode              TEXT,               -- sidecar `mode`: NULL = normal note, 'talk' = wants spoken feedback
    talk_status       TEXT,               -- talk notes: queued / transcribing / thinking / speaking / done / error
    talk_error        TEXT,
    conversation      TEXT                -- talk notes: the conversation they belong to (docs/talk.md)
);
CREATE INDEX IF NOT EXISTS notes_local_date ON notes(local_date);
CREATE TABLE IF NOT EXISTS talk_sessions (
    local_date TEXT PRIMARY KEY,          -- legacy (one Codex session per local date); read once by the migration
    session_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS talk_conversations (
    id          TEXT PRIMARY KEY,         -- "c-<hex>"; a conversation lasts until the watch starts a new one
    session_id  TEXT,                     -- its Codex session (NULL until the first turn ran)
    created_utc TEXT NOT NULL
);
"""

# Columns added after the first release; ALTERed into existing index.sqlite files on open.
MIGRATIONS = (("mode", "TEXT"), ("talk_status", "TEXT"), ("talk_error", "TEXT"), ("conversation", "TEXT"))
TALK_ACTIVE = ("queued", "transcribing", "thinking", "speaking")


@dataclass
class Note:
    id: str
    path: str
    sha256: str
    started_at: str
    local_date: str
    duration_ms: int | None
    transcript_status: str
    mode: str | None = None
    talk_status: str | None = None
    talk_error: str | None = None
    conversation: str | None = None

    @property
    def is_talk(self) -> bool:
        return self.mode == "talk"

    @property
    def started(self) -> datetime:
        return datetime.fromisoformat(self.started_at)

    @property
    def stem(self) -> str:
        return Path(self.path).stem


def parse_started_at(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("started_at must include a UTC offset")
    return dt


def stem_for(filename: str | None, started: datetime) -> str:
    """Use the watch's file name if it has the expected shape, else derive it from started_at."""
    stem = Path(filename or "").name.removesuffix(".aac")
    return stem if STEM_RE.fullmatch(stem) else started.strftime("%Y-%m-%d_%H-%M-%S%z")


def relative_dir(started: datetime) -> Path:
    """Date folder from the watch's own local date (its offset is kept, never converted)."""
    return Path("notes", f"{started:%Y}", f"{started:%m}", f"{started:%d}")


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()  # serialises check-then-store in PUT
        with closing(self._db()) as db, db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'notes'").fetchone()
            if exists:
                have = {r["name"] for r in db.execute("PRAGMA table_info(notes)")}
                for name, decl in MIGRATIONS:
                    if name not in have:
                        db.execute(f"ALTER TABLE notes ADD COLUMN {name} {decl}")
            db.executescript(SCHEMA)
            self._adopt_legacy_talk(db)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.data_dir / "index.sqlite")
        db.row_factory = sqlite3.Row
        return db

    def _query(self, sql: str, *args) -> list[Note]:
        cols = ("id, path, sha256, started_at, local_date, duration_ms, transcript_status, mode, talk_status,"
                " talk_error, conversation")
        with closing(self._db()) as db:
            return [Note(**dict(r)) for r in db.execute(sql.replace("*", cols, 1), args)]

    def get(self, note_id: str) -> Note | None:
        rows = self._query("SELECT * FROM notes WHERE id = ?", note_id)
        return rows[0] if rows else None

    def audio_path(self, note: Note) -> Path:
        return self.data_dir / note.path

    def add(self, note_id: str, audio: bytes, meta_raw: bytes, meta: dict, filename: str | None) -> Note:
        """Store audio + sidecar atomically, then index. Caller holds self.lock and checked the id is new."""
        started = parse_started_at(meta["started_at"])
        folder = relative_dir(started)
        stem = stem_for(filename, started)
        if (self.data_dir / folder / f"{stem}.aac").exists():  # same second, different note: never overwrite
            stem = f"{stem}_{note_id[:8]}"
        rel = folder / f"{stem}.aac"
        write_atomic(self.data_dir / rel, audio)
        write_atomic(self.data_dir / folder / f"{stem}.json", meta_raw)
        mode = meta.get("mode") if isinstance(meta.get("mode"), str) else None
        with closing(self._db()) as db, db:
            conversation = None
            if mode == "talk":
                latest = None if meta.get("conversation") == "new" else self._latest_conversation(db)
                conversation = latest or self._new_conversation(db)
            db.execute(
                "INSERT INTO notes (id, path, sha256, started_at, started_utc, local_date, duration_ms, received_at,"
                " mode, talk_status, conversation) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    note_id, rel.as_posix(), meta["sha256"].lower(), meta["started_at"],
                    started.astimezone(timezone.utc).isoformat(), started.date().isoformat(),
                    meta.get("duration_ms"), datetime.now(timezone.utc).isoformat(),
                    mode, "queued" if mode == "talk" else None, conversation,
                ),
            )
        return self.get(note_id)

    def pending_transcripts(self, include_done: bool = False) -> list[Note]:
        where = "" if include_done else "WHERE transcript_status != 'done'"
        return self._query(f"SELECT * FROM notes {where} ORDER BY started_utc")

    def days(self) -> list[dict]:
        """Local dates that have notes, newest first, with counts."""
        with closing(self._db()) as db:
            rows = db.execute(
                "SELECT local_date, COUNT(*) AS n FROM notes GROUP BY local_date ORDER BY local_date DESC"
            ).fetchall()
        return [{"date": r["local_date"], "count": r["n"]} for r in rows]

    def notes_on(self, day: date) -> list[Note]:
        return self._query("SELECT * FROM notes WHERE local_date = ? ORDER BY started_utc", day.isoformat())

    def set_transcript_status(self, note_id: str, status: str, error: str | None = None) -> None:
        with closing(self._db()) as db, db:
            db.execute(
                "UPDATE notes SET transcript_status = ?, transcript_error = ? WHERE id = ?",
                (status, error, note_id),
            )

    # --- talk ------------------------------------------------------------------------------------

    def set_talk_status(self, note_id: str, status: str, error: str | None = None) -> None:
        with closing(self._db()) as db, db:
            db.execute("UPDATE notes SET talk_status = ?, talk_error = ? WHERE id = ?", (status, error, note_id))

    def pending_talk(self) -> list[Note]:
        marks = ", ".join("?" * len(TALK_ACTIVE))
        return self._query(f"SELECT * FROM notes WHERE mode = 'talk' AND talk_status IN ({marks}) ORDER BY started_utc",
                           *TALK_ACTIVE)

    # --- talk conversations (docs/talk.md -> Conversations & history) ------------------------------

    @staticmethod
    def _new_conversation(db) -> str:
        cid = f"c-{uuid.uuid4().hex[:12]}"
        db.execute("INSERT INTO talk_conversations (id, session_id, created_utc) VALUES (?, NULL, ?)",
                   (cid, datetime.now(timezone.utc).isoformat()))
        return cid

    @staticmethod
    def _latest_conversation(db) -> str | None:
        row = db.execute("SELECT id FROM talk_conversations ORDER BY created_utc DESC, rowid DESC LIMIT 1").fetchone()
        return row["id"] if row else None

    def _adopt_legacy_talk(self, db) -> None:
        """Talk notes from before conversations existed join one conversation that keeps the newest daily session."""
        if not db.execute("SELECT 1 FROM notes WHERE mode = 'talk' AND conversation IS NULL LIMIT 1").fetchone():
            return
        cid = self._new_conversation(db)
        row = db.execute("SELECT session_id FROM talk_sessions ORDER BY local_date DESC LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE talk_conversations SET session_id = ? WHERE id = ?", (row["session_id"], cid))
        db.execute("UPDATE notes SET conversation = ? WHERE mode = 'talk' AND conversation IS NULL", (cid,))

    def latest_conversation(self) -> str | None:
        with closing(self._db()) as db:
            return self._latest_conversation(db)

    def conversation_session(self, conversation: str | None) -> str | None:
        if not conversation:
            return None
        with closing(self._db()) as db:
            row = db.execute("SELECT session_id FROM talk_conversations WHERE id = ?", (conversation,)).fetchone()
        return row["session_id"] if row else None

    def set_conversation_session(self, conversation: str, session_id: str) -> None:
        with closing(self._db()) as db, db:
            db.execute("UPDATE talk_conversations SET session_id = ? WHERE id = ?", (session_id, conversation))

    def conversation_turns(self, conversation: str, limit: int = 20) -> list[Note]:
        """The conversation's talk notes, oldest -> newest, keeping the newest `limit`."""
        rows = self._query("SELECT * FROM notes WHERE conversation = ? ORDER BY started_utc DESC LIMIT ?",
                           conversation, limit)
        return rows[::-1]
