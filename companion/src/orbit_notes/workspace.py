"""BB workspace access for Talk: a compact index, on-demand evidence, and executing confirmed actions.

Everything goes through the `bb` CLI (`--json`) as the owner. Codex never calls bb: it reads the text
built here and may ask for details (one round per turn). Every evidence line carries its time so a
reply can say how recent it is. See docs/talk.md -> "Workspace assistant".
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .actions import Command, WorkspaceRefs

INDEX_TTL_S = 180
CALL_TIMEOUT_S = 25
DETAILS_DEADLINE_S = 45
MAX_THREADS_IN_INDEX = 30
MAX_OPEN_TASKS = 40
MAX_DONE_TASKS = 15
DONE_WINDOW = timedelta(days=7)
DETAIL_CAPS = {"threads": 4, "tasks": 4, "memory": 3, "projects": 2}
# A thread's own context must not leak into commands run for the owner (spawn parent, project scope...).
SCRUB_ENV = ("BB_PROJECT_ID", "BB_THREAD_ID", "BB_ENVIRONMENT_ID")


class WorkspaceError(RuntimeError):
    pass


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
    threads: list[dict] = field(default_factory=list)  # live threads as bb returned them (briefing)
    tasks: list[dict] = field(default_factory=list)  # every task of every task project (briefing)


class Workspace:
    def __init__(self, exe: str = "bb", ttl_s: int = INDEX_TTL_S):
        self.exe_name, self.ttl_s = exe, ttl_s
        self._index: Index | None = None
        self._lock = threading.Lock()

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
        full_env = {k: v for k, v in os.environ.items() if k not in SCRUB_ENV}
        full_env.update(env or {})
        try:
            feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
            r = subprocess.run([self.exe(), *argv], capture_output=True, text=True, env=full_env,
                               timeout=timeout, cwd=cwd or "/", **feed)
        except subprocess.TimeoutExpired:
            raise WorkspaceError(f"bb {' '.join(argv[:2])} timed out after {timeout:.0f}s") from None
        if not as_json:
            if r.returncode != 0:
                raise WorkspaceError(f"bb {' '.join(argv[:2])} failed: {(r.stderr or r.stdout).strip()[-300:]}")
            return r.stdout
        try:
            data = json.loads(r.stdout) if r.stdout.strip() else None
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("ok") is False:
            err = data.get("error") or {}
            raise WorkspaceError(err.get("message") or err.get("code") or "bb reported an error")
        if r.returncode != 0 or data is None:
            raise WorkspaceError(f"bb {' '.join(argv[:2])} failed ({r.returncode}): "
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
        threading.Thread(target=lambda: self._safe_index(), name="bb-index", daemon=True).start()

    def _safe_index(self) -> None:
        try:
            self.index()
        except Exception as e:  # noqa: BLE001 - best effort; the turn rebuilds it and reports errors
            print(f"talk: workspace index failed: {e}")

    def _build_index(self) -> Index:
        t0 = time.monotonic()
        now = local_now()
        self.exe()  # no bb at all: one clear error instead of a snapshot made only of errors
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
        refs.ok = not errors
        lines = [f"BB WORKSPACE SNAPSHOT taken {now:%Y-%m-%d %H:%M} (owner's local time, {now:%Z}). "
                 "Ages are relative to that time."]
        names = {}
        lines.append("Projects (bb project id, name, repo path):")
        for p in projects:
            path = next((s.get("path") for s in p.get("sources", []) if s.get("isDefault")), None) or \
                   next((s.get("path") for s in p.get("sources", [])), None)
            names[p["id"]] = p["name"]
            refs.projects[p["id"]] = {"name": p["name"], "path": path}
            lines.append(f"- {p['id']} {p['name']} — {path or 'no local path'}")

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
            lines.append(f"- {t['id']} [{names.get(t.get('projectId'), t.get('projectId'))}] \"{title}\" — "
                         f"{t.get('status')}, updated {ago(parse_time(t.get('updatedAt')), now)}"
                         + (f"; {'; '.join(extra)}" if extra else ""))

        for tp in trackers:
            refs.trackers[tp["prefix"]] = tp["name"]
        if trackers:
            lines.append("Task projects (prefix, name, linked bb project): " + "; ".join(
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
            return (f"- {t['key']} \"{t.get('title', '').strip()}\" — {', '.join(b for b in bits if b)}, "
                    f"updated {ago(parse_time(t.get('updatedAt')), now)}")

        lines.append(f"Open tasks ({min(len(open_tasks), MAX_OPEN_TASKS)} most recently updated of {len(open_tasks)}):")
        lines += [task_line(t) for t in open_tasks[:MAX_OPEN_TASKS]] or ["- (none)"]
        lines.append("Tasks finished or canceled in the last 7 days:")
        lines += [task_line(t) for t in done[:MAX_DONE_TASKS]] or ["- (none)"]
        if errors:
            lines.append("NOT AVAILABLE in this snapshot (treat as no evidence): " + "; ".join(errors))
        print(f"talk: workspace index {time.monotonic() - t0:.1f}s ({len(live)} threads, {len(tasks)} tasks"
              + (f", errors: {errors}" if errors else "") + ")")
        return Index(now, "\n".join(lines), refs, errors, threads=live, tasks=tasks)

    # --- details (one round per turn) ------------------------------------------------------------

    def details(self, need: dict, idx: Index) -> str:
        """Fetch what Codex asked for, in parallel, within DETAILS_DEADLINE_S. Unknown ids are reported, not guessed."""
        jobs: list[tuple[str, callable]] = []
        for tid in [str(x).strip() for x in need.get("threads", [])][:DETAIL_CAPS["threads"]]:
            jobs.append((f"Thread {tid}", lambda tid=tid: self._thread_details(tid, idx)))
        for key in [str(x).strip().upper() for x in need.get("tasks", [])][:DETAIL_CAPS["tasks"]]:
            jobs.append((f"Task {key}", lambda key=key: self._task_details(key, idx)))
        for m in need.get("memory", [])[:DETAIL_CAPS["memory"]]:
            q = (m.get("query") if isinstance(m, dict) else str(m)) or ""
            proj = (m.get("project") if isinstance(m, dict) else "") or ""
            jobs.append((f"Memory search {q!r}" + (f" in {proj}" if proj else ""),
                         lambda q=q, proj=proj: self._memory(q, proj, idx)))
        for ref in [str(x).strip() for x in need.get("projects", [])][:DETAIL_CAPS["projects"]]:
            jobs.append((f"Git history of project {ref}", lambda ref=ref: self._git(ref, idx)))
        if not jobs:
            return ""
        now = local_now()
        out = [f"DETAILS fetched {now:%Y-%m-%d %H:%M} (owner's local time):"]
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

    def _thread_details(self, tid: str, idx: Index) -> str:
        info = idx.refs.threads.get(tid)
        if not info and idx.refs.ok:
            return "(no such thread in the workspace)"
        now = local_now()
        head = ""
        if info:
            proj = idx.refs.projects.get(info["project"], {}).get("name", info["project"])
            head = (f"\"{info['title']}\" in {proj}; status {info['status']}, last update "
                    f"{ago(info['updated'], now)}.\n")
        log = self.run(["thread", "log", tid, "--limit=2"], as_json=False)
        return head + "Latest two exchanges (newest last):\n" + clip(log, 1200, 3800)

    def _task_details(self, key: str, idx: Index) -> str:
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

    def _memory(self, query: str, project: str, idx: Index) -> str:
        if not query.strip():
            return "(empty query)"
        if project:
            try:
                pids = [idx.refs.project_id(project)]
            except ValueError as e:
                return f"({e})"
        else:
            pids = list(idx.refs.projects) or [None]
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
            where = idx.refs.projects.get(m.get("projectId"), {}).get("name") if m.get("projectId") else "global"
            text = m.get("details") or m.get("summary") or ""
            out.append(f"- {m.get('name')} ({where}, {m.get('kind', 'fact')}, saved {ago(when, now)}): "
                       + clip(" ".join(str(text).split()), 600, 100))
        return "\n".join(out)

    def _git(self, ref: str, idx: Index) -> str:
        try:
            pid = idx.refs.project_id(ref)
        except ValueError as e:
            return f"({e})"
        path = idx.refs.projects[pid].get("path")
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
