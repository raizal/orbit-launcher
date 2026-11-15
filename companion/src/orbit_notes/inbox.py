"""Orbit Inbox: things the companion wants the watch to know (docs/inbox.md).

Pull, not push: the watch asks `GET /v1/inbox?since=<cursor>` at cheap moments. Items live in their
own sqlite file (`<data_dir>/inbox.sqlite`). Every insert and every read-state change bumps a global
revision (`rev`); the watch's cursor is the highest rev it has seen, so `since` returns new items and
items whose read state changed elsewhere.

Producers create items lazily: they run when the watch polls (or a Talk turn starts), at most once per
PRODUCER_TTL_S, and never in the background. Each producer is a small class with `name` and
`run(inbox) -> list[NewItem]`; the first one watches BB threads that Talk spawned or messaged.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

PRODUCER_TTL_S = 60
KEEP_ITEMS = 200
TITLE_MAX = 80
BODY_MAX = 2000
KINDS = ("note", "thread_done", "thread_input", "thread_error", "app_update")
WATCH_WINDOW = timedelta(days=7)  # threads spawned/told by Talk longer ago than this are not watched
MENTION_WINDOW = timedelta(hours=24)  # unread items older than this are not brought up in Talk
MENTION_MAX = 3
THREAD_REF_RE = re.compile(r"(?:[a-z0-9][a-z0-9_-]{0,30}:)?thr_[A-Za-z0-9]+")  # "thr_…" or "work:thr_…"

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           TEXT PRIMARY KEY,          -- "in-<hex>"
    rev          INTEGER NOT NULL,          -- bumped on insert and on read; the watch's cursor
    created_at   TEXT NOT NULL,             -- ISO 8601 UTC
    kind         TEXT NOT NULL,
    title        TEXT NOT NULL,             -- short and spoken-friendly
    body         TEXT NOT NULL DEFAULT '',
    ref          TEXT,                      -- e.g. a BB thread id
    read_at      TEXT,
    mentioned_at TEXT,                      -- Talk brought it up once (never again)
    dedupe       TEXT UNIQUE                -- producers: the same event never makes two items
);
CREATE INDEX IF NOT EXISTS items_rev ON items(rev);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,                 -- "rev", "producer:<name>" state (JSON)
    value TEXT NOT NULL
);
"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class InvalidItem(ValueError):
    pass


@dataclass
class Item:
    id: str
    rev: int
    created_at: str
    kind: str
    title: str
    body: str
    ref: str | None
    read_at: str | None
    mentioned_at: str | None = None

    def public(self) -> dict:
        """What the watch and the web UI get."""
        d = asdict(self)
        d.pop("mentioned_at")
        return d


@dataclass
class NewItem:
    title: str
    body: str = ""
    kind: str = "note"
    ref: str | None = None
    dedupe: str | None = None


def clean(item: NewItem) -> NewItem:
    title = " ".join(str(item.title or "").split())
    if not title:
        raise InvalidItem("title is required")
    if len(title) > TITLE_MAX:
        title = title[:TITLE_MAX - 1].rstrip() + "…"
    body = str(item.body or "").strip()
    if len(body) > BODY_MAX:
        body = body[:BODY_MAX - 1].rstrip() + "…"
    kind = item.kind or "note"
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", kind):
        raise InvalidItem("kind must be a short lowercase word")
    ref = str(item.ref).strip() if item.ref else None
    if ref and len(ref) > 200:
        raise InvalidItem("ref is too long")
    return NewItem(title, body, kind, ref or None, item.dedupe)


class Inbox:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "inbox.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._db()) as db:
            db.executescript(SCHEMA)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)  # autocommit; writes use _write()
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _write(self):
        """One write transaction, taken up front (BEGIN IMMEDIATE): the CLI and the server may write at the
        same time, and the rev read-then-bump must not interleave."""
        with self._lock, closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @staticmethod
    def _next_rev(db) -> int:
        row = db.execute("SELECT value FROM meta WHERE key = 'rev'").fetchone()
        rev = int(row["value"]) + 1 if row else 1
        db.execute("INSERT INTO meta (key, value) VALUES ('rev', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   (str(rev),))
        return rev

    @staticmethod
    def _item(r) -> Item:
        return Item(**{k: r[k] for k in ("id", "rev", "created_at", "kind", "title", "body", "ref", "read_at",
                                         "mentioned_at")})

    # --- writes ----------------------------------------------------------------------------------

    def add(self, item: NewItem) -> Item | None:
        """Insert an item; None if a producer's `dedupe` key already exists."""
        item = clean(item)
        with self._write() as db:
            if item.dedupe and db.execute("SELECT 1 FROM items WHERE dedupe = ?", (item.dedupe,)).fetchone():
                return None
            iid = f"in-{uuid.uuid4().hex[:10]}"
            db.execute(
                "INSERT INTO items (id, rev, created_at, kind, title, body, ref, dedupe) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (iid, self._next_rev(db), iso(now_utc()), item.kind, item.title, item.body, item.ref, item.dedupe),
            )
            # Bounded: the oldest items go (the watch keeps its own copy of what it has seen).
            db.execute("DELETE FROM items WHERE id NOT IN (SELECT id FROM items ORDER BY rev DESC LIMIT ?)",
                       (KEEP_ITEMS,))
            return self._item(db.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone())

    def mark_read(self, item_id: str, read: bool = True) -> Item | None:
        with self._write() as db:
            row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if not row:
                return None
            if bool(row["read_at"]) != read:
                db.execute("UPDATE items SET read_at = ?, rev = ? WHERE id = ?",
                           (iso(now_utc()) if read else None, self._next_rev(db), item_id))
            return self._item(db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())

    def mark_mentioned(self, ids: list[str]) -> None:
        if not ids:
            return
        with self._write() as db:
            db.executemany("UPDATE items SET mentioned_at = ? WHERE id = ? AND mentioned_at IS NULL",
                           [(iso(now_utc()), i) for i in ids])

    # --- reads -----------------------------------------------------------------------------------

    def get(self, item_id: str) -> Item | None:
        with closing(self._db()) as db:
            row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return self._item(row) if row else None

    def cursor(self) -> int:
        with closing(self._db()) as db:
            row = db.execute("SELECT value FROM meta WHERE key = 'rev'").fetchone()
        return int(row["value"]) if row else 0

    def changes(self, since: int, limit: int = 50) -> tuple[int, list[Item]]:
        """(cursor, items changed after `since`, newest first). The cursor is read first, so an item added
        meanwhile is returned now or on the next poll — never skipped."""
        cursor = self.cursor()
        with closing(self._db()) as db:
            rows = db.execute("SELECT * FROM items WHERE rev > ? AND rev <= ? ORDER BY rev DESC LIMIT ?",
                              (since, cursor, limit)).fetchall()
        return cursor, [self._item(r) for r in rows]

    def latest(self, limit: int = 100) -> list[Item]:
        with closing(self._db()) as db:
            rows = db.execute("SELECT * FROM items ORDER BY rev DESC LIMIT ?", (limit,)).fetchall()
        items = [self._item(r) for r in rows]
        items.sort(key=lambda i: i.created_at, reverse=True)
        return items

    def unread(self) -> list[Item]:
        with closing(self._db()) as db:
            rows = db.execute("SELECT * FROM items WHERE read_at IS NULL ORDER BY created_at DESC").fetchall()
        return [self._item(r) for r in rows]

    # --- producer state --------------------------------------------------------------------------

    def state(self, name: str) -> dict:
        with closing(self._db()) as db:
            row = db.execute("SELECT value FROM meta WHERE key = ?", (f"producer:{name}",)).fetchone()
        try:
            return json.loads(row["value"]) if row else {}
        except ValueError:
            return {}

    def save_state(self, name: str, state: dict) -> None:
        with self._write() as db:
            db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                       (f"producer:{name}", json.dumps(state)))


# --- producers -----------------------------------------------------------------------------------


class Producer(Protocol):
    name: str

    def run(self, inbox: Inbox) -> list[NewItem]: ...


def summary(output: str, limit: int = 240) -> str:
    """First paragraph of a thread's final output, markdown stripped, cut at a sentence end near `limit`."""
    text = re.sub(r"```.*?```", " ", output or "", flags=re.S)
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip() and not p.strip().startswith(("|", "---"))]
    first = paras[0] if paras else ""
    first = re.sub(r"[*_`#>]+", "", first)
    first = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", first)
    first = " ".join(re.sub(r"^\s*[-•]\s+", "", first, flags=re.M).split())
    if len(first) <= limit:
        return first
    cut = first[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > limit // 3 else cut.rstrip() + "…"


def short_title(title: str, limit: int = 48) -> str:
    title = " ".join((title or "").split()) or "A thread"
    return title if len(title) <= limit else title[:limit - 1].rstrip() + "…"


def watched_threads(audit_path: Path, now: datetime) -> dict[tuple, dict]:
    """Threads Talk spawned or messaged (talk_audit.jsonl `executed` entries) in the watch window:
    (workspace or None for entries from before workspaces, thread id) -> {since: epoch ms of the latest
    spawn/tell, title}."""
    out: dict[tuple, dict] = {}
    if not audit_path.exists():
        return out
    for line in audit_path.read_text().splitlines():
        try:
            e = json.loads(line)
            if e.get("event") != "executed" or e.get("kind") not in ("thread_spawn", "thread_tell"):
                continue
            at = datetime.fromisoformat(e["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if now - at > WATCH_WINDOW:
            continue
        if e["kind"] == "thread_spawn":
            result = e.get("result") if isinstance(e.get("result"), dict) else {}
            tid, title = result.get("id"), result.get("title") or (e.get("params") or {}).get("title")
        else:
            tid, title = (e.get("params") or {}).get("thread_id"), None
        if not isinstance(tid, str) or not tid.startswith("thr_"):
            continue
        w = out.setdefault((e.get("workspace"), tid), {"since": 0, "title": None})
        w["since"] = max(w["since"], int(at.timestamp() * 1000))
        w["title"] = title or w["title"]
    return out


class ThreadsProducer:
    """'‹title› is done' / 'needs your input' / 'stopped with an error' for threads Talk started or messaged.

    One `bb thread list` per run; `bb thread output` only for a thread that went idle since it was last seen.
    A finished turn makes a new item only if its output changed (an idle thread touched for other reasons
    stays quiet). Completions from before the producer's first run are recorded silently (no backlog burst).
    """

    name = "threads"

    def __init__(self, workspace, audit_path: Path):
        from .workspace import Workspaces

        self.workspace, self.audit_path = Workspaces.wrap(workspace), Path(audit_path)

    def run(self, inbox: Inbox) -> list[NewItem]:
        """Per workspace: one `bb thread list` (only workspaces with watched threads). A workspace that can't be
        read is skipped this run; the others still produce. Refs/keys: "thr_…" in the primary workspace (as
        before), "<workspace>:thr_…" elsewhere; with several workspaces the title says which one."""
        ws_all = self.workspace
        now = now_utc()
        watched: dict[tuple, dict] = {}
        for (ws, tid), w in watched_threads(self.audit_path, now).items():  # old entries: primary workspace
            m = watched.setdefault((ws or ws_all.primary, tid), {"since": 0, "title": None})
            m["since"], m["title"] = max(m["since"], w["since"]), w["title"] or m["title"]
        st = inbox.state(self.name)
        first_run = st.setdefault("first_run_ms", int(now.timestamp() * 1000))
        seen: dict = st.setdefault("threads", {})
        items: list[NewItem] = []
        listed: dict[str, dict | None] = {}
        for ws in sorted({ws for ws, _ in watched}):
            try:
                listed[ws] = {t["id"]: t for t in ws_all.run(["thread", "list"], workspace=ws)
                              if isinstance(t, dict) and "id" in t}
            except Exception as e:  # noqa: BLE001 - try again on the next run; keep its state
                print(f"inbox: bb thread list ({ws}) failed: {e}")
                listed[ws] = None
        for (ws, tid), w in watched.items():
            ref = ws_all.ref_for(ws, tid)
            if listed.get(ws) is None:
                continue
            t = listed[ws].get(tid)
            if not t or t.get("archivedAt") or t.get("deletedAt"):
                continue
            s = seen.setdefault(ref, {})
            title = short_title(t.get("title") or w["title"] or t.get("titleFallback"))
            if ws_all.multi:
                title = f"{ws.capitalize()}: {title}"
            updated = int(t.get("updatedAt") or 0)
            fresh = updated > max(w["since"], first_run)
            if t.get("hasPendingInteraction"):
                if not s.get("input"):
                    s["input"] = True
                    if fresh:
                        items.append(NewItem(f"{title} needs your input", "It is waiting for your answer in BB.",
                                             "thread_input", ref, f"thread:{ref}:input:{updated}"))
                continue
            s["input"] = False
            status = t.get("status")
            if status == "error" and s.get("error_at") != updated:
                s["error_at"] = updated
                if fresh:
                    items.append(NewItem(f"{title} stopped with an error", "Its last turn failed; open it in BB.",
                                         "thread_error", ref, f"thread:{ref}:error:{updated}"))
            elif status == "idle" and s.get("idle_at") != updated:
                s["idle_at"] = updated
                try:
                    output = ws_all.thread_output(ref) or ""
                except Exception as e:  # noqa: BLE001 - try again on the next run
                    s.pop("idle_at", None)
                    print(f"inbox: bb thread output {ref} failed: {e}")
                    continue
                digest = hashlib.sha1(output.encode()).hexdigest()[:16]
                if digest != s.get("output"):
                    s["output"] = digest
                    if fresh:
                        items.append(NewItem(f"{title} is done", summary(output) or "It finished its turn.",
                                             "thread_done", ref, f"thread:{ref}:done:{digest}"))
        # Forget threads that left the window.
        keep = {ws_all.ref_for(ws, tid) for ws, tid in watched}
        st["threads"] = {k: v for k, v in seen.items() if k in keep}
        inbox.save_state(self.name, st)
        return items


class Producers:
    """Runs every producer at most once per `ttl_s`, on demand (a watch poll, a Talk turn)."""

    def __init__(self, inbox: Inbox, producers: list[Producer], ttl_s: float = PRODUCER_TTL_S):
        self.inbox, self.producers, self.ttl_s = inbox, producers, ttl_s
        self._last = float("-inf")
        self._lock = threading.Lock()

    def refresh(self, force: bool = False) -> int:
        """Returns how many items were added. Skips (returns 0) while another refresh is running."""
        if not self._lock.acquire(blocking=False):
            return 0
        try:
            if not force and time.monotonic() - self._last < self.ttl_s:
                return 0
            self._last = time.monotonic()
            added = 0
            for p in self.producers:
                try:
                    for item in p.run(self.inbox):
                        added += self.inbox.add(item) is not None
                except Exception as e:  # noqa: BLE001 - a broken producer must not break the inbox
                    print(f"inbox: producer {p.name} failed: {e}")
            return added
        finally:
            self._lock.release()


def default_producers(cfg, inbox: Inbox, workspace=None) -> Producers:
    """The companion's producers. BB-based ones only with talk_workspace = "bb"."""
    producers: list[Producer] = []
    if cfg.talk_workspace == "bb":
        if workspace is None:
            from .workspace import Workspaces

            workspace = Workspaces.from_config(cfg)
        producers.append(ThreadsProducer(workspace, Path(cfg.data_dir) / "talk_audit.jsonl"))
    return Producers(inbox, producers)


_shared: dict[tuple, tuple[Inbox, Producers]] = {}
_shared_lock = threading.Lock()


def shared(cfg) -> tuple[Inbox, Producers]:
    """One Inbox + Producers per data dir in this process (API, web UI and Talk share the 60 s cache)."""
    key = (str(Path(cfg.data_dir).resolve()), cfg.talk_workspace)
    with _shared_lock:
        if key not in _shared:
            inbox = Inbox(cfg.data_dir)
            _shared[key] = (inbox, default_producers(cfg, inbox))
        return _shared[key]


# --- Talk ----------------------------------------------------------------------------------------


def note_about(sidecar: Path) -> str | None:
    """The sidecar's `about` (an inbox item id the owner opened before talking), if any."""
    try:
        about = json.loads(sidecar.read_text()).get("about")
    except (OSError, ValueError, AttributeError):
        return None
    return about if isinstance(about, str) and about.startswith("in-") else None


def talk_context(inbox: Inbox, about: str | None, thread_output=None, now: datetime | None = None) -> tuple[str, list[str]]:
    """Inbox lines for a talk turn's prompt and the ids to count as mentioned once the turn is answered.

    `about`: the item the owner opened on the watch → its full text plus, for a thread item, the thread's
    latest output (`thread_output(tid) -> str`), so "tell me more" works. Unread items not mentioned yet
    (last 24 h, max 3) may be brought up once."""
    now = now or now_utc()
    parts: list[str] = []
    item = inbox.get(about) if about else None
    if item:
        parts.append(f"HE OPENED THIS INBOX ITEM ON HIS WATCH and is talking about it (received "
                     f"{item.created_at[:16].replace('T', ' ')} UTC):")
        parts.append(f"- {item.title}" + (f": {item.body}" if item.body else ""))
        if item.ref and THREAD_REF_RE.fullmatch(item.ref) and thread_output:
            try:
                out = (thread_output(item.ref) or "").strip()
            except Exception as e:  # noqa: BLE001
                out = f"(could not be read: {e})"
            if out:
                if len(out) > 4000:
                    out = out[:4000].rstrip() + "\n[… cut …]"
                parts += [f"Latest output of that thread ({item.ref}), as evidence:", out]
    fresh = [i for i in inbox.unread() if i.id != about and not i.mentioned_at
             and now - datetime.fromisoformat(i.created_at) <= MENTION_WINDOW][:MENTION_MAX]
    if fresh:
        if parts:
            parts.append("")
        parts.append("UNREAD ON HIS WATCH'S INBOX (new since he last heard about them). If it fits, mention them "
                     "briefly once at the end of your reply, like \"by the way, the cancel feature thread finished\". "
                     "Do not bring them up again in later turns:")
        parts += [f"- {i.title}" + (f": {summary(i.body, 160)}" if i.body else "") for i in fresh]
    # The opened item counts as mentioned too: it is the topic now, not news for a later turn.
    return "\n".join(parts), [i.id for i in fresh] + ([item.id] if item else [])
