"""Talk capabilities used only when the owner asks: morning briefing, evening reflection, mood history.

Everything here is deterministic evidence gathering + storage; Codex only reads the text built here
(docs/talk.md -> "Briefing, reflection, mood"). Mood/energy estimates are recorded quietly for every
transcribed note (talk turns: from the turn's own Codex JSON; normal notes: from the diary build) and
are only ever read back when the owner asks about them.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from .store import Note, Store, write_atomic

# --- triggers ------------------------------------------------------------------------------------
# A cheap pre-check on the transcript so the usual requests get their evidence in the FIRST Codex call
# (no extra detail round). Anything these miss, Codex can still ask for via need_details.

BRIEFING_RE = re.compile(
    r"\bbrief(ing)?\b|what'?s (on|up|planned) (for )?(today|my plate)|what('?s| is) (the plan|happening) "
    r"(for )?today|plan for today|today'?s plan|morning (update|summary|rundown)|rundown", re.I)
REFLECTION_RE = re.compile(r"\breflect(ion|ing)?\b|look(ing)? back (on|at) (my|the|to)day|review (my|the) day", re.I)
MOOD_RE = re.compile(r"\b(mood|energy|energi)\b", re.I)
MOOD_QUESTION_RE = re.compile(r"\b(how|what|been|lately|trend|week|month|days|track|chart|history|pattern)\b", re.I)

MOOD_DAYS_DEFAULT, MOOD_DAYS_MAX = 7, 31
EVENING = time(18, 0)  # "since yesterday evening" for the briefing
DAY_ROLLOVER_H = 4  # a reflection before 04:00 is about the previous day


def triggers(transcript: str) -> dict:
    """{"briefing": True, "reflection": True, "mood_days": 7} for what the transcript clearly asks for."""
    out: dict = {}
    if BRIEFING_RE.search(transcript):
        out["briefing"] = True
    if REFLECTION_RE.search(transcript):
        out["reflection"] = True
    if MOOD_RE.search(transcript) and MOOD_QUESTION_RE.search(transcript):
        out["mood_days"] = mood_days_from_text(transcript)
    return out


def mood_days_from_text(text: str) -> int:
    t = text.lower()
    if re.search(r"\bmonth\b", t):
        return 30
    if m := re.search(r"\b(\d{1,2}) days\b", t):
        return clamp_days(int(m.group(1)))
    if re.search(r"\btoday\b", t) and not re.search(r"\bweek\b", t):
        return 1
    return MOOD_DAYS_DEFAULT


def clamp_days(n) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MOOD_DAYS_MAX, n))


# --- helpers -------------------------------------------------------------------------------------


def read_text(path: Path) -> str | None:
    return path.read_text().strip() if path.exists() else None


def clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def transcript(store: Store, n: Note) -> str | None:
    return read_text(store.audio_path(n).with_suffix(".txt"))


def reply(store: Store, n: Note) -> str | None:
    return read_text(store.audio_path(n).with_name(f"{n.stem}.reply.txt")) if n.is_talk else None


def note_lines(store: Store, notes: list[Note], *, per_note: int, total: int, replies: bool) -> list[str]:
    """`[HH:MM] (talk) words…` lines, oldest first, the whole list capped at `total` characters (newest kept)."""
    lines = []
    for n in notes:
        t = transcript(store, n)
        if t is None:
            lines.append(f"[{n.started:%H:%M}] (not transcribed yet)")
            continue
        line = f"[{n.started:%H:%M}]{' (talk)' if n.is_talk else ''} {clip(t, per_note) or '(no speech)'}"
        if replies and (r := reply(store, n)):
            line += f"\n    Orbit answered: {clip(r, per_note // 2)}"
        lines.append(line)
    out, size = [], 0
    for line in reversed(lines):
        size += len(line) + 1
        if size > total:
            out.append(f"[… {len(lines) - len(out)} earlier notes omitted …]")
            break
        out.append(line)
    return out[::-1]


# --- diary page sections -------------------------------------------------------------------------

TODO_RE = re.compile(r"^- \[ \] (.+)$", re.M)


def page_path(cfg, day: date) -> Path:
    return cfg.diary_dir / f"{day.isoformat()}.md"


def open_todos(page_text: str) -> list[str]:
    """Unticked `- [ ]` items of the page's `## To-dos` section (the owner may tick them in Obsidian)."""
    m = re.search(r"^## To-dos\s*$(.*?)(?=^## |\Z|<!-- orbit:)", page_text, re.M | re.S)
    return [t.strip() for t in TODO_RE.findall(m.group(1))] if m else []


# --- briefing ------------------------------------------------------------------------------------


def briefing_evidence(cfg, store: Store, note: Note, idx) -> str:
    """Morning briefing evidence: yesterday's open to-dos, BB tasks due/in progress, BB threads that ended
    or need input since yesterday evening, today's notes so far. No weather (no location, no source)."""
    now = datetime.now().astimezone()
    today = date.fromisoformat(note.local_date)
    yesterday = today - timedelta(days=1)
    out = [f"BRIEFING EVIDENCE gathered {now:%Y-%m-%d %H:%M} (owner's local time) for {today:%A %d %B}:",
           "Weather: NOT AVAILABLE — the companion has no location and no weather source."]

    page = page_path(cfg, yesterday)
    if page.exists():
        todos = open_todos(page.read_text())
        out.append(f"Open to-dos from yesterday's diary page ({yesterday:%A}):")
        out += [f"- {t}" for t in todos] or ["- (none listed, or all ticked)"]
    else:
        out.append(f"Yesterday ({yesterday:%A}) has no diary page yet, so to-dos were not extracted; "
                   "pick out only clear to-dos from his notes below (say so if there are none):")
        out += note_lines(store, [n for n in store.notes_on(yesterday)], per_note=400, total=3000, replies=False) \
            or ["- (no notes yesterday)"]

    if idx is None:
        out.append("BB tasks and threads: NOT AVAILABLE (the workspace could not be read) — say so briefly.")
    else:
        out += bb_briefing(idx, now, today)
        for name, why in (getattr(idx, "unavailable", None) or {}).items():
            out.append(f"BB tasks and threads of the [{name}] workspace: NOT AVAILABLE ({why}) — say so briefly.")

    earlier = [n for n in store.notes_on(today) if n.started < note.started]
    out.append("His notes so far today:")
    out += note_lines(store, earlier, per_note=300, total=2500, replies=False) or ["- (none yet)"]
    return "\n".join(out)


def bb_briefing(idx, now: datetime, today: date) -> list[str]:
    from .actions import AllRefs
    from .workspace import ago, parse_time

    allrefs = AllRefs.of(idx.refs)
    names = {(ws, pid): p["name"] for ws, r in allrefs.workspaces.items() for pid, p in r.projects.items()}
    default_ws = allrefs.names[0] if allrefs.names else "personal"
    out = []
    soon = today + timedelta(days=1)
    tasks = []
    for t in idx.tasks:
        if t.get("status") in ("done", "canceled"):
            continue
        due = None
        try:
            due = date.fromisoformat(str(t.get("dueDate"))[:10]) if t.get("dueDate") else None
        except ValueError:
            pass
        why = []
        if due and due < today:
            why.append(f"OVERDUE (due {due:%b %d})")
        elif due and due <= soon:
            why.append("due today" if due == today else "due tomorrow")
        if t.get("status") in ("in_progress", "in_review"):
            why.append(str(t["status"]).replace("_", " "))
        if why:
            tasks.append((0 if due and due <= today else 1, t, why))
    tasks.sort(key=lambda x: (x[0], x[1].get("dueDate") or "9999"))
    out.append("BB tasks due (overdue, today, tomorrow) or in progress:")
    out += [f"- [{t.get('_ws') or default_ws}] {t['key']} \"{(t.get('title') or '').strip()}\" — {', '.join(why)}, "
            f"priority {t.get('priority')}"
            for _, t, why in tasks[:12]] or ["- (none)"]

    since = datetime.combine(today - timedelta(days=1), EVENING).astimezone()
    rows = []
    for t in idx.threads:
        updated = parse_time(t.get("updatedAt"))
        if not updated or updated < since:
            continue
        title = (t.get("title") or t.get("titleFallback") or "(untitled)").replace("\n", " ")
        ws = t.get("_ws") or default_ws
        where = names.get((ws, t.get("projectId")), t.get("projectId"))
        if t.get("hasPendingInteraction"):
            state = "WAITING FOR HIS INPUT"
        elif t.get("status") == "idle":
            state = "stopped (agent finished a run; outcome not checked)"
        else:
            state = f"{t.get('status')} (still running)"
        rows.append((0 if t.get("hasPendingInteraction") else 1, f"- [{ws}] {t['id']} \"{title}\" (project {where}) — {state}, "
                                                                  f"updated {ago(updated, now)}"))
    rows.sort(key=lambda r: r[0])
    out.append(f"BB threads updated since yesterday {EVENING:%H:%M} (waiting for input first):")
    out += [r for _, r in rows[:12]] or ["- (none)"]
    if len(rows) > 12:
        out.append(f"- (+{len(rows) - 12} more)")
    return out


# --- reflection ----------------------------------------------------------------------------------

REFLECTION_BEGIN, REFLECTION_END = "<!-- orbit:reflection:begin -->", "<!-- orbit:reflection:end -->"
REFLECTION_BLOCK_RE = re.compile(re.escape(REFLECTION_BEGIN) + r".*?" + re.escape(REFLECTION_END), re.S)


def reflection_day(note: Note) -> date:
    """The day a reflection is about: the note's local date, or the day before if it is past midnight."""
    d = date.fromisoformat(note.local_date)
    return d - timedelta(days=1) if note.started.hour < DAY_ROLLOVER_H else d


def reflection_path(cfg, day: date) -> Path:
    """Companion copy of the saved reflection (source of truth; the diary page shows it)."""
    return cfg.data_dir / "reflections" / f"{day.isoformat()}.md"


def reflection_evidence(cfg, store: Store, note: Note) -> str:
    day = reflection_day(note)
    notes = [n for n in store.notes_on(day) if n.id != note.id]
    out = [f"REFLECTION EVIDENCE for {day:%A %d %B} — all his notes and talk turns that day, oldest first "
           "(talk turns include what you answered):"]
    out += note_lines(store, notes, per_note=700, total=9000, replies=True) or ["- (no other notes that day)"]
    saved = read_text(reflection_path(cfg, day))
    out.append(f"Reflection already saved for that day (a new save replaces it): {saved}" if saved else
               "No reflection saved for that day yet.")
    return "\n".join(out)


def reflection_block(text: str, saved_at: datetime) -> str:
    return "\n".join([REFLECTION_BEGIN, "## Reflection", "", text.strip(), "",
                      f"_Saved from Orbit Talk at {saved_at:%H:%M}._", REFLECTION_END])


def put_reflection(page_text: str, block: str) -> str:
    """Replace the page's reflection block, or add it after the diary block (outside the orbit markers, so a
    diary rebuild — which only rewrites between orbit:begin/end — keeps it)."""
    if REFLECTION_BLOCK_RE.search(page_text):
        return REFLECTION_BLOCK_RE.sub(lambda _m: block, page_text, count=1)
    return page_text.rstrip("\n") + ("\n\n" if page_text.strip() else "") + block + "\n"


def save_reflection(cfg, day: date, text: str) -> Path:
    """Write the reflection (companion copy + the day's diary page). Never touches the diary block."""
    saved_at = datetime.now().astimezone()
    write_atomic(reflection_path(cfg, day), (text.strip() + "\n").encode())
    page = page_path(cfg, day)
    existing = page.read_text() if page.exists() else f"---\ndate: {day.isoformat()}\n---\n"
    write_atomic(page, put_reflection(existing, reflection_block(text, saved_at)).encode())
    return page


def restore_reflection(cfg, day: date, page_text: str) -> str:
    """Diary build: put a saved reflection back if the page lost it (e.g. the page was deleted)."""
    saved = read_text(reflection_path(cfg, day))
    if not saved or REFLECTION_BLOCK_RE.search(page_text):
        return page_text
    when = datetime.fromtimestamp(reflection_path(cfg, day).stat().st_mtime).astimezone()
    return put_reflection(page_text, reflection_block(saved, when))


# --- mood & energy -------------------------------------------------------------------------------

MOODS = (-2, -1, 0, 1, 2)
ENERGIES = (1, 2, 3, 4, 5)
CONFIDENCES = ("low", "medium", "high")
MOOD_SCHEMA = {  # one estimate; used in the talk turn's JSON and per note in the diary build's JSON
    "type": "object",
    "additionalProperties": False,
    "required": ["mood", "energy", "confidence", "rationale"],
    "properties": {
        "mood": {"type": "integer", "enum": list(MOODS)},
        "energy": {"type": "integer", "enum": list(ENERGIES)},
        "confidence": {"type": "string", "enum": list(CONFIDENCES)},
        "rationale": {"type": "string"},
    },
}
MOOD_RULE = ("mood -2 (very low: sad, upset, stressed) … 0 (neutral or no sign) … +2 (very good: happy, excited); "
             "energy 1 (exhausted) … 3 (ordinary) … 5 (very energetic); confidence low when the note says little "
             "about how he feels (e.g. a pure work question or a list); rationale = one short line citing his words")

MOOD_TABLE = """
CREATE TABLE IF NOT EXISTS note_mood (
    note_id    TEXT PRIMARY KEY,
    local_date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    mode       TEXT,                 -- 'talk' or NULL (normal note)
    mood       INTEGER NOT NULL,     -- -2..+2
    energy     INTEGER NOT NULL,     -- 1..5
    confidence TEXT NOT NULL,        -- low / medium / high
    rationale  TEXT NOT NULL,
    source     TEXT NOT NULL,        -- 'talk' (the turn's Codex JSON) or 'diary' (the diary build)
    rated_utc  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS note_mood_date ON note_mood(local_date);
"""


def valid_mood(m) -> dict | None:
    """A clean estimate, or None if the model's value is missing/malformed (then nothing is stored)."""
    if not isinstance(m, dict):
        return None
    try:
        mood, energy = int(m.get("mood")), int(m.get("energy"))
    except (TypeError, ValueError):
        return None
    if mood not in MOODS or energy not in ENERGIES:
        return None
    conf = m.get("confidence") if m.get("confidence") in CONFIDENCES else "low"
    return {"mood": mood, "energy": energy, "confidence": conf,
            "rationale": clip(str(m.get("rationale") or ""), 240)}


class MoodLog:
    """Private per-note mood/energy estimates (table `note_mood` in index.sqlite). Talk estimates win over
    diary-build ones for the same note (the turn saw the note on its own, in conversation)."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "index.sqlite"
        with closing(self._db()) as db, db:
            db.executescript(MOOD_TABLE)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def record(self, note: Note, estimate, source: str) -> bool:
        m = valid_mood(estimate)
        if not m:
            return False
        with closing(self._db()) as db, db:
            if source != "talk":
                row = db.execute("SELECT source FROM note_mood WHERE note_id = ?", (note.id,)).fetchone()
                if row and row["source"] == "talk":
                    return False
            db.execute(
                "INSERT OR REPLACE INTO note_mood (note_id, local_date, started_at, mode, mood, energy, confidence,"
                " rationale, source, rated_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (note.id, note.local_date, note.started_at, note.mode, m["mood"], m["energy"], m["confidence"],
                 m["rationale"], source, datetime.now(timezone.utc).isoformat()))
        return True

    def since(self, first_day: date) -> list[dict]:
        with closing(self._db()) as db:
            rows = db.execute("SELECT * FROM note_mood WHERE local_date >= ? ORDER BY started_at",
                              (first_day.isoformat(),)).fetchall()
        return [dict(r) for r in rows]

    def unrated(self, first_day: date, exclude: str | None = None) -> int:
        """Transcribed notes in the range with no estimate yet (normal notes wait for the diary build)."""
        with closing(self._db()) as db:
            return db.execute(
                "SELECT COUNT(*) FROM notes n LEFT JOIN note_mood m ON m.note_id = n.id "
                "WHERE n.local_date >= ? AND n.transcript_status = 'done' AND m.note_id IS NULL AND n.id != ?",
                (first_day.isoformat(), exclude or "")).fetchone()[0]

    def daily(self, first_day: date) -> list[dict]:
        """Per-day averages (oldest first) for the web UI chart."""
        return daily_averages(self.since(first_day))


def daily_averages(rows: list[dict]) -> list[dict]:
    """Per day: averages over the medium/high-confidence estimates (a "yes" or a work question says little about
    mood); a day with only low-confidence ones averages those and is flagged `low_only`."""
    days: dict[str, list[dict]] = {}
    for r in rows:
        days.setdefault(r["local_date"], []).append(r)
    out = []
    for d, rs in sorted(days.items()):
        sure = [r for r in rs if r["confidence"] != "low"]
        use = sure or rs
        out.append({"date": d, "notes": len(rs), "used": len(use), "low_only": not sure,
                    "mood": round(sum(r["mood"] for r in use) / len(use), 2),
                    "energy": round(sum(r["energy"] for r in use) / len(use), 2)})
    return out


def mood_evidence(moods: MoodLog, days: int, today: date, current: str | None = None) -> str:
    """`current`: the note being answered (rated by this very turn, so not counted as unrated)."""
    days = clamp_days(days) or MOOD_DAYS_DEFAULT
    first = today - timedelta(days=days - 1)
    rows = moods.since(first)
    out = [f"MOOD & ENERGY HISTORY, {first:%a %d %b} – {today:%a %d %b} ({days} day{'s' * (days != 1)}). These are "
           "quiet machine estimates from his transcribed words (mood -2..+2, energy 1..5), not measurements; "
           "speech recognition errors and short notes make them rough."]
    if not rows:
        out.append("- (no estimates in this range)")
    for d in daily_averages(rows):
        out.append(f"{d['date']} ({date.fromisoformat(d['date']):%A}): average mood {d['mood']:+.1f}, energy "
                   f"{d['energy']:.1f}, from {d['used']} of {d['notes']} rated note(s)"
                   + (" (all low confidence)" if d["low_only"] else " (low-confidence ones left out of the average)"
                      if d["used"] < d["notes"] else ""))
    for r in rows[-40:]:
        t = datetime.fromisoformat(r["started_at"])
        out.append(f"- {r['local_date']} {t:%H:%M}{' talk' if r['mode'] == 'talk' else ''}: mood {r['mood']:+d}, "
                   f"energy {r['energy']}, {r['confidence']} confidence — {r['rationale']}")
    if len(rows) > 40:
        out.append(f"(only the newest 40 of {len(rows)} notes are listed; the daily averages cover all)")
    missing = moods.unrated(first, exclude=current)
    if missing:
        out.append(f"{missing} transcribed note(s) in this range have no estimate yet (normal notes are rated when "
                   "their day's diary page is built) — mention that the picture may be incomplete.")
    return "\n".join(out)

