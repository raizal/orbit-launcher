"""BB workspace access for Talk: a compact index, on-demand evidence, and executing confirmed actions.

Everything goes through the `bb` CLI (`--json`) as the owner. Codex never calls bb: it reads the text
built here and may ask for details (one round per turn). Every evidence line carries its time so a
reply can say how recent it is. See docs/talk.md -> "Workspace assistant".

The owner can have several BB workspaces (independent bb desktop instances, each with its own data dir
and server: `~/.bb` = "personal", `~/.bb-work` = "work", ...). `Workspace` is one of them; `Workspaces`
fans reads out to all of them in parallel, labels everything with the workspace name, and routes
details and actions to the workspace that owns the id. A workspace that can't be reached is reported as
such — its commands never fall back to another workspace.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from .actions import AllRefs, Command, WorkspaceRefs, split_ref

INDEX_TTL_S = 180
CALL_TIMEOUT_S = 25
DETAILS_DEADLINE_S = 45
MAX_THREADS_IN_INDEX = 30
MAX_OPEN_TASKS = 40
MAX_DONE_TASKS = 15
DONE_WINDOW = timedelta(days=7)
DETAIL_CAPS = {"threads": 4, "tasks": 4, "memory": 3, "projects": 2}
# A thread's own context must not leak into commands run for the owner (spawn parent, project scope...).
# For bb itself every inherited BB_* variable is dropped (`bb_base_env`), so a companion started from
# inside a BB thread (or any workspace) never talks to the wrong server; this tuple is for other tools.
SCRUB_ENV = ("BB_PROJECT_ID", "BB_THREAD_ID", "BB_ENVIRONMENT_ID", "BB_THREAD_STORAGE", "BB_SERVER_URL",
             "BB_HOST_DAEMON_PORT", "BB_DATA_DIR", "BB_CLI")
RUNTIME_FILE = "bb-app-runtime.json"
PERSONAL = "personal"
HOME: Path | None = None  # where to discover ~/.bb* data dirs (None = the user's home; tests point it away)
NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,30}")
NOT_FOUND_RE = re.compile(r"not[ _]found|no such|unknown (thread|task|project)", re.I)


class WorkspaceError(RuntimeError):
    pass


def bb_base_env() -> dict[str, str]:
    """The process env without any BB_* variable (each workspace then sets its own)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("BB_")}


@dataclass(frozen=True)
class WorkspaceSpec:
    name: str
    data_dir: Path | None = None  # None: bb's own defaults (no BB_DATA_DIR / BB_SERVER_URL set)
    server_url: str | None = None  # fixed; else read from <data_dir>/bb-app-runtime.json at every call


def discover(home: Path | None = None) -> list[WorkspaceSpec]:
    """`~/.bb` (personal) and `~/.bb-<name>` data dirs that have a bb-app-runtime.json — or a bb.db, so a
    workspace whose app is closed is still listed (and reported unreachable) instead of vanishing. Personal
    first."""
    home = Path(home or HOME or Path.home())
    out = []
    for d in sorted(home.glob(".bb*")):
        if not d.is_dir() or not ((d / RUNTIME_FILE).is_file() or (d / "bb.db").is_file()):
            continue
        name = PERSONAL if d.name == ".bb" else d.name[4:].lower() if d.name.startswith(".bb-") else ""
        if NAME_RE.fullmatch(name):
            out.append(WorkspaceSpec(name, d))
    return sorted(out, key=lambda w: (w.name != PERSONAL, w.name))


def configured_specs(cfg) -> list[WorkspaceSpec]:
    """Workspaces Talk uses: `[[workspaces]]` from the config if any, else the discovered ones; restricted
    to `talk_workspaces` if set. Nothing found at all → one "personal" workspace on bb's own defaults."""
    specs: list[WorkspaceSpec] = []
    for w in getattr(cfg, "workspaces", None) or []:
        name = str(w.get("name") or "").strip().lower()
        if not NAME_RE.fullmatch(name):
            print(f"talk: ignoring [[workspaces]] entry with a bad name: {w!r}")
            continue
        d = w.get("data_dir")
        d = Path(str(d)).expanduser() if d else None
        if d and not d.is_absolute():
            d = (Path(cfg.path).parent / d).resolve()
        specs.append(WorkspaceSpec(name, d, str(w["server_url"]) if w.get("server_url") else None))
    if not specs:
        specs = discover()
    wanted = [n.strip().lower() for n in (getattr(cfg, "talk_workspaces", None) or []) if n.strip()]
    if wanted:
        have = {s.name: s for s in specs}
        home = Path(HOME or Path.home())
        # A restricted-to workspace that isn't there stays in the list (it is reported unreachable): never
        # silently replaced by another one.
        specs = [have.get(n) or WorkspaceSpec(n, home / (".bb" if n == PERSONAL else f".bb-{n}")) for n in wanted]
    return specs or [WorkspaceSpec(PERSONAL)]


_client_versions: dict[str, str | None] = {}


def client_version(exe: str) -> str | None:
    """`bb --version` of the installed CLI (cached), to explain version-skew failures."""
    if exe not in _client_versions:
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10, env=bb_base_env(),
                               stdin=subprocess.DEVNULL)
            m = re.search(r"\d+\.\d+\.\d+", r.stdout)
            _client_versions[exe] = m.group(0) if m else None
        except (OSError, subprocess.SubprocessError):
            _client_versions[exe] = None
    return _client_versions[exe]


def local_now() -> datetime:
    return datetime.now().astimezone()


def parse_time(value) -> datetime | None:
    """bb times: epoch milliseconds (core) or ISO strings (tasks plugin)."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000).astimezone()
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except (ValueError, OSError, OverflowError):
        return None


def ago(t: datetime | None, now: datetime) -> str:
    """'3 h ago (Nov 14 07:10)' — relative for speaking, absolute for checking."""
    if t is None:
        return "time unknown"
    s = max(0, (now - t).total_seconds())
    if s < 90:
        rel = "just now"
    elif s < 3600:
        rel = f"{round(s / 60)} min ago"
    elif s < 36 * 3600:
        rel = f"{round(s / 3600)} h ago"
    else:
        rel = f"{round(s / 86400)} days ago"
    return f"{rel} ({t:%b %d %H:%M})"


def clip(text: str, head: int, tail: int) -> str:
    text = text.strip()
    if len(text) <= head + tail + 20:
        return text
    return f"{text[:head].rstrip()}\n[… {len(text) - head - tail} characters omitted …]\n{text[-tail:].lstrip()}"


@dataclass
class Index:
    taken_at: datetime
    text: str
    refs: WorkspaceRefs
    errors: list[str] = field(default_factory=list)
    threads: list[dict] = field(default_factory=list)  # live threads as bb returned them + "_ws" (briefing)
    tasks: list[dict] = field(default_factory=list)  # every task of every task project + "_ws" (briefing)
    unavailable: dict[str, str] = field(default_factory=dict)  # workspace name -> why it couldn't be read
    stamp: tuple = ()  # identifies this exact snapshot (per-workspace snapshot times)
    body: str = ""  # text without the snapshot header (one section of a merged snapshot)


class Workspace:
    """One BB workspace (one bb desktop instance)."""

    def __init__(self, exe: str = "bb", ttl_s: int = INDEX_TTL_S, spec: WorkspaceSpec | None = None):
        self.exe_name, self.ttl_s = exe, ttl_s
        self.spec = spec or WorkspaceSpec(PERSONAL)
        self.name = self.spec.name
        self._index: Index | None = None
        self._lock = threading.Lock()

    def runtime(self) -> dict | None:
        """The workspace's bb-app-runtime.json (None for bb's defaults). Missing / stale → unreachable."""
        d = self.spec.data_dir
        if d is None:
            return None
        try:
            rt = json.loads((d / RUNTIME_FILE).read_text())
        except FileNotFoundError:
            raise WorkspaceError(f"the {self.name} workspace is unreachable: its bb app is not running "
                                 f"(no {d / RUNTIME_FILE})") from None
        except (OSError, ValueError) as e:
            raise WorkspaceError(f"the {self.name} workspace is unreachable: unreadable {RUNTIME_FILE} ({e})") from None
        pid = rt.get("pid")
        if isinstance(pid, int) and pid > 0:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                raise WorkspaceError(f"the {self.name} workspace is unreachable: its bb app is not running "
                                     f"(stale {RUNTIME_FILE}, pid {pid} is gone)") from None
            except PermissionError:
                pass
        return rt

    def bb_env(self) -> dict[str, str]:
        """BB_DATA_DIR + BB_SERVER_URL for this workspace, read now (the URL changes when bb restarts). Both
        are needed: with only BB_DATA_DIR the CLI silently talks to the default (personal) server."""
        if self.spec.data_dir is None:
            return {}
        url = self.spec.server_url
        if not url:
            url = (self.runtime() or {}).get("serverUrl")
            if not url:
                raise WorkspaceError(f"the {self.name} workspace is unreachable: no serverUrl in its {RUNTIME_FILE}")
        return {"BB_DATA_DIR": str(self.spec.data_dir), "BB_SERVER_URL": url}

    def server_version(self) -> str | None:
        try:
            rt = self.runtime()
        except WorkspaceError:
            return None
        return (rt or {}).get("version")

    def _failure(self, argv: list[str], msg: str) -> WorkspaceError:
        """A failed command; on a server whose bb version differs from the CLI's, say so (version skew)."""
        server = self.server_version()
        if server and not NOT_FOUND_RE.search(msg):
            client = client_version(self.exe())
            if client and client != server:
                return WorkspaceError(f"bb {' '.join(argv[:2])} is not available in the {self.name} workspace "
                                      f"(bb {server}, the CLI is {client}): {msg}")
        return WorkspaceError(msg)

    # --- running bb ------------------------------------------------------------------------------

    def exe(self) -> str:
        exe = shutil.which(self.exe_name)
        if not exe:
            raise WorkspaceError(f"the bb CLI ({self.exe_name}) is not on PATH")
        return exe

    def run(self, argv: list[str], *, stdin: str | None = None, env: dict[str, str] | None = None,
            timeout: float = CALL_TIMEOUT_S, as_json: bool = True, cwd: str | None = None):
        if as_json and "--json" not in argv:
            argv = [*argv, "--json"]
        full_env = bb_base_env()
        full_env.update(self.bb_env())
        full_env.update(env or {})
        try:
            feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
            r = subprocess.run([self.exe(), *argv], capture_output=True, text=True, env=full_env,
                               timeout=timeout, cwd=cwd or "/", **feed)
        except subprocess.TimeoutExpired:
            raise WorkspaceError(f"bb {' '.join(argv[:2])} timed out after {timeout:.0f}s") from None
        if not as_json:
            if r.returncode != 0:
                raise self._failure(argv, f"bb {' '.join(argv[:2])} failed: {(r.stderr or r.stdout).strip()[-300:]}")
            return r.stdout
        try:
            data = json.loads(r.stdout) if r.stdout.strip() else None
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("ok") is False:
            err = data.get("error") or {}
            raise self._failure(argv, err.get("message") or err.get("code") or "bb reported an error")
        if r.returncode != 0 or data is None:
            raise self._failure(argv, f"bb {' '.join(argv[:2])} failed ({r.returncode}): "
                                      f"{(r.stderr or r.stdout).strip()[-300:]}")
        return data

    # --- index -----------------------------------------------------------------------------------

    def index(self, max_age_s: float | None = None) -> Index:
        ttl = self.ttl_s if max_age_s is None else max_age_s
        with self._lock:
            idx = self._index
            if idx and (local_now() - idx.taken_at).total_seconds() <= ttl:
                return idx
            idx = self._build_index()
            self._index = idx
            return idx

    def warm(self) -> None:
        """Refresh the index in the background (called while a talk note is being transcribed)."""
        threading.Thread(target=lambda: self._safe_index(), name=f"bb-index-{self.name}", daemon=True).start()

    def _safe_index(self) -> None:
        try:
            self.index()
        except Exception as e:  # noqa: BLE001 - best effort; the turn rebuilds it and reports errors
            print(f"talk: workspace index ({self.name}) failed: {e}")

    def _build_index(self) -> Index:
        t0 = time.monotonic()
        now = local_now()
        self.exe()  # no bb at all: one clear error instead of a snapshot made only of errors
        self.bb_env()  # bb app not running: same
        ws = self.name
        with ThreadPoolExecutor(max_workers=8) as pool:
            fp = pool.submit(self.run, ["project", "list"])
            ft = pool.submit(self.run, ["thread", "list"])
            ftr = pool.submit(self.run, ["tasks", "project", "list"])
            errors, refs = [], WorkspaceRefs()

            def get(fut, what):
                try:
                    return fut.result()
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{what}: {e}")
                    return None

            projects = get(fp, "projects") or []
            trackers = (get(ftr, "task projects") or {}).get("projects", [])
            task_futs = {tp["prefix"]: pool.submit(self.run, ["tasks", "list", f"--project={tp['prefix']}",
                                                               "--limit=200"]) for tp in trackers}
            threads = get(ft, "threads") or []
            tasks = []
            for prefix, fut in task_futs.items():
                tasks += [dict(t, _prefix=prefix) for t in (get(fut, f"tasks {prefix}") or {}).get("tasks", [])]

        if not projects and not threads and errors:
            raise WorkspaceError("; ".join(errors))
        refs.ok, refs.name = not errors, ws
        lines = []
        names = {}
        lines.append("Projects (bb project id, name, repo path):")
        for p in projects:
            path = next((s.get("path") for s in p.get("sources", []) if s.get("isDefault")), None) or \
                   next((s.get("path") for s in p.get("sources", [])), None)
            names[p["id"]] = p["name"]
            refs.projects[p["id"]] = {"name": p["name"], "path": path}
            lines.append(f"- [{ws}] {p['id']} {p['name']} — {path or 'no local path'}")

        live = [t for t in threads if not t.get("archivedAt") and not t.get("deletedAt")
                and t.get("visibility", "visible") == "visible"]
        for t in live:
            refs.threads[t["id"]] = {"title": t.get("title") or t.get("titleFallback") or "",
                                     "project": t.get("projectId"), "status": t.get("status"),
                                     "updated": parse_time(t.get("updatedAt"))}
        live.sort(key=lambda t: t.get("updatedAt") or 0, reverse=True)
        lines.append(f"Recent threads ({min(len(live), MAX_THREADS_IN_INDEX)} most recently updated of {len(live)}; "
                     "status 'idle' only means no agent is running now — not that the work is finished; "
                     "ask for details to know the outcome):")
        for t in live[:MAX_THREADS_IN_INDEX]:
            extra = []
            if t.get("hasPendingInteraction"):
                extra.append("waiting for the owner's input")
            if t.get("parentThreadId"):
                extra.append(f"child of {t['parentThreadId']}")
            title = (t.get("title") or t.get("titleFallback") or "(untitled)").replace("\n", " ")
            lines.append(f"- [{ws}] {t['id']} (project {names.get(t.get('projectId'), t.get('projectId'))}) \"{title}\" — "
                         f"{t.get('status')}, updated {ago(parse_time(t.get('updatedAt')), now)}"
                         + (f"; {'; '.join(extra)}" if extra else ""))

        for tp in trackers:
            refs.trackers[tp["prefix"]] = tp["name"]
        if trackers:
            lines.append(f"[{ws}] Task projects (prefix, name, linked bb project): " + "; ".join(
                f"{tp['prefix']} {tp['name']} ({names.get(tp.get('linkedBbProjectId'), 'not linked')})"
                for tp in trackers))
        open_tasks = [t for t in tasks if t.get("status") not in ("done", "canceled")]
        open_tasks.sort(key=lambda t: t.get("updatedAt") or "", reverse=True)
        done = [t for t in tasks if t.get("status") in ("done", "canceled")
                and (u := parse_time(t.get("updatedAt"))) and now - u <= DONE_WINDOW]
        done.sort(key=lambda t: t.get("updatedAt") or "", reverse=True)

        def task_line(t):
            bits = [t.get("status"), t.get("priority")]
            if t.get("agentsWorking"):
                bits.append(f"{t['agentsWorking']} agent(s) working")
            if t.get("dueDate"):
                bits.append(f"due {t['dueDate']}")
            return (f"- [{ws}] {t['key']} \"{t.get('title', '').strip()}\" — {', '.join(b for b in bits if b)}, "
                    f"updated {ago(parse_time(t.get('updatedAt')), now)}")

        lines.append(f"Open tasks ({min(len(open_tasks), MAX_OPEN_TASKS)} most recently updated of {len(open_tasks)}):")
        lines += [task_line(t) for t in open_tasks[:MAX_OPEN_TASKS]] or ["- (none)"]
        lines.append("Tasks finished or canceled in the last 7 days:")
        lines += [task_line(t) for t in done[:MAX_DONE_TASKS]] or ["- (none)"]
        if errors:
            lines.append("NOT AVAILABLE in this snapshot (treat as no evidence): " + "; ".join(errors))
        print(f"talk: workspace index ({ws}) {time.monotonic() - t0:.1f}s ({len(live)} threads, {len(tasks)} tasks"
              + (f", errors: {errors}" if errors else "") + ")")
        body = "\n".join(lines)
        head = (f"BB WORKSPACE SNAPSHOT taken {now:%Y-%m-%d %H:%M} (owner's local time, {now:%Z}). "
                f"Ages are relative to that time. Workspace: [{ws}] (every line is labelled with it).")
        return Index(now, f"{head}\n{body}", refs, errors, threads=[dict(t, _ws=ws) for t in live],
                     tasks=[dict(t, _ws=ws) for t in tasks], stamp=((ws, now),), body=body)

    # --- details (one round per turn) ------------------------------------------------------------

    def details(self, need: dict, idx: Index) -> str:
        """Details from this workspace alone (see `Workspaces.details`)."""
        return Workspaces([self]).details(need, idx)

    def thread_details(self, tid: str, refs: WorkspaceRefs) -> str:
        info = refs.threads.get(tid)
        if not info and refs.ok:
            return "(no such thread in the workspace)"
        now = local_now()
        head = ""
        if info:
            proj = refs.projects.get(info["project"], {}).get("name", info["project"])
            head = (f"\"{info['title']}\" in {proj}; status {info['status']}, last update "
                    f"{ago(info['updated'], now)}.\n")
        log = self.run(["thread", "log", tid, "--limit=2"], as_json=False)
        return head + "Latest two exchanges (newest last):\n" + clip(log, 1200, 3800)

    def task_details(self, key: str) -> str:
        d = self.run(["tasks", "show", key])
        t, now = d["task"], local_now()
        lines = [f"\"{t.get('title')}\" — {t.get('status')}, priority {t.get('priority')}, created "
                 f"{ago(parse_time(t.get('createdAt')), now)}, updated {ago(parse_time(t.get('updatedAt')), now)}"
                 + (f", due {t['dueDate']}" if t.get("dueDate") else "")]
        if t.get("description"):
            lines += ["Description:", clip(t["description"], 1500, 300)]
        for s in d.get("subtasks", []):
            lines.append(f"- subtask {s['key']} \"{s.get('title')}\" — {s.get('status')}, "
                         f"updated {ago(parse_time(s.get('updatedAt')), now)}")
        for th in d.get("taskThreads", []):
            lines.append(f"- agent thread {th.get('threadId')} \"{th.get('title')}\" — {th.get('liveStatus')}")
        comments = d.get("comments", [])[-6:]
        if comments:
            lines.append("Latest comments (oldest first):")
            for c in comments:
                lines.append(f"- {ago(parse_time(c.get('createdAt')), now)} {c.get('authorName') or c.get('kind')}: "
                             + clip(" ".join(str(c.get("body", "")).split()), 400, 100))
        return "\n".join(lines)

    def memory(self, query: str, project: str, refs: WorkspaceRefs) -> str:
        if not query.strip():
            return "(empty query)"
        if project:
            try:
                pids = [refs.project_id(project)]
            except ValueError as e:
                return f"({e})"
        else:
            pids = list(refs.projects) or [None]

        def search(pid):  # project memories need a project context; global ones come with every search
            env = {"BB_PROJECT_ID": pid} if pid else {}
            return self.run(["memory", "search", *query.split(), "--scope=all", "--limit=5"], env=env)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(search, pids))
        seen, found = set(), []
        for d in results:
            for m in d.get("memories", []):
                mid = m.get("id") or m.get("name")
                if mid not in seen:
                    seen.add(mid)
                    found.append(m)
        if not found:
            return "(no matching memories)"
        now = local_now()
        out = []
        for m in found[:8]:
            when = parse_time(m.get("updatedAt") or m.get("createdAt"))
            where = refs.projects.get(m.get("projectId"), {}).get("name") if m.get("projectId") else "global"
            text = m.get("details") or m.get("summary") or ""
            out.append(f"- [{self.name}] {m.get('name')} ({where}, {m.get('kind', 'fact')}, saved {ago(when, now)}): "
                       + clip(" ".join(str(text).split()), 600, 100))
        return "\n".join(out)

    def git(self, ref: str, refs: WorkspaceRefs) -> str:
        try:
            pid = refs.project_id(ref)
        except ValueError as e:
            return f"({e})"
        path = refs.projects[pid].get("path")
        if not path or not os.path.isdir(path):
            return "(no local repository path for this project)"

        def git(*args):
            r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=CALL_TIMEOUT_S)
            if r.returncode != 0:
                raise WorkspaceError(r.stderr.strip()[-200:] or "git failed")
            return r.stdout.strip()

        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        dirty = len([ln for ln in git("status", "--porcelain").splitlines() if ln.strip()])
        log = git("log", "--all", "--date-order", "-n", "25", "--date=format-local:%Y-%m-%d %H:%M",
                  "--pretty=format:%h %ad%d %s")
        return (f"Repo {path}, checked-out branch {branch}, {dirty} uncommitted change(s) in that checkout "
                f"(worktrees of threads are separate).\nLatest commits on all branches (newest first):\n{log}")

    # --- actions ---------------------------------------------------------------------------------

    def execute(self, cmd: Command):
        """Run a confirmed action. Long timeouts: a spawn may need to create a worktree."""
        result = self.run(cmd.argv, stdin=cmd.stdin, env=cmd.env, timeout=90)
        self._index = None  # the workspace changed; next turn reads it fresh
        return result


class Workspaces:
    """All of the owner's BB workspaces: one merged, labelled index; details and actions routed by owner.

    Each workspace keeps its own 3-minute index cache and they are read in parallel. A workspace that
    fails is listed as NOT AVAILABLE in the merged snapshot; the others are unaffected."""

    def __init__(self, members: list[Workspace]):
        if not members:
            raise ValueError("no workspaces")
        self.members: dict[str, Workspace] = {}
        for w in members:
            self.members.setdefault(w.name, w)
        self._merged: Index | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, cfg, exe: str = "bb") -> Workspaces:
        return cls([Workspace(exe, spec=s) for s in configured_specs(cfg)])

    @classmethod
    def wrap(cls, ws) -> Workspaces:
        return ws if isinstance(ws, Workspaces) else cls([ws])

    @property
    def names(self) -> list[str]:
        return list(self.members)

    @property
    def primary(self) -> str:
        return self.names[0]

    @property
    def multi(self) -> bool:
        return len(self.members) > 1

    def get(self, name: str | None) -> Workspace:
        name = name or self.primary
        if name not in self.members:
            raise WorkspaceError(f"there is no BB workspace called {name!r} on this companion "
                                 f"(known: {', '.join(self.names)})")
        return self.members[name]

    # --- reads -----------------------------------------------------------------------------------

    def run(self, argv: list[str], workspace: str | None = None, **kw):
        return self.get(workspace).run(argv, **kw)

    def thread_output(self, ref: str) -> str | None:
        """`bb thread output` for an inbox ref ("thr_…" in the primary workspace, "work:thr_…" elsewhere)."""
        ws, tid = split_ref(ref)
        return (self.run(["thread", "output", tid], workspace=ws) or {}).get("output")

    def ref_for(self, workspace: str | None, tid: str) -> str:
        """Inbox ref of a thread: bare id in the primary workspace (as before), "<ws>:<id>" in the others."""
        return tid if not workspace or workspace == self.primary else f"{workspace}:{tid}"

    def warm(self) -> None:
        for w in self.members.values():
            w.warm()

    def index(self, max_age_s: float | None = None) -> Index:
        if not self.multi:
            idx = self.primary_member().index(max_age_s)
            with self._lock:
                if not (self._merged and self._merged.stamp == idx.stamp):
                    self._merged = replace(idx, refs=AllRefs.of(idx.refs))
                return self._merged
        results: dict[str, Index | Exception] = {}
        with ThreadPoolExecutor(max_workers=len(self.members)) as pool:
            futs = {n: pool.submit(w.index, max_age_s) for n, w in self.members.items()}
            for n, f in futs.items():
                try:
                    results[n] = f.result()
                except Exception as e:  # noqa: BLE001 - one workspace down must not hide the others
                    results[n] = e
        if all(isinstance(r, Exception) for r in results.values()):
            raise WorkspaceError("; ".join(f"{n}: {r}" for n, r in results.items()))
        stamp = tuple((n, r.taken_at if isinstance(r, Index) else str(r)) for n, r in results.items())
        with self._lock:
            if self._merged and self._merged.stamp == stamp:
                return self._merged
            self._merged = merged = self._merge(results, stamp)
            return merged

    def primary_member(self) -> Workspace:
        return self.members[self.primary]

    def _merge(self, results: dict, stamp: tuple) -> Index:
        now = local_now()
        refs = AllRefs({})
        lines = [f"BB WORKSPACE SNAPSHOT taken {now:%Y-%m-%d %H:%M} (owner's local time, {now:%Z}). Ages are "
                 f"relative to each section's snapshot time. The owner has {len(results)} BB workspaces: "
                 f"{', '.join(results)}. They are separate: every project, thread and task line starts with its "
                 "workspace label, and an id, project name or task prefix belongs to that workspace only (if the "
                 "same one appears in two workspaces, write it as \"work:ID\" in need_details)."]
        errors, threads, tasks, unavailable = [], [], [], {}
        for n, r in results.items():
            if isinstance(r, Exception):
                unavailable[n] = str(r)
                refs.workspaces[n] = WorkspaceRefs(name=n, ok=False)
                lines += ["", f"=== [{n}] workspace: NOT AVAILABLE right now ({r}). No evidence from it this turn — "
                              "say so if he asks about it; never answer from the other workspace instead. ==="]
                errors.append(f"{n}: {r}")
                continue
            refs.workspaces[n] = r.refs
            lines += ["", f"=== [{n}] workspace (snapshot taken {r.taken_at:%H:%M}) ===", r.body]
            errors += [f"{n}: {e}" for e in r.errors]
            threads += r.threads
            tasks += r.tasks
        taken = max((r.taken_at for r in results.values() if isinstance(r, Index)), default=now)
        return Index(taken, "\n".join(lines), refs, errors, threads=threads, tasks=tasks, unavailable=unavailable,
                     stamp=stamp, body="\n".join(lines[1:]))

    # --- details ---------------------------------------------------------------------------------

    def _owners(self, refs: AllRefs, explicit: str | None, has) -> list[str] | None:
        """Workspaces to ask for one id: the named one; else every workspace that has it; else the ones
        that could not be read (it may be there). None: known to be in none of them."""
        if explicit:
            return [explicit]
        owners = [n for n in self.names if has(refs.get(n))]
        if owners:
            return owners
        unknown = [n for n in self.names if not refs.get(n).ok]
        if unknown:
            return unknown
        return None if self.multi else [self.primary]

    def details(self, need: dict, idx: Index) -> str:
        """Fetch what Codex asked for, in parallel, within DETAILS_DEADLINE_S, each from the workspace that owns
        it. Unknown ids are reported, not guessed."""
        refs = AllRefs.of(idx.refs)
        jobs: list[tuple[str, callable]] = []

        def label(n):
            return f" [{n}]" if self.multi else ""

        def add(title, owners, fn, missing):
            if owners is None:
                jobs.append((title, lambda: missing))
                return
            for n in owners:
                if n not in self.members:
                    jobs.append((f"{title}{label(n)}", lambda n=n: f"(there is no workspace called {n!r})"))
                else:
                    jobs.append((f"{title}{label(n)}", lambda n=n: fn(self.members[n], refs.get(n))))

        for raw in [str(x).strip() for x in need.get("threads", [])][:DETAIL_CAPS["threads"]]:
            ws, tid = split_ref(raw)
            add(f"Thread {tid}", self._owners(refs, ws, lambda r, tid=tid: tid in r.threads),
                lambda w, r, tid=tid: w.thread_details(tid, r), "(no such thread in any workspace)")
        for raw in [str(x).strip() for x in need.get("tasks", [])][:DETAIL_CAPS["tasks"]]:
            ws, key = split_ref(raw)
            key = key.upper()
            prefix = key.split("-", 1)[0]
            add(f"Task {key}", self._owners(refs, ws, lambda r, prefix=prefix: prefix in r.trackers),
                lambda w, r, key=key: w.task_details(key), "(no task project with that prefix in any workspace)")
        for m in need.get("memory", [])[:DETAIL_CAPS["memory"]]:
            q = (m.get("query") if isinstance(m, dict) else str(m)) or ""
            proj = (m.get("project") if isinstance(m, dict) else "") or ""
            ws = ((m.get("workspace") if isinstance(m, dict) else "") or "").strip().lower() or None
            if ws is None and ":" in proj:
                ws, proj = split_ref(proj)
            if proj:
                owners = self._owners(refs, ws, lambda r, proj=proj: r.has_project(proj))
            else:
                owners = [ws] if ws else self.names
            add(f"Memory search {q!r}" + (f" in {proj}" if proj else ""), owners,
                lambda w, r, q=q, proj=proj: w.memory(q, proj, r), "(no project by that name in any workspace)")
        for raw in [str(x).strip() for x in need.get("projects", [])][:DETAIL_CAPS["projects"]]:
            ws, ref = split_ref(raw)
            add(f"Git history of project {ref}", self._owners(refs, ws, lambda r, ref=ref: r.has_project(ref)),
                lambda w, r, ref=ref: w.git(ref, r), "(no project by that name in any workspace)")
        if not jobs:
            return ""
        now = local_now()
        out = [f"DETAILS fetched {now:%Y-%m-%d %H:%M} (owner's local time)"
               + (", each from the workspace in brackets:" if self.multi else ":")]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = [(title, pool.submit(fn)) for title, fn in jobs]
            wait([f for _, f in futs], timeout=DETAILS_DEADLINE_S)
            for title, fut in futs:
                if not fut.done():
                    body = "(not fetched: took too long)"
                else:
                    try:
                        body = fut.result()
                    except Exception as e:  # noqa: BLE001
                        body = f"(could not be read: {e})"
                out += ["", f"## {title}", body]
        return "\n".join(out)

    # --- actions ---------------------------------------------------------------------------------

    def execute(self, cmd: Command, workspace: str | None = None):
        """Run a confirmed action in exactly that workspace (never another one)."""
        result = self.get(workspace).execute(cmd)
        with self._lock:
            self._merged = None
        return result
