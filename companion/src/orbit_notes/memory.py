"""Memory & recall (docs/memory.md): episodic index, recall evidence for Talk, distilled facts, Obsidian view.

SQLite (`<data_dir>/memory.sqlite`) is the source of truth. Everything is derived from the notes on disk
(transcripts, talk replies, diary pages, reflections) except the facts, which are distilled from Codex calls
that already happen (the talk turn's JSON, the diary build) and corrected by the owner (voice / web UI).

- Episodic index: chunks of ~1–3 sentences with their local date/time and source; SQLite FTS5 (BM25) for
  keywords + a small local multilingual embedding model (multilingual-e5-small, int8 ONNX, CPU) for meaning;
  vectors are float32 BLOBs, brute-force cosine; the two result lists are merged by reciprocal rank fusion.
- Indexing is cheap and incremental: text goes into FTS right away (any process, e.g. the `diary` cron);
  vectors are filled by a low-priority background thread inside `serve`. The model is loaded lazily, so
  `serve` starts fast.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import sqlite3
import threading
import time
import traceback
import unicodedata
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .store import Note, Store, write_atomic

# --- embedding model -------------------------------------------------------------------------------

# int8 ONNX export of intfloat/multilingual-e5-small (MIT, 100 languages incl. Indonesian; 384 dims).
# ~118 MB, downloaded once into the Hugging Face cache (~/.cache/huggingface/hub, shared with whisper).
MODELS = {
    "multilingual-e5-small": {"repo": "Xenova/multilingual-e5-small",
                              "revision": "761b726dd34fb83930e26aab4e9ac3899aa1fa78",
                              "file": "onnx/model_quantized.onnx", "dim": 384},
}
DEFAULT_MODEL = "multilingual-e5-small"
MAX_TOKENS = 256


class E5Embedder:
    """multilingual-e5 via onnxruntime + tokenizers (both already installed for faster-whisper).
    `threads=1` runs the model on the calling thread, so the background indexer's low priority applies."""

    def __init__(self, name: str = DEFAULT_MODEL, threads: int = 1):
        self.name, self.threads = name, threads
        self.spec = MODELS[name]
        self.dim = self.spec["dim"]
        self._session = self._tok = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self._session is not None:
                return
            from huggingface_hub import hf_hub_download  # heavy imports: first use only
            import onnxruntime as ort
            import tokenizers

            t = time.monotonic()
            repo, rev = self.spec["repo"], self.spec["revision"]
            model = hf_hub_download(repo, self.spec["file"], revision=rev)
            tok = tokenizers.Tokenizer.from_file(hf_hub_download(repo, "tokenizer.json", revision=rev))
            tok.enable_truncation(MAX_TOKENS)
            tok.enable_padding()
            so = ort.SessionOptions()
            so.intra_op_num_threads, so.inter_op_num_threads = self.threads, 1
            so.log_severity_level = 3
            self._session = ort.InferenceSession(model, so, providers=["CPUExecutionProvider"])
            self._inputs = {i.name for i in self._session.get_inputs()}
            self._tok = tok
            print(f"memory: embedding model {self.name} loaded in {time.monotonic() - t:.1f}s ({model})")

    def embed(self, texts: list[str], kind: str = "passage"):
        """Unit vectors (float32, one row per text). kind: "query" or "passage" (e5's prefixes)."""
        import numpy as np

        self.load()
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))  # similar lengths -> less padding
        for start in range(0, len(order), 16):
            idx = order[start:start + 16]
            enc = self._tok.encode_batch([f"{kind}: {texts[i]}" for i in idx])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self._session.run(None, feed)[0]
            v = (hidden * mask[..., None]).sum(1) / mask.sum(1, keepdims=True)
            out[idx] = v / np.linalg.norm(v, axis=1, keepdims=True)
        return out


def make_embedder(cfg):
    """None = keyword search only (`memory_embedder = "none"`). Tests replace this with a fake."""
    name = getattr(cfg, "memory_embedder", DEFAULT_MODEL)
    if not name or name == "none":
        return None
    if name not in MODELS:
        raise ValueError(f"unknown memory_embedder {name!r}; use one of {', '.join(MODELS)} or \"none\"")
    return E5Embedder(name)


# --- text helpers ------------------------------------------------------------------------------------

SENT_RE = re.compile(r"(?<=[.!?…])\s+")
WORD_RE = re.compile(r"\w+", re.U)
CHUNK_TARGET, CHUNK_SHORT = 320, 480  # chars: ~1–3 spoken sentences; a short note stays one chunk

# English + Indonesian function words, recall verbs and date words: they say nothing about WHAT to find.
STOPWORDS = set("""
a an the and or but if of to in on at by for from with about as is am are was were be been being do does did
done have has had i me my mine myself you your he him his she her it its we us our they them their this that
these those what which who whom whose when where why how there here so not no yes just very really also too
can could will would shall should may might must again ever never all any some more most much many each other
than then now today yesterday tomorrow week weeks month months year years day days ago last past next before
after earlier lately recently time times morning evening night tonight remember recall said say says saying
tell told talk talked talking mention mentioned think thought know knew orbit please okay ok um uh like
going go goes went get got getting want wanted need needed make made thing things stuff something anything
actually yeah yep right maybe still already lot lots kind sort bit pretty quite one two us let lets gonna
wanna would could should might sure good great nice cool well even only into out up down over then because
monday tuesday wednesday thursday friday saturday sunday
yang dan di ke dari ini itu aku saya gue gw kamu anda dia mereka kita kami apa siapa kapan dimana mana kenapa
mengapa bagaimana gimana ada tidak nggak gak enggak ya sudah udah belum akan mau bisa juga lagi saja aja dong
sih kok deh nih tuh untuk dengan pada oleh karena jadi kalau kalo tapi atau hari kemarin besok minggu bulan
yg gak ga dah lah kan banget sama terus trus abis habis biar supaya lebih masih baru mungkin kayak kaya
tahun lalu tadi sekarang pernah ingat inget bilang ngomong cerita katanya kata soal tentang
senin selasa rabu kamis jumat sabtu
""".split())


def normalize(text: str) -> str:
    """Casefolded, accents stripped: for forget-matching and duplicate detection."""
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def content_words(text: str) -> list[str]:
    seen, out = set(), []
    for w in WORD_RE.findall(normalize(text)):
        if len(w) >= 2 and w not in STOPWORDS and not w.isdigit() and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def chunk_text(text: str) -> list[str]:
    """~1–3 sentences per chunk; a short note is one chunk; run-on speech is split at word boundaries."""
    text = " ".join(text.split())
    if not text:
        return []
    if len(text) <= CHUNK_SHORT:
        return [text]
    pieces = []
    for s in SENT_RE.split(text):
        while len(s) > 2 * CHUNK_TARGET:
            cut = s.rfind(" ", 0, CHUNK_TARGET + 40)
            cut = cut if cut > CHUNK_TARGET // 2 else CHUNK_TARGET
            pieces.append(s[:cut].strip())
            s = s[cut:].strip()
        if s:
            pieces.append(s)
    out, cur = [], ""
    for p in pieces:
        if cur and len(cur) + 1 + len(p) > CHUNK_TARGET:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        if out and len(cur) < 60:
            out[-1] = f"{out[-1]} {cur}"
        else:
            out.append(cur)
    return out


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def fmt_day(d: str) -> str:
    return f"{date.fromisoformat(d):%a %d %b %Y}"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read(path: Path) -> str | None:
    return path.read_text().strip() if path.exists() else None


# --- date ranges in recall questions ------------------------------------------------------------------

MONTHS = {m: i + 1 for i, names in enumerate([
    ("january", "januari", "jan"), ("february", "februari", "feb"), ("march", "maret", "mar"),
    ("april", "apr"), ("may", "mei"), ("june", "juni", "jun"), ("july", "juli", "jul"),
    ("august", "agustus", "aug", "agu"), ("september", "sept", "sep"), ("october", "oktober", "oct", "okt"),
    ("november", "nov"), ("december", "desember", "dec", "des")]) for m in names}
WEEKDAYS = {w: i for i, names in enumerate([("monday", "senin"), ("tuesday", "selasa"), ("wednesday", "rabu"),
                                            ("thursday", "kamis"), ("friday", "jumat", "jum'at"),
                                            ("saturday", "sabtu"), ("sunday", "minggu")]) for w in names}


def parse_range(text: str, today: date) -> tuple[date, date] | None:
    """A date range named in the question ("last week", "kemarin", "in October", "3 days ago"), or None."""
    t = normalize(text)
    monday = today - timedelta(days=today.weekday())
    if re.search(r"\b(day before yesterday|kemarin lusa)\b", t):
        d = today - timedelta(days=2)
        return d, d
    if re.search(r"\b(yesterday|kemarin|kemaren)\b", t):
        d = today - timedelta(days=1)
        return d, d
    if m := re.search(r"\b(?:last|past|terakhir) (\d{1,3}) days\b|\b(\d{1,3}) hari terakhir\b", t):
        n = int(m.group(1) or m.group(2))
        return today - timedelta(days=n), today
    if m := re.search(r"\b(\d{1,3}) (days?|hari) (ago|lalu|yang lalu)\b", t):
        d = today - timedelta(days=int(m.group(1)))
        return d, d
    if re.search(r"\b(last week|minggu lalu|minggu kemarin|pekan lalu|seminggu (yang )?lalu)\b", t):
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if re.search(r"\b(this week|minggu ini|pekan ini)\b", t):
        return monday, today
    if re.search(r"\b(last month|bulan lalu|bulan kemarin)\b", t):
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if re.search(r"\b(this month|bulan ini)\b", t):
        return today.replace(day=1), today
    if re.search(r"\b(today|hari ini|tadi)\b", t):
        return today, today
    for word in WORD_RE.findall(t):
        if word in MONTHS and len(word) > 3 and re.search(rf"\b(in|on|during|bulan|pada|di) {word}\b", t):
            month = MONTHS[word]
            year = today.year if month <= today.month else today.year - 1
            start = date(year, month, 1)
            end = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1))
            return start, min(end, today)
    for word in WORD_RE.findall(t):
        if word in WEEKDAYS and re.search(rf"\b(on|last|hari|pada|tanggal)? ?{word}\b", t) and word != "minggu":
            back = (today.weekday() - WEEKDAYS[word]) % 7 or 7
            d = today - timedelta(days=back)
            return d, d
    return None


RECALL_RE = re.compile(
    r"\b(remember|recall|remind me|did i (say|mention|tell|talk|write|record)|what did i|when did i|"
    r"have i (ever )?(said|mentioned|talked|told)|last time i|what was i|ingat|inget|pernah|kapan (aku|saya|gue|gw)|"
    r"(aku|saya|gue|gw) (bilang|ngomong|cerita) apa|apa yang (aku|saya|gue) (bilang|katakan|omongin|ceritain))\b",
    re.I)


def is_recall_question(text: str) -> bool:
    return bool(RECALL_RE.search(text))


# --- schema ------------------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS chunks (
    id         INTEGER PRIMARY KEY,
    source     TEXT NOT NULL,      -- note (transcript) | reply (Orbit's talk reply) | diary (page prose) | reflection
    ref        TEXT NOT NULL,      -- note id, or YYYY-MM-DD for diary / reflection
    seq        INTEGER NOT NULL,
    note_id    TEXT,
    local_date TEXT NOT NULL,
    at         TEXT,               -- ISO 8601 with the watch's offset; NULL for day-level sources
    mode       TEXT,               -- 'talk' for talk notes
    text       TEXT NOT NULL,
    excluded   INTEGER NOT NULL DEFAULT 0,   -- 1 = forgotten: never retrieved
    vec        BLOB,               -- float32 unit vector; NULL until the background embedder ran
    UNIQUE (source, ref, seq)
);
CREATE INDEX IF NOT EXISTS chunks_date ON chunks(local_date);
CREATE INDEX IF NOT EXISTS chunks_note ON chunks(note_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize = 'unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS sources (source TEXT, ref TEXT, sha TEXT, indexed_at TEXT, PRIMARY KEY (source, ref));
CREATE TABLE IF NOT EXISTS facts (
    id             TEXT PRIMARY KEY,   -- f-xxxxxx
    text           TEXT NOT NULL,
    topic          TEXT NOT NULL,
    kind           TEXT NOT NULL,      -- fact | preference | goal | decision | person | place
    sources        TEXT NOT NULL DEFAULT '[]',  -- JSON [{note_id, local_date, via}]
    first_seen     TEXT NOT NULL,      -- YYYY-MM-DD (local)
    last_confirmed TEXT NOT NULL,
    confidence     TEXT NOT NULL,      -- medium | high
    status         TEXT NOT NULL DEFAULT 'active',  -- active | corrected | retired
    history        TEXT NOT NULL DEFAULT '[]',      -- JSON [{at, event, ...}]
    updated_at     TEXT NOT NULL,
    vec            BLOB
);
CREATE TABLE IF NOT EXISTS tags (
    note_id TEXT NOT NULL, local_date TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, via TEXT NOT NULL,
    PRIMARY KEY (note_id, kind, name)
);
CREATE INDEX IF NOT EXISTS tags_name ON tags(name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS forgotten (
    id INTEGER PRIMARY KEY, term TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, chunks INTEGER, facts INTEGER
);
"""

FACT_KINDS = ("fact", "preference", "goal", "decision", "person", "place")
TAG_KINDS = ("person", "place", "project", "topic")
FACT_STATUSES = ("active", "corrected", "retired")
FACT_ID_RE = re.compile(r"f-[0-9a-f]{6}")
FACT_TEXT_MIN, FACT_TEXT_MAX, TOPIC_MAX, TAG_MAX = 5, 300, 60, 60
MAX_FACTS_PER_NOTE, MAX_TAGS_PER_NOTE = 5, 12
ALL_FACTS_LIMIT = 40  # up to this many active facts go into every turn; above it, the relevant ones


@dataclass
class Hit:
    id: int
    source: str
    ref: str
    note_id: str | None
    local_date: str
    at: str | None
    mode: str | None
    text: str
    score: float = 0.0
    sim: float | None = None
    keyword: bool = False

    def when(self) -> str:
        if self.at:
            return f"{datetime.fromisoformat(self.at):%a %d %b %Y %H:%M}"
        return fmt_day(self.local_date)

    def label(self) -> str:
        return {"note": "talk turn, his words" if self.mode == "talk" else "voice note",
                "reply": "Orbit's reply to him", "diary": "diary page", "reflection": "his saved reflection"}[self.source]

    def json(self) -> dict:
        return {"id": self.id, "source": self.source, "ref": self.ref, "note_id": self.note_id,
                "local_date": self.local_date, "at": self.at, "mode": self.mode, "text": self.text,
                "score": round(self.score, 5), "sim": None if self.sim is None else round(self.sim, 3),
                "keyword": self.keyword, "when": self.when(), "label": self.label()}


@dataclass
class Fact:
    id: str
    text: str
    topic: str
    kind: str
    sources: list = field(default_factory=list)
    first_seen: str = ""
    last_confirmed: str = ""
    confidence: str = "medium"
    status: str = "active"
    history: list = field(default_factory=list)
    updated_at: str = ""

    @classmethod
    def from_row(cls, r) -> "Fact":
        return cls(r["id"], r["text"], r["topic"], r["kind"], json.loads(r["sources"]), r["first_seen"],
                   r["last_confirmed"], r["confidence"], r["status"], json.loads(r["history"]), r["updated_at"])

    def line(self) -> str:
        seen = fmt_day(self.first_seen)
        conf = "" if self.last_confirmed == self.first_seen else f", last confirmed {fmt_day(self.last_confirmed)}"
        return f"- {self.id} — {self.text} [{self.topic}, {self.kind}; first heard {seen}{conf}]"

    def json(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "text", "topic", "kind", "sources", "first_seen",
                                               "last_confirmed", "confidence", "status", "history", "updated_at")}


class Invalid(ValueError):
    """Bad memory action / edit; the message is spoken or shown to the owner."""


# --- the store -------------------------------------------------------------------------------------


class Memory:
    def __init__(self, cfg, store: Store, embedder="default"):
        self.cfg, self.store = cfg, store
        self.path = Path(cfg.data_dir) / "memory.sqlite"
        self.embedder = make_embedder(cfg) if embedder == "default" else embedder
        self._cache: tuple | None = None
        self._cache_lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        with closing(self._db()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            model = self.embedder.name if self.embedder else "none"
            row = db.execute("SELECT value FROM meta WHERE key = 'model'").fetchone()
            if row and row["value"] != model and model != "none":
                db.execute("UPDATE chunks SET vec = NULL")  # another model's vectors are meaningless
                db.execute("UPDATE facts SET vec = NULL")
            if model != "none":
                db.execute("INSERT OR REPLACE INTO meta VALUES ('model', ?)", (model,))

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 30000")
        return db

    # --- background indexer (inside `serve`) ---------------------------------------------------------

    def start(self) -> None:
        """Catch up with the archive, then index notes as they are transcribed / answered, at low priority."""
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="memory", daemon=True)
        self._thread.start()
        self._queue.put(("sync", None))

    def stop(self) -> None:
        if self._thread:
            self._queue.put(("stop", None))

    def enqueue_note(self, note_id: str) -> None:
        if self._thread:
            self._queue.put(("note", note_id))
        else:
            self._safe(lambda: self.index_note_id(note_id))

    def warm(self) -> None:
        """Load the embedding model in the background (called while whisper runs for a talk note)."""
        if self.embedder and getattr(self.embedder, "_session", True) is None:
            threading.Thread(target=self._safe, args=(self.embedder.load,), name="memory-warm", daemon=True).start()

    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001 - memory must never take the companion down
            traceback.print_exc()

    def _loop(self) -> None:
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)  # Linux: per-thread nice
        except (OSError, AttributeError):
            pass
        while True:
            try:
                job, arg = self._queue.get(timeout=300)
            except queue.Empty:
                job, arg = "embed", None
            if job == "stop":
                return
            if job == "sync":
                t = time.monotonic()
                n = self._safe(self.sync) or 0
                print(f"memory: archive sync {n} source(s) changed in {time.monotonic() - t:.1f}s")
            elif job == "note":
                self._safe(lambda: self.index_note_id(arg))
            elif job == "day":
                self._safe(lambda: self.index_day(arg))
            if self.embedder:
                t = time.monotonic()
                n = self._safe(self.embed_pending) or 0
                if n:
                    print(f"memory: embedded {n} chunk(s) in {time.monotonic() - t:.1f}s")

    # --- indexing (text only; cheap; any process) ---------------------------------------------------

    def _forgotten_res(self, db) -> list[re.Pattern]:
        return [term_re(r["term"]) for r in db.execute("SELECT term FROM forgotten")]

    def _put_source(self, db, source: str, ref: str, text: str | None, *, local_date: str, at: str | None,
                    note_id: str | None, mode: str | None, forgotten) -> bool:
        """(Re)index one source if its text changed. Returns True when something changed."""
        digest = sha(text) if text else ""
        row = db.execute("SELECT sha FROM sources WHERE source = ? AND ref = ?", (source, ref)).fetchone()
        if row and row["sha"] == digest:
            return False
        old = [r["id"] for r in db.execute("SELECT id FROM chunks WHERE source = ? AND ref = ?", (source, ref))]
        for cid in old:
            db.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
        db.execute("DELETE FROM chunks WHERE source = ? AND ref = ?", (source, ref))
        for seq, piece in enumerate(chunk_text(text or "")):
            excluded = int(any(p.search(normalize(piece)) for p in forgotten))
            cur = db.execute("INSERT INTO chunks (source, ref, seq, note_id, local_date, at, mode, text, excluded)"
                             " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (source, ref, seq, note_id, local_date, at, mode, piece, excluded))
            db.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, piece))
        db.execute("INSERT OR REPLACE INTO sources VALUES (?, ?, ?, ?)", (source, ref, digest, now_utc()))
        return True

    def index_note(self, note: Note, db=None) -> int:
        """Transcript + (talk) reply of one note."""
        audio = self.store.audio_path(note)
        transcript = read(audio.with_suffix(".txt")) if note.transcript_status == "done" else None
        reply = read(audio.with_name(f"{note.stem}.reply.txt")) if note.is_talk else None
        own = db is None
        db = db or self._db()
        try:
            forgotten = self._forgotten_res(db)
            kw = dict(local_date=note.local_date, at=note.started_at, note_id=note.id, mode=note.mode,
                      forgotten=forgotten)
            n = self._put_source(db, "note", note.id, transcript, **kw)
            n += self._put_source(db, "reply", note.id, reply, **kw)
            if own:
                db.commit()
            return n
        finally:
            if own:
                db.close()

    def index_note_id(self, note_id: str) -> int:
        note = self.store.get(note_id)
        return self.index_note(note) if note else 0

    def diary_text(self, day: date) -> str | None:
        """The page's own prose (the orbit block before `## Notes`), without headings and link lines."""
        from . import diary  # late import: diary imports this module

        page = self.cfg.diary_dir / f"{day.isoformat()}.md"
        if not page.exists():
            return None
        text = page.read_text()
        start, end = text.find(diary.BEGIN), text.find(diary.END)
        if start == -1 or end < start:
            return None
        body = text[start + len(diary.BEGIN):end].split("\n## ", 1)[0]
        lines = [ln for ln in body.splitlines() if ln.strip() and not ln.startswith("# ")
                 and not ln.startswith(MENTIONS_PREFIX)]
        prose = "\n".join(lines).strip()
        return None if not prose or prose.startswith("_No ") else prose

    def index_day(self, day: date, db=None) -> int:
        """Diary page prose + saved reflection of one day."""
        from . import insights

        own = db is None
        db = db or self._db()
        try:
            forgotten = self._forgotten_res(db)
            d = day.isoformat()
            kw = dict(local_date=d, at=None, note_id=None, mode=None, forgotten=forgotten)
            n = self._put_source(db, "diary", d, self.diary_text(day), **kw)
            n += self._put_source(db, "reflection", d, read(insights.reflection_path(self.cfg, day)), **kw)
            if own:
                db.commit()
            return n
        finally:
            if own:
                db.close()

    def sync(self, progress=None) -> int:
        """Bring the whole index up to date with the archive (only changed sources are re-chunked)."""
        days: set[date] = set()
        n = 0
        with closing(self._db()) as db:
            for note in self.store.pending_transcripts(include_done=True):
                n += self.index_note(note, db)
                days.add(date.fromisoformat(note.local_date))
            for folder in (self.cfg.diary_dir, Path(self.cfg.data_dir) / "reflections"):
                for p in folder.glob("????-??-??.md") if folder.is_dir() else []:
                    try:
                        days.add(date.fromisoformat(p.stem))
                    except ValueError:
                        pass
            for d in sorted(days):
                n += self.index_day(d, db)
            db.commit()
        if progress:
            progress(f"indexed text of {len(days)} day(s); {n} source(s) changed")
        return n

    def embed_pending(self, batch: int = 32, limit: int | None = None) -> int:
        """Vectors for chunks (and facts) that have none yet. Returns how many were embedded."""
        if not self.embedder:
            return 0
        import numpy as np

        done = 0
        while limit is None or done < limit:
            with closing(self._db()) as db:
                rows = db.execute("SELECT id, text FROM chunks WHERE vec IS NULL ORDER BY id LIMIT ?",
                                  (batch,)).fetchall()
            if not rows:
                break
            vecs = self.embedder.embed([r["text"] for r in rows], "passage")
            with closing(self._db()) as db, db:
                db.executemany("UPDATE chunks SET vec = ? WHERE id = ?",
                               [(np.asarray(v, dtype=np.float32).tobytes(), r["id"]) for r, v in zip(rows, vecs)])
            done += len(rows)
        self._embed_facts()
        return done

    def _embed_facts(self) -> None:
        if not self.embedder:
            return
        import numpy as np

        with closing(self._db()) as db:
            rows = db.execute("SELECT id, text, topic FROM facts WHERE vec IS NULL AND status = 'active'").fetchall()
        if rows:
            vecs = self.embedder.embed([f"{r['topic']}: {r['text']}" for r in rows], "passage")
            with closing(self._db()) as db, db:
                db.executemany("UPDATE facts SET vec = ? WHERE id = ?",
                               [(np.asarray(v, dtype=np.float32).tobytes(), r["id"]) for r, v in zip(rows, vecs)])

    # --- search ------------------------------------------------------------------------------------

    def _matrix(self):
        """(ids, vectors, dates, note ids) of retrievable embedded chunks, cached until the index changes."""
        import numpy as np

        with closing(self._db()) as db:
            key = tuple(db.execute("SELECT COUNT(*), MAX(id), SUM(excluded) FROM chunks WHERE vec IS NOT NULL")
                        .fetchone())
            with self._cache_lock:
                if self._cache and self._cache[0] == key:
                    return self._cache[1]
            rows = db.execute("SELECT id, vec, local_date, note_id FROM chunks WHERE vec IS NOT NULL AND excluded = 0"
                              " ORDER BY id").fetchall()
        dim = self.embedder.dim
        ids = np.array([r["id"] for r in rows], dtype=np.int64)
        mat = np.frombuffer(b"".join(r["vec"] for r in rows), dtype=np.float32).reshape(len(rows), dim) \
            if rows else np.zeros((0, dim), dtype=np.float32)
        dates = np.array([r["local_date"] for r in rows], dtype="U10")
        notes = np.array([r["note_id"] or "" for r in rows], dtype=object)
        value = (ids, mat, dates, notes)
        with self._cache_lock:
            self._cache = (key, value)
        return value

    def search(self, query: str, *, limit: int = 8, date_from: date | None = None, date_to: date | None = None,
               exclude_notes=(), tag: str | None = None, keyword_only: bool = False,
               per_ref: int = 2) -> list[Hit]:
        """Hybrid search: FTS5 BM25 + cosine over all embedded chunks, merged by reciprocal rank fusion
        (k = 60), the top `limit` best first, recency breaking ties. The model's cosine scores are compressed
        (unrelated queries still reach ~0.83 on this archive), so no similarity floor is applied: callers either
        let Codex judge the top hits (explicit recall) or keep only `keyword_only` hits (everyday turns)."""
        import numpy as np

        words = content_words(query)
        exclude = set(exclude_notes)
        allowed_notes = self.notes_tagged(tag) if tag else None
        where, args = ["c.excluded = 0"], []
        if date_from:
            where.append("c.local_date >= ?")
            args.append(date_from.isoformat())
        if date_to:
            where.append("c.local_date <= ?")
            args.append(date_to.isoformat())

        def allowed(note_id) -> bool:
            if note_id and note_id in exclude:
                return False
            return allowed_notes is None or (note_id in allowed_notes)

        fts: list[int] = []
        if words:
            match = " OR ".join(f'"{w}"*' if len(w) >= 4 else f'"{w}"' for w in words[:24])
            with closing(self._db()) as db:
                rows = db.execute(
                    f"SELECT c.id, c.note_id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
                    f"WHERE chunks_fts MATCH ? AND {' AND '.join(where)} ORDER BY bm25(chunks_fts) LIMIT 60",
                    (match, *args)).fetchall()
            fts = [r["id"] for r in rows if allowed(r["note_id"])]

        sims: dict[int, float] = {}
        vec_rank: list[int] = []
        if self.embedder and query.strip():
            ids, mat, dates, notes = self._matrix()
            if len(ids):
                # Short questions ("what did I say about X last week?") are embedded as their content words:
                # the question wording otherwise outweighs the topic. Long monologues are embedded as they are.
                qtext = " ".join(words) if words and len(query) < 300 else query
                q = self.embedder.embed([qtext], "query")[0]
                s = mat @ q
                mask = np.ones(len(ids), dtype=bool)
                if date_from:
                    mask &= dates >= date_from.isoformat()
                if date_to:
                    mask &= dates <= date_to.isoformat()
                if exclude or allowed_notes is not None:
                    mask &= np.array([allowed(n or None) for n in notes], dtype=bool)
                s = np.where(mask, s, -1.0)
                top = np.argsort(-s)[:60]
                vec_rank = [int(ids[i]) for i in top if s[i] > -1.0]
                sims = {int(ids[i]): float(s[i]) for i in np.nonzero(mask)[0]}

        scores: dict[int, float] = {}
        for rank_list in (fts, vec_rank):
            for rank, cid in enumerate(rank_list):
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (60 + rank + 1)
        keyword = set(fts)
        keep = [cid for cid in scores if cid in keyword or not keyword_only]
        if not keep:
            return []
        with closing(self._db()) as db:
            rows = {r["id"]: r for r in db.execute(
                f"SELECT id, source, ref, note_id, local_date, at, mode, text FROM chunks WHERE id IN "
                f"({','.join('?' * len(keep))})", keep)}
        hits = [Hit(**dict(rows[cid]), score=scores[cid], sim=sims.get(cid), keyword=cid in keyword)
                for cid in keep if cid in rows]
        # Best first; on (near) ties the more recent one.
        hits.sort(key=lambda h: (round(h.score, 4), h.at or h.local_date), reverse=True)
        out, per = [], {}
        for h in hits:
            k = (h.source, h.ref)
            if per.get(k, 0) >= per_ref:
                continue
            per[k] = per.get(k, 0) + 1
            out.append(h)
            if len(out) >= limit:
                break
        return out

    def in_range(self, date_from: date, date_to: date, *, limit: int = 12, exclude_notes=()) -> list[Hit]:
        """Everything from a date range (a question like "what did I say last week?" names no topic):
        diary prose and reflections first, then his own words, newest first."""
        with closing(self._db()) as db:
            rows = db.execute(
                "SELECT id, source, ref, note_id, local_date, at, mode, text FROM chunks WHERE excluded = 0 AND "
                "local_date BETWEEN ? AND ? AND source != 'reply' ORDER BY "
                "CASE source WHEN 'diary' THEN 0 WHEN 'reflection' THEN 1 ELSE 2 END, local_date DESC, at DESC, seq",
                (date_from.isoformat(), date_to.isoformat())).fetchall()
        hits = [Hit(**dict(r)) for r in rows if not (r["note_id"] and r["note_id"] in set(exclude_notes))]
        return hits[:limit]

    def stats(self) -> dict:
        with closing(self._db()) as db:
            c = db.execute("SELECT COUNT(*) n, SUM(vec IS NOT NULL) v, SUM(excluded) x, MIN(local_date) a, "
                           "MAX(local_date) b FROM chunks").fetchone()
            f = {r["status"]: r["n"] for r in db.execute("SELECT status, COUNT(*) n FROM facts GROUP BY status")}
            t = db.execute("SELECT COUNT(DISTINCT name) FROM tags").fetchone()[0]
            forgotten = db.execute("SELECT COUNT(*) FROM forgotten").fetchone()[0]
        size = sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*") if p.is_file())
        return {"chunks": c["n"], "embedded": c["v"] or 0, "excluded": c["x"] or 0, "first_date": c["a"],
                "last_date": c["b"], "facts": f, "tags": t, "forgotten": forgotten, "bytes": size,
                "model": self.embedder.name if self.embedder else "none"}

    # --- recall for Talk ---------------------------------------------------------------------------------

    def turn_evidence(self, note: Note, transcript: str) -> str:
        """MEMORY section for a talk turn: known facts + top episodic hits, each with its date.
        Explicit recall questions get a bigger budget (and a date range when the question names one)."""
        t = time.monotonic()
        today = date.fromisoformat(note.local_date)
        recall = is_recall_question(transcript)
        exclude = self.conversation_notes(note)
        rng = parse_range(transcript, today) if recall else None
        if recall:
            hits = self.search(transcript, limit=10, exclude_notes=exclude,
                               date_from=rng[0] if rng else None, date_to=rng[1] if rng else None)
            if rng and len(hits) < 4:
                seen = {h.id for h in hits}
                hits += [h for h in self.in_range(*rng, exclude_notes=exclude) if h.id not in seen][: 12 - len(hits)]
        else:
            hits = self.search(transcript, limit=3, exclude_notes=exclude, keyword_only=True)
        facts = self.relevant_facts(transcript)
        text = self.render_evidence(facts, hits, recall=recall, rng=rng, query=transcript,
                                    budget=4500 if recall else 1500)
        print(f"memory: turn evidence {len(facts)} fact(s), {len(hits)} hit(s){' (recall)' if recall else ''} "
              f"in {time.monotonic() - t:.2f}s")
        return text

    def detail_evidence(self, requests, note: Note) -> str:
        """The detail round's `need_details.recall`: up to 3 searches with optional date ranges."""
        exclude = self.conversation_notes(note)
        parts = []
        for r in (requests if isinstance(requests, list) else [])[:3]:
            if not isinstance(r, dict):
                continue
            q = str(r.get("query") or "").strip()[:300]
            try:
                a = date.fromisoformat(r["from"]) if r.get("from") else None
                b = date.fromisoformat(r["to"]) if r.get("to") else None
            except ValueError:
                a = b = None
            hits = self.search(q, limit=10, date_from=a, date_to=b, exclude_notes=exclude) if q else []
            if a and b and len(hits) < 4:
                seen = {h.id for h in hits}
                hits += [h for h in self.in_range(a, b, exclude_notes=exclude) if h.id not in seen][: 10 - len(hits)]
            span = f" between {a} and {b}" if a or b else ""
            parts.append(f"RECALL SEARCH \"{q}\"{span}:")
            parts += [self.hit_line(h) for h in hits] or ["- nothing found: he has no notes about this "
                                                          "(say so honestly; do not guess)."]
        return "\n".join(parts) if parts else ""

    @staticmethod
    def hit_line(h: Hit, limit: int = 420) -> str:
        return f"- {h.when()}, {h.label()}: \"{clip(h.text, limit)}\""

    def render_evidence(self, facts: list[Fact], hits: list[Hit], *, recall: bool, rng, query: str,
                        budget: int) -> str:
        st = self.coverage()
        out = ["MEMORY (retrieved by the companion from his own earlier notes; every item has its date — "
               "never present a memory without it):"]
        if facts:
            out.append("Known facts about him (id — fact [topic, kind; when heard]):")
            out += [f.line() for f in facts]
        else:
            out.append("Known facts about him: none yet.")
        if recall:
            span = f", limited to {rng[0]:%a %d %b} – {rng[1]:%a %d %b %Y}" if rng else ""
            out.append(f"He is asking about the past. The search covered his notes from {st}{span}.")
        if hits:
            out.append("Earlier notes found by search (best match first; a search hit is NOT necessarily about what he "
                       "asks — use only those that really are):" if recall else
                       "Earlier notes sharing words with this voice note (use only if really related):")
            used = sum(len(x) for x in out)
            for h in hits:
                line = self.hit_line(h, 600 if recall else 300)
                if used + len(line) > budget:
                    break
                out.append(line)
                used += len(line)
        elif recall:
            out.append("NOTHING in his notes matched this. Say plainly that you have no memory of it (you may ask "
                       "him to tell you, or use need_details.recall with other words or dates); never invent.")
        else:
            out.append("No earlier notes matched this voice note closely.")
        return "\n".join(out)

    def coverage(self) -> str:
        with closing(self._db()) as db:
            r = db.execute("SELECT MIN(local_date) a, MAX(local_date) b, COUNT(DISTINCT ref) n FROM chunks "
                           "WHERE source = 'note'").fetchone()
        return f"{fmt_day(r['a'])} to {fmt_day(r['b'])} ({r['n']} notes)" if r["a"] else "(no notes indexed yet)"

    def conversation_notes(self, note: Note) -> set[str]:
        """The current note and its conversation's turns (already in the Codex session): never 'recalled'."""
        ids = {note.id}
        if note.conversation:
            ids |= {n.id for n in self.store.conversation_turns(note.conversation, limit=200)}
        return ids

    # --- facts -------------------------------------------------------------------------------------------

    def facts(self, status: str | None = "active") -> list[Fact]:
        with closing(self._db()) as db:
            rows = db.execute("SELECT * FROM facts" + (" WHERE status = ?" if status else "") +
                              " ORDER BY topic COLLATE NOCASE, first_seen", (status,) if status else ()).fetchall()
        return [Fact.from_row(r) for r in rows]

    def fact(self, fact_id: str) -> Fact | None:
        with closing(self._db()) as db:
            r = db.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
        return Fact.from_row(r) if r else None

    def relevant_facts(self, text: str, limit: int = 25) -> list[Fact]:
        """All active facts while there are few; else the most relevant + the most recently confirmed."""
        facts = self.facts()
        if len(facts) <= ALL_FACTS_LIMIT:
            return facts
        words = set(content_words(text))
        score = {f.id: len(words & set(content_words(f"{f.topic} {f.text}"))) for f in facts}
        if self.embedder:
            import numpy as np

            self._embed_facts()
            with closing(self._db()) as db:
                vecs = {r["id"]: np.frombuffer(r["vec"], dtype=np.float32) for r in
                        db.execute("SELECT id, vec FROM facts WHERE status = 'active' AND vec IS NOT NULL")}
            q = self.embedder.embed([text], "query")[0]
            for fid, v in vecs.items():
                score[fid] += float(v @ q) * 3
        best = sorted(facts, key=lambda f: score[f.id], reverse=True)[:limit]
        recent = sorted(facts, key=lambda f: f.last_confirmed, reverse=True)[:10]
        keep = {f.id for f in best} | {f.id for f in recent}
        return [f for f in facts if f.id in keep]

    def _new_id(self, db) -> str:
        while True:
            fid = f"f-{uuid.uuid4().hex[:6]}"
            if not db.execute("SELECT 1 FROM facts WHERE id = ?", (fid,)).fetchone():
                return fid

    def _save(self, db, f: Fact) -> None:
        f.updated_at = now_utc()
        db.execute("INSERT OR REPLACE INTO facts (id, text, topic, kind, sources, first_seen, last_confirmed, "
                   "confidence, status, history, updated_at, vec) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                   "(SELECT vec FROM facts WHERE id = ? AND text = ? AND topic = ?))",
                   (f.id, f.text, f.topic, f.kind, json.dumps(f.sources, ensure_ascii=False), f.first_seen,
                    f.last_confirmed, f.confidence, f.status, json.dumps(f.history, ensure_ascii=False),
                    f.updated_at, f.id, f.text, f.topic))

    def ingest(self, items, sources: list[dict], via: str) -> list[str]:
        """Facts extracted by Codex (talk turn / diary build). Returns a log line per change.
        - relation "confirms" + a known id: last_confirmed + source added;
        - relation "replaces" + a known id, confidence high: the old fact is retired (history kept), new one active;
        - new: added, unless the same sentence is already known (then it confirms it). Low confidence: dropped."""
        if not isinstance(items, list) or not sources:
            return []
        day = sources[0]["local_date"]
        changes = []
        with closing(self._db()) as db, db:
            active = {Fact.from_row(r).id: Fact.from_row(r) for r in db.execute("SELECT * FROM facts WHERE status = 'active'")}
            by_text = {normalize(f.text): f for f in active.values()}
            for item in items[:MAX_FACTS_PER_NOTE * max(1, len(sources))]:
                try:
                    text, topic, kind = clean_fact(item)
                except Invalid:
                    continue
                if item.get("confidence") not in ("medium", "high"):
                    continue
                src = [s for s in sources if s.get("index") in (None, item.get("note"))] or sources[:1]
                src = [{k: v for k, v in s.items() if k != "index"} | {"via": via} for s in src]
                rel, ref = item.get("relation"), str(item.get("fact_id") or "")
                old = active.get(ref) if FACT_ID_RE.fullmatch(ref) else None
                same = by_text.get(normalize(text))
                if rel == "confirms" and old or (same and not (rel == "replaces" and old and old is not same)):
                    f = old if rel == "confirms" and old else same
                    f.last_confirmed = max(f.last_confirmed, day)
                    f.sources = (f.sources + [s for s in src if s not in f.sources])[-20:]
                    f.history.append({"at": now_utc(), "event": "confirmed", "via": via,
                                      "note_id": src[0].get("note_id")})
                    self._save(db, f)
                    changes.append(f"confirmed {f.id}")
                    continue
                if rel == "replaces" and old and item.get("confidence") != "high":
                    changes.append(f"skipped unsure replacement of {old.id}")
                    continue
                new = Fact(self._new_id(db), text, topic, kind, src, day, day, item["confidence"], "active",
                           [{"at": now_utc(), "event": "created", "via": via, "note_id": src[0].get("note_id")}])
                if rel == "replaces" and old:
                    old.status = "retired"
                    old.history.append({"at": now_utc(), "event": "replaced", "by": new.id, "via": via,
                                        "note_id": src[0].get("note_id")})
                    self._save(db, old)
                    active.pop(old.id, None)
                    new.history[0]["replaces"] = old.id
                    changes.append(f"retired {old.id} (replaced)")
                self._save(db, new)
                active[new.id] = new
                by_text[normalize(text)] = new
                changes.append(f"added {new.id}")
        if changes:
            self.view_changed()
        return changes

    def set_tags(self, items, notes: dict[int | None, Note], via: str) -> None:
        """Tags per note (people / places / projects / topics). `notes` maps the item's `note` index -> Note
        (key None for a talk turn). A note's tags from the same source are replaced."""
        if not isinstance(items, list):
            return
        rows = []
        for item in items[: MAX_TAGS_PER_NOTE * max(1, len(notes))]:
            if not isinstance(item, dict) or item.get("kind") not in TAG_KINDS:
                continue
            name = link_name(str(item.get("name") or ""))
            note = notes.get(item.get("note")) if None not in notes else notes[None]
            if note and 1 < len(name) <= TAG_MAX:
                rows.append((note.id, note.local_date, item["kind"], name, via))
        with closing(self._db()) as db, db:
            for nid in {n.id for n in notes.values()}:
                db.execute("DELETE FROM tags WHERE note_id = ? AND via = ?", (nid, via))
            db.executemany("INSERT OR IGNORE INTO tags VALUES (?, ?, ?, ?, ?)", rows)

    def day_links(self, day: date) -> list[str]:
        """Tag names mentioned on a day (forgotten terms left out), for `[[Topic]]` links on the diary page."""
        with closing(self._db()) as db:
            names = [r["name"] for r in db.execute(
                "SELECT name, MIN(rowid) r FROM tags WHERE local_date = ? GROUP BY name COLLATE NOCASE ORDER BY r",
                (day.isoformat(),))]
            forgotten = self._forgotten_res(db)
        return [n for n in names if not any(p.search(normalize(n)) for p in forgotten)]

    def notes_tagged(self, name: str) -> set[str]:
        with closing(self._db()) as db:
            return {r["note_id"] for r in db.execute("SELECT note_id FROM tags WHERE name = ? COLLATE NOCASE",
                                                     (name.strip(),))}

    def tag_names(self) -> list[dict]:
        with closing(self._db()) as db:
            return [dict(r) for r in db.execute(
                "SELECT name, kind, COUNT(DISTINCT note_id) notes, MAX(local_date) last FROM tags "
                "GROUP BY name COLLATE NOCASE ORDER BY notes DESC, last DESC LIMIT 200")]

    # --- talk turn / diary hooks ---------------------------------------------------------------------

    def after_turn(self, note: Note, answer: dict) -> list[str]:
        """Facts + tags from the talk turn's own Codex JSON (no extra LLM call)."""
        mem = answer.get("memory") if isinstance(answer, dict) else None
        if not isinstance(mem, dict):
            return []
        self.set_tags(mem.get("tags"), {None: note}, "talk")
        changes = self.ingest(mem.get("facts"), [{"note_id": note.id, "local_date": note.local_date}], "talk")
        if changes:
            print(f"memory: talk turn {note.stem}: " + ", ".join(changes))
        return changes

    def after_diary(self, day: date, notes: list[Note], data: dict) -> list[str]:
        """Facts + tags from the diary build's JSON (`facts[]`, `summaries[].tags`); talk tags are kept."""
        idx = {i: n for i, n in enumerate(notes)}
        tags = [{**t, "note": s.get("note")} for s in data.get("summaries") or [] if isinstance(s, dict)
                for t in (s.get("tags") or []) if isinstance(t, dict)]
        self.set_tags(tags, idx, "diary")
        sources = [{"index": i, "note_id": n.id, "local_date": n.local_date} for i, n in idx.items()]
        changes = self.ingest(data.get("facts"), sources, "diary") if sources else []
        if changes:
            print(f"memory: diary {day}: " + ", ".join(changes))
        return changes

    def known_facts_text(self, limit: int = 60) -> str:
        facts = sorted(self.facts(), key=lambda f: f.last_confirmed, reverse=True)[:limit]
        return "\n".join(f"- {f.id}: {f.text} [{f.topic}]" for f in facts) or "(none yet)"

    # --- owner edits (web UI) and confirmed voice actions -----------------------------------------------

    def add_fact(self, text: str, topic: str, kind: str, via: str = "web UI", note_id: str | None = None,
                 local_date: str | None = None) -> Fact:
        text, topic, kind = clean_fact({"text": text, "topic": topic, "kind": kind})
        day = local_date or date.today().isoformat()
        with closing(self._db()) as db, db:
            f = Fact(self._new_id(db), text, topic, kind,
                     [{"note_id": note_id, "local_date": day, "via": via}] if note_id else [], day, day, "high",
                     "active", [{"at": now_utc(), "event": "created", "via": via, "note_id": note_id}])
            self._save(db, f)
        self.view_changed()
        return f

    def edit_fact(self, fact_id: str, changes: dict, via: str = "web UI") -> Fact:
        f = self.fact(fact_id)
        if not f:
            raise Invalid(f"there is no fact {fact_id}")
        unknown = set(changes) - {"text", "topic", "kind", "status"}
        if unknown:
            raise Invalid(f"unknown field(s): {', '.join(sorted(unknown))}")
        before = {k: getattr(f, k) for k in changes}
        text, topic, kind = clean_fact({"text": changes.get("text", f.text), "topic": changes.get("topic", f.topic),
                                        "kind": changes.get("kind", f.kind)})
        status = changes.get("status", f.status)
        if status not in FACT_STATUSES:
            raise Invalid(f"status must be one of {', '.join(FACT_STATUSES)}")
        f.text, f.topic, f.kind, f.status = text, topic, kind, status
        f.history.append({"at": now_utc(), "event": "edited", "via": via, "before": before})
        with closing(self._db()) as db, db:
            self._save(db, f)
        self.view_changed()
        return f

    def delete_fact(self, fact_id: str) -> bool:
        with closing(self._db()) as db, db:
            n = db.execute("DELETE FROM facts WHERE id = ?", (fact_id,)).rowcount
        if n:
            self.view_changed()
        return bool(n)

    def forget_preview(self, term: str) -> tuple[list[Fact], int]:
        pat = term_re(term)
        facts = [f for f in self.facts() if pat.search(normalize(f"{f.topic} {f.text}"))]
        with closing(self._db()) as db:
            n = sum(1 for r in db.execute("SELECT text FROM chunks WHERE excluded = 0") if pat.search(normalize(r["text"])))
        return facts, n

    def forget(self, term: str, via: str = "web UI", note_id: str | None = None) -> tuple[int, int]:
        """Retire matching facts and hide matching chunks (now and in future notes). Raw notes are untouched."""
        term = clean_term(term)
        pat = term_re(term)
        with closing(self._db()) as db, db:
            ids = [r["id"] for r in db.execute("SELECT id, text FROM chunks WHERE excluded = 0")
                   if pat.search(normalize(r["text"]))]
            db.executemany("UPDATE chunks SET excluded = 1 WHERE id = ?", [(i,) for i in ids])
            n_facts = 0
            for r in db.execute("SELECT * FROM facts WHERE status = 'active'").fetchall():
                f = Fact.from_row(r)
                if pat.search(normalize(f"{f.topic} {f.text}")):
                    f.status = "retired"
                    f.history.append({"at": now_utc(), "event": "forgotten", "term": term, "via": via,
                                      "note_id": note_id})
                    self._save(db, f)
                    n_facts += 1
            db.execute("INSERT INTO forgotten (term, created_at, chunks, facts) VALUES (?, ?, ?, ?) "
                       "ON CONFLICT(term) DO UPDATE SET chunks = chunks + excluded.chunks, facts = facts + excluded.facts",
                       (term, now_utc(), len(ids), n_facts))
        self.view_changed()
        return n_facts, len(ids)

    def forgotten(self) -> list[dict]:
        with closing(self._db()) as db:
            return [dict(r) for r in db.execute("SELECT * FROM forgotten ORDER BY created_at DESC")]

    def unforget(self, forget_id: int) -> bool:
        """Undo a forget for the episodic index (chunks become retrievable again). Retired facts stay retired."""
        with closing(self._db()) as db, db:
            row = db.execute("SELECT term FROM forgotten WHERE id = ?", (forget_id,)).fetchone()
            if not row:
                return False
            db.execute("DELETE FROM forgotten WHERE id = ?", (forget_id,))
            others = self._forgotten_res(db)
            pat = term_re(row["term"])
            ids = [r["id"] for r in db.execute("SELECT id, text FROM chunks WHERE excluded = 1")
                   if pat.search(normalize(r["text"])) and not any(p.search(normalize(r["text"])) for p in others)]
            db.executemany("UPDATE chunks SET excluded = 0 WHERE id = ?", [(i,) for i in ids])
        self.view_changed()
        return True

    # Voice actions (talk.py: proposed -> validated here -> pending -> executed after a spoken yes)

    def check_action(self, kind: str, params: dict, summary: str) -> tuple[dict, str]:
        """Checks against the current memory and says exactly what will change (spoken in the proposal)."""
        if kind == "memory_fact":
            f = self.fact(params["fact_id"])
            if not f or f.status != "active":
                raise Invalid(f"I have no current fact {params['fact_id']}")
            if params["op"] == "correct":
                return params, f"{summary} (replacing “{f.text}” with “{params['text']}”)"
            return params, f"{summary} (marking “{f.text}” as no longer true)"
        if kind == "memory_forget":
            facts, n = self.forget_preview(params["query"])
            what = []
            if facts:
                what.append(f"{len(facts)} fact{'s' if len(facts) != 1 else ''}: " +
                            "; ".join(f"“{clip(f.text, 80)}”" for f in facts[:3]))
            what.append(f"{n} note excerpt{'s' if n != 1 else ''} hidden from recall")
            return params, f"{summary} (forgets {', and '.join(what)}; the recordings themselves stay)"
        raise Invalid(f"{kind!r} is not a memory action")

    def execute(self, kind: str, params: dict, note_id: str | None) -> str:
        if kind == "memory_fact":
            f = self.fact(params["fact_id"])
            if not f or f.status != "active":
                raise Invalid(f"the fact {params['fact_id']} is no longer current")
            if params["op"] == "retire":
                self.edit_fact(f.id, {"status": "retired"}, via="voice")
                return f"Done: I no longer hold that {clip(f.text, 120).rstrip('.')}."
            note = self.store.get(note_id) if note_id else None
            new = self.add_fact(params["text"], params.get("topic") or f.topic, params.get("kind") or f.kind,
                                via="voice", note_id=note_id, local_date=note.local_date if note else None)
            with closing(self._db()) as db, db:
                f.status = "corrected"
                f.history.append({"at": now_utc(), "event": "corrected", "by": new.id, "via": "voice",
                                  "note_id": note_id})
                self._save(db, f)
                new.history[0]["corrects"] = f.id
                self._save(db, new)
            self.view_changed()
            return f"Done: I corrected it. I now remember: {new.text}"
        if kind == "memory_forget":
            n_facts, n_chunks = self.forget(params["query"], via="voice", note_id=note_id)
            return (f"Done: I forgot “{params['query']}”: {n_facts} fact{'s' if n_facts != 1 else ''} retired and "
                    f"{n_chunks} note excerpt{'s' if n_chunks != 1 else ''} hidden from recall. "
                    "Your recordings are still there.")
        raise Invalid(f"{kind!r} is not a memory action")

    # --- Obsidian view (generated, read-only) -----------------------------------------------------------

    def view_path(self) -> Path:
        p = getattr(self.cfg, "memory_page", None)
        if p:
            return Path(p)
        return self.cfg.diary_dir.parent / "Orbit" / "Memory.md"

    def view_changed(self) -> None:
        try:
            self.write_view()
        except Exception:  # noqa: BLE001 - the facts are saved; the view is only a mirror
            traceback.print_exc()

    def write_view(self) -> Path:
        facts = self.facts()
        groups: dict[str, list[Fact]] = {}
        for f in facts:
            groups.setdefault(link_name(f.topic) or "Other", []).append(f)
        port = getattr(self.cfg, "port", 8765)
        lines = ["---", f"generated: {datetime.now().astimezone():%Y-%m-%dT%H:%M%z}", f"facts: {len(facts)}", "---",
                 f"> Generated by Orbit — edit at http://localhost:{port}/ui/ (changes made here are overwritten).", "",
                 "# What Orbit remembers", ""]
        if not facts:
            lines.append("_Nothing yet._")
        for topic in sorted(groups, key=str.casefold):
            lines += [f"## [[{topic}]]", ""]
            for f in groups[topic]:
                days = sorted({s["local_date"] for s in f.sources if s.get("local_date")} | {f.first_seen})
                shown = days if len(days) <= 4 else days[:1] + days[-3:]
                links = ", ".join(f"[[{d}]]" for d in shown)
                lines.append(f"- {f.text} _({f.kind})_ — {links}")
            lines.append("")
        path = self.view_path()
        write_atomic(path, "\n".join(lines).encode())
        return path


# --- validation helpers -----------------------------------------------------------------------------------

LINK_BAD = re.compile(r"[\[\]#^|\\/:*?\"<>]")
MENTIONS_PREFIX = "**Mentions:**"


def link_name(name: str) -> str:
    """Safe Obsidian page name for a topic/tag."""
    return " ".join(LINK_BAD.sub(" ", name).split())[:TAG_MAX].strip(" .")


def clean_fact(item) -> tuple[str, str, str]:
    if not isinstance(item, dict):
        raise Invalid("not a fact")
    text = " ".join(str(item.get("text") or "").split())
    topic = link_name(str(item.get("topic") or ""))
    kind = item.get("kind") or "fact"
    if not FACT_TEXT_MIN <= len(text) <= FACT_TEXT_MAX:
        raise Invalid(f"the fact must be {FACT_TEXT_MIN} to {FACT_TEXT_MAX} characters")
    if not topic:
        raise Invalid("the fact needs a topic")
    if kind not in FACT_KINDS:
        raise Invalid(f"kind must be one of {', '.join(FACT_KINDS)}")
    return text, topic, kind


def clean_term(term) -> str:
    term = " ".join(str(term or "").split())
    if not 2 <= len(term) <= 80 or not WORD_RE.search(term):
        raise Invalid("say a word or phrase to forget (2 to 80 characters)")
    return term


def term_re(term: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + re.escape(normalize(term)) + r"(?!\w)")


# --- Talk integration (talk.py calls these) -----------------------------------------------------------


def safe_evidence(fn, title: str = "MEMORY") -> str:
    """Evidence text for a talk turn; a failure is reported to Codex as 'no evidence', never fails the turn."""
    try:
        return fn() or ""
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return f"{title}: could not be searched right now ({e}). You have no memory evidence this turn."


ACTION_KINDS = ("memory_fact", "memory_forget")
ACTION_PARAMS = {"memory_fact": ({"op", "fact_id"}, {"text", "topic", "kind"}), "memory_forget": ({"query"}, set())}


def validate_action(kind: str, params: dict) -> dict:
    """Shape of a proposed memory action (actions.validate dispatches here); existence is checked by
    Memory.check_action against the live memory."""
    if kind == "memory_fact":
        op = params.get("op")
        if op not in ("correct", "retire"):
            raise Invalid("a memory change is either a correction or retiring a fact")
        fid = str(params.get("fact_id") or "").strip()
        if not FACT_ID_RE.fullmatch(fid):
            raise Invalid(f"{fid or 'the fact id'} is not a fact id like f-1a2b3c")
        out = {"op": op, "fact_id": fid}
        if op == "correct":
            text, topic, kind_ = clean_fact({"text": params.get("text"), "topic": params.get("topic") or "x",
                                             "kind": params.get("kind") or "fact"})
            out["text"] = text
            if params.get("topic"):
                out["topic"] = topic
            if params.get("kind"):
                out["kind"] = kind_
        elif set(params) - {"op", "fact_id"}:
            raise Invalid("retiring a fact takes only its id")
        return out
    if kind == "memory_forget":
        return {"query": clean_term(params.get("query"))}
    raise Invalid(f"{kind!r} is not a memory action")


FACT_ITEM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["text", "topic", "kind", "confidence", "relation", "fact_id"],
    "properties": {"text": {"type": "string"}, "topic": {"type": "string"},
                   "kind": {"type": "string", "enum": list(FACT_KINDS)},
                   "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                   "relation": {"type": "string", "enum": ["new", "confirms", "replaces"]},
                   "fact_id": {"type": "string"}},
}
TAG_ITEM_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["kind", "name"],
                   "properties": {"kind": {"type": "string", "enum": list(TAG_KINDS)}, "name": {"type": "string"}}}
TURN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["facts", "tags"],
               "properties": {"facts": {"type": "array", "items": FACT_ITEM_SCHEMA},
                              "tags": {"type": "array", "items": TAG_ITEM_SCHEMA}}}
RECALL_SCHEMA = {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["query", "from", "to"],
    "properties": {"query": {"type": "string"}, "from": {"type": "string"}, "to": {"type": "string"}}}}


def extend_recall_schema(schema: dict) -> dict:
    """Add `need_details.recall` (the detail round's memory searches) to the talk turn's output schema."""
    nd = schema["properties"]["need_details"]
    nd["properties"]["recall"] = RECALL_SCHEMA
    nd["required"].append("recall")
    return schema


def extend_fact_schema(schema: dict) -> dict:
    """Add `memory` (facts + tags of this voice note) to the talk turn's output schema."""
    schema["properties"]["memory"] = TURN_SCHEMA
    schema["required"].append("memory")
    return schema


def extend_diary_schema(schema: dict) -> dict:
    """Diary build JSON: `summaries[].tags` and top-level `facts[]` (with the note index they came from)."""
    item = schema["properties"]["summaries"]["items"]
    item["properties"]["tags"] = {"type": "array", "items": TAG_ITEM_SCHEMA}
    item["required"].append("tags")
    fact = json.loads(json.dumps(FACT_ITEM_SCHEMA))
    fact["properties"]["note"] = {"type": "integer"}
    fact["required"].append("note")
    schema["properties"]["facts"] = {"type": "array", "items": fact}
    schema["required"].append("facts")
    return schema


FACT_RULE = """durable facts about him that are clearly stated (people and pets in his life, places,
  preferences, goals, decisions, routines) — not passing moods, one-off events or project/workspace status (his
  notes are kept anyway). One short English sentence each, third person ("His cat is called Mochi."), a topic
  (the person, pet, place, project or thing, 1 to 3 words, as he names it) and a kind. If it repeats a known
  fact: relation "confirms" + its id. If he clearly says something changed: relation "replaces" + the old id and
  confidence high. If unsure whether it contradicts a known fact, leave it out. Usually there are none."""

RECALL_RULES = """- Memory. The MEMORY section is what the companion retrieved from his earlier voice notes, your earlier
  replies, diary pages and known facts. Use it when it helps (a name, a plan he made), but never present a
  recalled item without its date ("on Monday 3 November you said …"). If he asks about the past and MEMORY does
  not answer it, say plainly that you have no memory of that — never invent or guess a memory. In your one
  detail round you may ask for need_details.recall (at most 3 searches: query words, and from / to as
  YYYY-MM-DD or ""); his notes mix Indonesian and English, so a second search in the other language can help."""

FACT_RULES = f"""- Facts and tags, private bookkeeping: in EVERY answer fill memory.facts with {FACT_RULE}
  memory.tags: the people, places, projects and topics this note mentions. Never mention this bookkeeping. When
  he says "remember that …", record it as a fact and simply say you will remember it.
- Memory changes (confirm first, like any action): "that's wrong / actually it's …" about a known fact ->
  memory_fact op "correct" with its id and the corrected sentence; "that's no longer true" -> op "retire";
  "forget X" -> memory_forget with the word or phrase. Forgetting retires matching facts and hides matching notes
  from recall; the recordings themselves are never deleted."""

PARAMS_HELP = """- memory_fact: {"op": "correct|retire", "fact_id": "f-...", "text": "<the corrected fact, one sentence; only for correct>"}
- memory_forget: {"query": "<word or phrase he wants forgotten>"}"""

DIARY_RULE = f"""Also list, in "facts", {FACT_RULE}
Give each fact the index of the note it comes from ("note"). For each note, list in "tags" the people, places,
projects and topics it mentions (names as spoken; empty list if none).
Known facts (id: fact [topic]):
{{known}}"""
