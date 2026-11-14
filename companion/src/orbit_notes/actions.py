"""Talk actions: allowlist + param validation, the one pending action, the audit log, talk settings.

Codex only *proposes* (kind + params as JSON). Everything here is deterministic: params are checked
against a fixed schema per kind, and the exact `bb` argv is built from the validated params — never a
shell string from the model. Execution happens only after a spoken confirmation (talk.py).
See docs/talk.md -> "Workspace assistant".
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .store import write_atomic

PENDING_TTL = timedelta(minutes=10)

# --- settings ------------------------------------------------------------------------------------

# Curated English Piper voices (all present in rhasspy/piper-voices voices.json, checked 2026-11-14).
VOICES = {
    "en_US-lessac-medium": "US English, neutral (default)",
    "en_US-amy-medium": "US English, female",
    "en_US-ryan-high": "US English, male, high quality",
    "en_US-joe-medium": "US English, male",
    "en_US-kristin-medium": "US English, female",
    "en_GB-alan-medium": "British English, male",
    "en_GB-jenny_dioco-medium": "British English, female",
    "en_GB-cori-high": "British English, female, high quality",
}
LENGTH_SCALE_MIN, LENGTH_SCALE_MAX = 0.7, 1.6  # Piper length_scale: < 1 faster, > 1 slower
REPLY_LENGTHS = {
    "short": "1 to 2 short sentences",
    "normal": "2 to 4 short sentences",
    "long": "4 to 7 sentences (still spoken, no lists)",
}
PERSONALITY_MAX = 600
VOICE_NAME_RE = re.compile(r"en_[A-Z]{2}-[a-z0-9_]+-(x_low|low|medium|high)")


def installed_voices(data_dir: Path) -> set[str]:
    """English Piper voices already downloaded into <data_dir>/voices/ (model + config both present)."""
    d = Path(data_dir) / "voices"
    if not d.is_dir():
        return set()
    return {m.name[:-len(".onnx")] for m in d.glob("en_*.onnx")
            if VOICE_NAME_RE.fullmatch(m.name[:-len(".onnx")]) and m.with_name(m.name + ".json").exists()}


def voice_catalog(data_dir: Path, default_voice: str) -> dict[str, dict]:
    """Every voice the web UI may pick: the curated list plus any other installed English voice (and the
    config's default voice). name -> {label, installed}."""
    have = installed_voices(data_dir)
    names = [*VOICES, *sorted(have - set(VOICES))]
    if default_voice not in names:
        names.append(default_voice)
    return {n: {"label": VOICES.get(n) or ("config default voice" if n == default_voice else "installed voice"),
                "installed": n in have} for n in names}


@dataclass
class TalkSettings:
    voice: str = "en_US-lessac-medium"
    length_scale: float = 1.0
    personality: str = ""  # free-text style, layered on the base persona
    reply_length: str = "normal"
    updated_at: str | None = None

    def describe(self) -> str:
        speed = "normal speed" if abs(self.length_scale - 1.0) < 0.01 else (
            f"{'slower' if self.length_scale > 1 else 'faster'} than normal")
        return "\n".join([
            f"- voice: {self.voice} ({VOICES.get(self.voice, 'custom voice from the config')})",
            f"- speaking rate: length_scale {self.length_scale:.2f} ({speed}; allowed {LENGTH_SCALE_MIN}–{LENGTH_SCALE_MAX},"
            " higher = slower)",
            f"- reply length: {self.reply_length} ({REPLY_LENGTHS[self.reply_length]})",
            f"- personality / style: {self.personality or '(none: base persona only)'}",
        ])


class SettingsStore:
    def __init__(self, data_dir: Path, default_voice: str):
        self.path = Path(data_dir) / "talk_settings.json"
        self.default_voice = default_voice

    def get(self) -> TalkSettings:
        s = TalkSettings(voice=self.default_voice)
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text())
                for k in ("voice", "length_scale", "personality", "reply_length", "updated_at"):
                    if k in raw:
                        setattr(s, k, raw[k])
            except (ValueError, TypeError) as e:
                print(f"talk: ignoring unreadable {self.path}: {e}")
        if s.reply_length not in REPLY_LENGTHS:
            s.reply_length = "normal"
        return s

    def apply(self, changes: dict) -> TalkSettings:
        s = self.get()
        for k, v in changes.items():
            setattr(s, k, v)
        s.updated_at = now_utc().isoformat()
        write_atomic(self.path, json.dumps(asdict(s), indent=1).encode())
        return s


# --- validation ----------------------------------------------------------------------------------


class Invalid(ValueError):
    """A proposed action that is not allowed or has bad params. The message is spoken to the owner."""


@dataclass
class WorkspaceRefs:
    """What the companion knows exists (from the workspace index), to check references against."""

    projects: dict[str, dict] = field(default_factory=dict)  # bb project id -> {name, path}
    threads: dict[str, dict] = field(default_factory=dict)  # thread id -> {title, project}
    trackers: dict[str, str] = field(default_factory=dict)  # tracker prefix -> name
    ok: bool = True  # False when the index could not be read

    def project_id(self, ref: str) -> str:
        ref = ref.strip()
        if ref in self.projects:
            return ref
        matches = [pid for pid, p in self.projects.items() if p["name"].lower() == ref.lower()]
        if len(matches) == 1:
            return matches[0]
        raise Invalid(f"I don't know a project called {ref!r}" if self.ok else
                      "I couldn't read the workspace to check the project")


THREAD_RE = re.compile(r"thr_[a-z0-9]{6,20}")
TASK_KEY_RE = re.compile(r"([A-Z][A-Z0-9]{0,9})-(\d{1,6})")
MEMORY_NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}")
TASK_STATUSES = ("backlog", "todo", "in_progress", "in_review", "done", "canceled")
PRIORITIES = ("urgent", "high", "medium", "low", "none")
MEMORY_KINDS = ("fact", "preference", "decision", "procedure", "episode", "reference")

# kind -> (required keys, optional keys)
PARAMS = {
    "thread_spawn": ({"project", "prompt"}, {"title"}),
    "thread_tell": ({"thread_id", "message"}, set()),
    "task_create": ({"project", "title"}, {"description", "priority", "due"}),
    "task_update": ({"key"}, {"status", "priority", "title", "due"}),
    "task_comment": ({"key", "body"}, set()),
    "memory_add": ({"scope", "name", "summary", "details", "reason"}, {"project", "kind"}),
    "settings": (set(), {"voice", "length_scale", "personality", "reply_length"}),
}
KINDS = tuple(PARAMS)


def _text(params: dict, key: str, limit: int, *, required: bool = True) -> str | None:
    v = params.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v.strip():
        raise Invalid(f"the {key.replace('_', ' ')} is missing")
    v = v.strip()
    if len(v) > limit:
        raise Invalid(f"the {key.replace('_', ' ')} is too long ({len(v)} characters, max {limit})")
    return v


def _enum(params: dict, key: str, allowed: tuple[str, ...]) -> str | None:
    v = params.get(key)
    if v is None:
        return None
    if v not in allowed:
        raise Invalid(f"{key} must be one of {', '.join(allowed)}")
    return v


def _due(params: dict) -> str | None:
    v = params.get("due")
    if v is None:
        return None
    try:
        return date.fromisoformat(str(v)).isoformat()
    except ValueError:
        raise Invalid("the due date must be a calendar date") from None


def _task_key(params: dict, refs: WorkspaceRefs) -> str:
    key = str(params.get("key") or "").strip().upper()
    m = TASK_KEY_RE.fullmatch(key)
    if not m:
        raise Invalid(f"{key or 'the task key'} is not a task key like ABC-12")
    if refs.ok and m.group(1) not in refs.trackers:
        raise Invalid(f"there is no task project with prefix {m.group(1)}")
    return key


def validate(kind: str, params: dict, refs: WorkspaceRefs) -> dict:
    """Normalised params for an allowed kind, or Invalid with a reason that can be spoken."""
    if kind not in PARAMS:
        raise Invalid(f"{kind!r} is not something I'm allowed to do")
    if not isinstance(params, dict):
        raise Invalid("the action details were not readable")
    params = {k: v for k, v in params.items() if v is not None}
    required, optional = PARAMS[kind]
    unknown = set(params) - required - optional
    if unknown:
        raise Invalid(f"{kind} does not support {', '.join(sorted(unknown))}")
    out: dict = {}
    if kind == "thread_spawn":
        out["project"] = refs.project_id(str(params.get("project") or ""))
        out["prompt"] = _text(params, "prompt", 6000)
        if t := _text(params, "title", 100, required=False):
            out["title"] = t
    elif kind == "thread_tell":
        tid = str(params.get("thread_id") or "").strip()
        if not THREAD_RE.fullmatch(tid):
            raise Invalid(f"{tid or 'the thread id'} is not a thread id")
        if refs.ok and tid not in refs.threads:
            raise Invalid(f"I can't find thread {tid} in the workspace")
        out["thread_id"] = tid
        out["message"] = _text(params, "message", 6000)
    elif kind == "task_create":
        prefix = str(params.get("project") or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9]{0,9}", prefix) or (refs.ok and prefix not in refs.trackers):
            raise Invalid(f"there is no task project with prefix {prefix or '(none)'}")
        out["project"] = prefix
        out["title"] = _text(params, "title", 200)
        if d := _text(params, "description", 6000, required=False):
            out["description"] = d
        if p := _enum(params, "priority", PRIORITIES):
            out["priority"] = p
        if due := _due(params):
            out["due"] = due
    elif kind == "task_update":
        out["key"] = _task_key(params, refs)
        if s := _enum(params, "status", TASK_STATUSES):
            out["status"] = s
        if p := _enum(params, "priority", PRIORITIES):
            out["priority"] = p
        if t := _text(params, "title", 200, required=False):
            out["title"] = t
        if due := _due(params):
            out["due"] = due
        if len(out) == 1:
            raise Invalid("nothing to change on the task (status, priority, title or due date)")
    elif kind == "task_comment":
        out["key"] = _task_key(params, refs)
        out["body"] = _text(params, "body", 6000)
    elif kind == "memory_add":
        out["scope"] = _enum(params, "scope", ("project", "global")) or "global"
        if out["scope"] == "project":
            out["project"] = refs.project_id(str(params.get("project") or ""))
        elif "project" in params:
            raise Invalid("a global memory doesn't belong to a project")
        name = str(params.get("name") or "").strip()
        if not MEMORY_NAME_RE.fullmatch(name):
            raise Invalid("the memory name must be lowercase letters, digits, dots, dashes or underscores")
        out["name"] = name
        out["summary"] = _text(params, "summary", 400)
        out["details"] = _text(params, "details", 16000)
        out["reason"] = _text(params, "reason", 500)
        if k := _enum(params, "kind", MEMORY_KINDS):
            out["kind"] = k
    elif kind == "settings":
        out = validate_settings(params)
    return out


def validate_settings(params: dict, voices=VOICES) -> dict:
    """Talk settings (spoken confirm flow uses the curated `VOICES`; the web UI also allows installed ones)."""
    out: dict = {}
    if "voice" in params:
        if params["voice"] not in voices:
            raise Invalid(f"I don't have the voice {params['voice']!r}; I can use {', '.join(voices)}")
        out["voice"] = params["voice"]
    if "length_scale" in params:
        try:
            ls = round(float(params["length_scale"]), 2)
        except (TypeError, ValueError):
            raise Invalid("the speaking rate must be a number") from None
        if not LENGTH_SCALE_MIN <= ls <= LENGTH_SCALE_MAX:
            raise Invalid(f"the speaking rate must stay between {LENGTH_SCALE_MIN} and {LENGTH_SCALE_MAX}")
        out["length_scale"] = ls
    if "personality" in params:
        p = params["personality"]
        if not isinstance(p, str) or len(p.strip()) > PERSONALITY_MAX:
            raise Invalid(f"the personality must be text of at most {PERSONALITY_MAX} characters")
        out["personality"] = " ".join(p.split())
    if "reply_length" in params:
        out["reply_length"] = _enum(params, "reply_length", tuple(REPLY_LENGTHS))
    if not out:
        raise Invalid("no setting to change")
    return out


@dataclass
class Command:
    argv: list[str]  # after the `bb` executable
    stdin: str | None = None
    env: dict[str, str] = field(default_factory=dict)  # extra env (e.g. BB_PROJECT_ID for project memories)


ORCHESTRATED_FOOTER = """

---
Started from Orbit Talk (Raiz's watch). Orbit work is coordinated by the Orbit orchestrator thread
{parent}, which is this thread's parent: it reviews and merges your branch, deploys the companion
and installs on the watch. Work on your own branch, don't merge into main, don't run
companion/deploy/install.sh, and follow CLAUDE.md. Report what you changed and how it was verified."""


def command(kind: str, p: dict) -> Command:
    """The exact bb invocation for validated params. `--key=value` so a value can never be read as a flag.
    A thread_spawn with `parent` (set by the companion for Orbit-project spawns, never by Codex) becomes a
    child of that orchestrator thread in its own worktree off main."""
    if kind == "thread_spawn":
        argv = ["thread", "spawn", f"--project={p['project']}", "--prompt-file", "-"]
        if "title" in p:
            argv.append(f"--title={p['title']}")
        prompt = p["prompt"]
        if p.get("parent"):
            argv += [f"--parent-thread={p['parent']}", "--new-environment=worktree", "--base-branch=main"]
            prompt += ORCHESTRATED_FOOTER.format(parent=p["parent"])
        return Command(argv + ["--json"], stdin=prompt)
    if kind == "thread_tell":
        return Command(["thread", "tell", p["thread_id"], "--message-file", "-", "--json"], stdin=p["message"])
    if kind == "task_create":
        argv = ["tasks", "create", f"--project={p['project']}", f"--title={p['title']}"]
        argv += [f"--{k}={p[k]}" for k in ("description", "priority", "due") if k in p]
        return Command(argv + ["--json"])
    if kind == "task_update":
        argv = ["tasks", "update", p["key"]]
        argv += [f"--{k}={p[k]}" for k in ("status", "priority", "title", "due") if k in p]
        return Command(argv + ["--json"])
    if kind == "task_comment":
        return Command(["tasks", "comment", p["key"], f"--body={p['body']}", "--author=Orbit Talk", "--json"])
    if kind == "memory_add":
        argv = ["memory", "add", f"--scope={p['scope']}", f"--name={p['name']}", f"--summary={p['summary']}",
                f"--details={p['details']}", f"--reason={p['reason']}"]
        if "kind" in p:
            argv.append(f"--kind={p['kind']}")
        return Command(argv + ["--json"], env={"BB_PROJECT_ID": p["project"]} if p["scope"] == "project" else {})
    raise ValueError(f"no bb command for {kind}")


# --- pending action + audit ----------------------------------------------------------------------


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Pending:
    id: str
    kind: str
    params: dict
    summary: str
    created_at: str
    note_id: str | None = None

    @property
    def expires_at(self) -> datetime:
        return datetime.fromisoformat(self.created_at) + PENDING_TTL

    def expired(self, now: datetime | None = None) -> bool:
        return (now or now_utc()) >= self.expires_at


class ActionLog:
    """The single pending action (`talk_pending.json`) and the append-only audit log (`talk_audit.jsonl`)."""

    def __init__(self, data_dir: Path):
        self.pending_path = Path(data_dir) / "talk_pending.json"
        self.audit_path = Path(data_dir) / "talk_audit.jsonl"
        self._lock = threading.Lock()

    def pending(self) -> Pending | None:
        if not self.pending_path.exists():
            return None
        try:
            return Pending(**json.loads(self.pending_path.read_text()))
        except (ValueError, TypeError) as e:
            print(f"talk: dropping unreadable pending action: {e}")
            return None

    def propose(self, kind: str, params: dict, summary: str, note_id: str | None) -> Pending:
        p = Pending(uuid.uuid4().hex[:8], kind, params, summary, now_utc().isoformat(), note_id)
        write_atomic(self.pending_path, json.dumps(asdict(p), indent=1).encode())
        return p

    def clear(self) -> None:
        self.pending_path.unlink(missing_ok=True)

    def audit(self, event: str, *, pending: Pending | None = None, note_id: str | None = None, **extra) -> dict:
        entry = {"at": now_utc().isoformat(), "event": event, "note_id": note_id}
        if pending:
            entry.update(action_id=pending.id, kind=pending.kind, summary=pending.summary, params=pending.params)
        entry.update(extra)
        with self._lock, self.audit_path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def entries(self, limit: int = 100) -> list[dict]:
        if not self.audit_path.exists():
            return []
        lines = self.audit_path.read_text().splitlines()[-limit:]
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
