"""Talk as BB workspace assistant (docs/talk.md -> "Workspace assistant"): fake `bb` and fake `codex` on PATH."""

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

import pytest

from orbit_notes import actions
from orbit_notes.actions import ActionLog, Invalid, WorkspaceRefs, command, validate
from orbit_notes.talk import CONFIRM_ASK, Talker
from orbit_notes.workspace import Workspace
from test_companion import add_talk, cfg, client, fake_tts, store, ui_client  # noqa: F401 (fixtures)

FAKE_BB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
stdin = sys.stdin.read()
data = json.load(open(os.environ["FAKE_BB_DATA"]))
with open(os.environ["FAKE_BB_LOG"], "a") as f:
    f.write(json.dumps({"args": args, "stdin": stdin, "cwd": os.getcwd(),
                        "env": {k: v for k, v in os.environ.items() if k.startswith("BB_")}}) + "\n")
def out(x):
    print(x if isinstance(x, str) else json.dumps(x)); sys.exit(0)
def fail(msg):
    print(json.dumps({"ok": False, "error": {"code": "x", "message": msg}})); sys.exit(1)
if ("--json" in args) == (args[:2] == ["thread", "log"]):  # JSON everywhere except the readable thread log
    print("------ a table, not JSON ------"); sys.exit(0)
fail_on = os.environ.get("FAKE_BB_FAIL")
if fail_on and " ".join(args).startswith(fail_on):
    fail("simulated failure")
a = args
if a[:2] == ["project", "list"]: out(data["projects"])
if a[:2] == ["thread", "list"]: out(data["threads"])
if a[:3] == ["tasks", "project", "list"]: out({"projects": data["trackers"]})
if a[:2] == ["tasks", "list"]:
    prefix = [x for x in a if x.startswith("--project=")][0].split("=", 1)[1]
    out({"tasks": [t for t in data["tasks"] if t["key"].startswith(prefix + "-")]})
if a[:2] == ["thread", "log"]: out(data["logs"].get(a[2]) or fail("no such thread"))
if a[:2] == ["tasks", "show"]: out(data["task_details"].get(a[2]) or fail("no such task"))
if a[:2] == ["memory", "search"]:
    pid = os.environ.get("BB_PROJECT_ID")
    out({"ok": True, "memories": [m for m in data["memories"] if m.get("projectId") in (None, pid)]})
if a[:2] == ["thread", "spawn"]:
    out({"id": "thr_new1234567", "status": "active", "title": [x for x in a if x.startswith("--title=")][0][8:]})
if a[:2] == ["thread", "tell"]: out({"threadId": a[2], "delivered": True})
if a[:2] == ["tasks", "create"]: out({"task": {"key": "ORB-9"}})
if a[:2] in (["tasks", "update"], ["tasks", "comment"]): out({"task": {"key": a[2]}})
if a[:2] == ["memory", "add"]: out({"ok": True, "memory": {"name": "x"}})
fail("unknown command " + " ".join(a))
'''

FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
log = os.environ["FAKE_CODEX_LOG"]
calls = [json.loads(l) for l in open(log)] if os.path.exists(log) else []
open(log, "a").write(json.dumps({"args": args, "prompt": prompt}) + "\n")
answers = json.load(open(os.environ["FAKE_CODEX_ANSWERS"]))
answer = answers[len(calls)]
resume = args[1] == "resume"
sid = args[-2] if resume else "sess-%d" % len(calls)
print(json.dumps({"type": "thread.started", "thread_id": sid}))
assert json.load(open(args[args.index("--output-schema") + 1]))["required"][0] == "reply"
open(args[args.index("-o") + 1], "w").write(json.dumps(answer))
'''


def answer(reply="Okay.", *, need=None, confirmation="none", kind="none", params=None, summary=""):
    return {"reply": reply,
            "need_details": {"threads": [], "tasks": [], "memory": [], "projects": [], **(need or {})},
            "confirmation": confirmation,
            "proposed_action": {"kind": kind, "summary": summary, "params_json": json.dumps(params or {})}}


def ms_ago(**kw):
    return int((datetime.now(timezone.utc) - timedelta(**kw)).timestamp() * 1000)


def iso_ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat().replace("+00:00", "Z")


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "orbit-launcher"
    path.mkdir()
    git = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "a.txt").write_text("x")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "watch: notification fix"], check=True)
    return path


@pytest.fixture
def bb(tmp_path, monkeypatch, repo):
    bin_dir = tmp_path / "bbbin"
    bin_dir.mkdir()
    (bin_dir / "bb").write_text(FAKE_BB)
    (bin_dir / "bb").chmod(0o755)
    data = {
        "projects": [
            {"id": "proj_orbit", "name": "orbit-launcher", "sources": [{"path": str(repo), "isDefault": True}]},
            {"id": "proj_rig", "name": "rig-smith-2d", "sources": [{"path": "/nonexistent/rig", "isDefault": True}]},
        ],
        "threads": [
            {"id": "thr_notif00001", "projectId": "proj_orbit", "title": "Fix notification sound", "status": "idle",
             "updatedAt": ms_ago(hours=3), "visibility": "visible", "archivedAt": None},
            {"id": "thr_talk000001", "projectId": "proj_orbit", "title": "Talk mode", "status": "active",
             "updatedAt": ms_ago(minutes=5), "visibility": "visible", "archivedAt": None,
             "hasPendingInteraction": True},
            {"id": "thr_old0000001", "projectId": "proj_rig", "title": "Archived thing", "status": "idle",
             "updatedAt": ms_ago(days=2), "visibility": "visible", "archivedAt": ms_ago(days=1)},
        ],
        "trackers": [{"prefix": "ORB", "name": "Orbit", "linkedBbProjectId": "proj_orbit"}],
        "tasks": [
            {"key": "ORB-1", "title": "Pair over QR", "status": "in_progress", "priority": "high",
             "updatedAt": iso_ago(hours=20)},
            {"key": "ORB-2", "title": "Diary page", "status": "done", "priority": "medium", "updatedAt": iso_ago(days=1)},
            {"key": "ORB-3", "title": "Ancient", "status": "done", "priority": "low", "updatedAt": iso_ago(days=30)},
        ],
        "logs": {"thr_notif00001": "── User ──\nFix the notification sound\n── Assistant ──\nFixed; 12 tests pass."},
        "task_details": {"ORB-1": {"task": {"title": "Pair over QR", "status": "in_progress", "priority": "high",
                                            "createdAt": iso_ago(days=3), "updatedAt": iso_ago(hours=20),
                                            "description": "Scan the QR on the watch."},
                                   "subtasks": [{"key": "ORB-4", "title": "Camera", "status": "done",
                                                 "updatedAt": iso_ago(hours=21)}],
                                   "comments": [{"createdAt": iso_ago(hours=19), "authorName": "agent",
                                                 "body": "Camera works, scanning not yet."}]}},
        "memories": [{"id": "m1", "name": "watch-density", "projectId": "proj_orbit", "kind": "fact",
                      "summary": "Vendor density 0.75", "updatedAt": iso_ago(days=4)}],
    }
    (tmp_path / "bb.json").write_text(json.dumps(data))
    log = tmp_path / "bb-calls.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_BB_DATA", str(tmp_path / "bb.json"))
    monkeypatch.setenv("FAKE_BB_LOG", str(log))
    monkeypatch.setenv("BB_THREAD_ID", "thr_mine")  # must never reach bb (would make spawns our children)
    monkeypatch.setenv("BB_PROJECT_ID", "proj_mine")
    return lambda: [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def codex(tmp_path, monkeypatch):
    bin_dir = tmp_path / "codexbin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text(FAKE_CODEX)
    (bin_dir / "codex").chmod(0o755)
    log, answers = tmp_path / "codex-calls.jsonl", tmp_path / "answers.json"
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    monkeypatch.setenv("FAKE_CODEX_ANSWERS", str(answers))

    class Codex:
        def script(self, *items):
            answers.write_text(json.dumps(list(items)))

        def calls(self):
            return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return Codex()


@pytest.fixture
def talk(cfg, store, client, bb, codex):
    """Run talk turns: returns (reply text, tts call) per turn."""
    cfg.talk_workspace = "bb"
    tts_calls = []
    talker = Talker(cfg, store, tts=fake_tts([], tts_calls))

    def turn(text, minute=[0]):  # noqa: B006 - a counter across turns
        minute[0] += 1
        started = (datetime.now().astimezone().replace(microsecond=0) - timedelta(minutes=60 - minute[0])).isoformat()
        note = add_talk(client, store, started, text)
        assert talker.run(note), store.get(note.id).talk_error
        txt = store.audio_path(note).with_name(f"{note.stem}.reply.txt").read_text().strip()
        return txt, tts_calls[-1]

    turn.talker = talker
    return turn


def writes(calls):
    return [c for c in calls if c["args"][:2] in (["thread", "spawn"], ["thread", "tell"], ["tasks", "create"],
                                                   ["tasks", "update"], ["tasks", "comment"], ["memory", "add"])]


# --- index -----------------------------------------------------------------------------------------


def test_index_is_compact_timestamped_and_cached(bb):
    ws = Workspace()
    idx = ws.index()
    text = idx.text
    assert text.startswith("BB WORKSPACE SNAPSHOT taken ")
    assert "- proj_orbit orbit-launcher — " in text
    assert 'thr_notif00001 [orbit-launcher] "Fix notification sound" — idle, updated 3 h ago' in text
    assert "waiting for the owner's input" in text  # the Talk thread has a pending interaction
    assert "Archived thing" not in text
    assert "status 'idle' only means no agent is running now" in text
    assert 'ORB-1 "Pair over QR" — in_progress, high, updated 20 h ago' in text
    assert 'ORB-2 "Diary page" — done' in text and "Ancient" not in text  # done > 7 days ago: dropped
    assert idx.refs.ok and set(idx.refs.threads) == {"thr_notif00001", "thr_talk000001"}
    n = len(bb())
    assert n == 4  # projects, threads, tracker projects, tasks per tracker
    assert ws.index() is idx and len(bb()) == n  # cached (3 min)
    assert ws.index(max_age_s=0) is not idx and len(bb()) == 2 * n
    for call in bb():
        assert "BB_THREAD_ID" not in call["env"] and "BB_PROJECT_ID" not in call["env"]


def test_index_reports_unavailable_parts(bb, monkeypatch):
    monkeypatch.setenv("FAKE_BB_FAIL", "thread list")
    idx = Workspace().index()
    assert not idx.refs.ok
    assert "NOT AVAILABLE in this snapshot (treat as no evidence): threads: simulated failure" in idx.text


def test_details_threads_tasks_memory_git(bb):
    ws = Workspace()
    idx = ws.index()
    text = ws.details({"threads": ["thr_notif00001", "thr_nope000001"], "tasks": ["orb-1"],
                       "memory": [{"query": "density", "project": ""}], "projects": ["orbit-launcher"]}, idx)
    assert text.startswith("DETAILS fetched ")
    assert '"Fix notification sound" in orbit-launcher; status idle, last update 3 h ago' in text
    assert "Fixed; 12 tests pass." in text
    assert "## Thread thr_nope000001\n(no such thread in the workspace)" in text
    assert "Scan the QR on the watch." in text and "subtask ORB-4" in text and "Camera works, scanning not yet." in text
    assert "watch-density (orbit-launcher, fact, saved 4 days ago (" in text and "Vendor density 0.75" in text
    assert "watch: notification fix" in text and "checked-out branch main" in text
    searches = [c for c in bb() if c["args"][:2] == ["memory", "search"]]
    assert {c["env"].get("BB_PROJECT_ID") for c in searches} == {"proj_orbit", "proj_rig"}  # every project


# --- turns -----------------------------------------------------------------------------------------


def test_progress_question_uses_one_detail_round(talk, codex, bb):
    codex.script(answer("Let me check.", need={"threads": ["thr_notif00001"], "projects": ["orbit-launcher"]}),
                 answer("Done: the notification fix ended 3 hours ago with tests passing. Uncertain: release build.",
                        need={"tasks": ["ORB-1"]}))  # a second request for details is ignored
    reply, _ = talk("How is the orbit launcher project going?")
    assert reply.startswith("Done: the notification fix ended 3 hours ago")
    calls = codex.calls()
    assert len(calls) == 2
    first, second = calls
    assert "BB WORKSPACE SNAPSHOT taken" in first["prompt"] and "How is the orbit launcher" in first["prompt"]
    assert second["args"][:3] == ["exec", "resume", "--json"] and second["args"][-2:] == ["sess-0", "-"]
    assert "DETAILS fetched" in second["prompt"] and "Fixed; 12 tests pass." in second["prompt"]
    assert "watch: notification fix" in second["prompt"]
    assert not [c for c in bb() if c["args"][:2] == ["tasks", "show"]]  # no second detail round
    assert not writes(bb())


def test_propose_confirm_execute_thread_spawn(talk, codex, bb, cfg):
    params = {"project": "orbit-launcher", "prompt": "-Fix the watch face clock drift.", "title": "Clock drift"}
    codex.script(answer("You want a new thread in orbit-launcher to fix the clock drift.", kind="thread_spawn",
                        params=params, summary="Start a thread in orbit-launcher to fix the clock drift"),
                 answer("Okay.", confirmation="confirm"))
    reply, _ = talk("Start a thread to fix the clock drift on the watch face")
    assert reply == f"You want a new thread in orbit-launcher to fix the clock drift. {CONFIRM_ASK}"
    log = ActionLog(cfg.data_dir)
    pending = log.pending()
    assert pending.kind == "thread_spawn" and pending.params["project"] == "proj_orbit"
    assert not writes(bb())  # nothing happens before the confirmation

    reply, _ = talk("Yes, do it")
    w = writes(bb())
    assert len(w) == 1
    assert w[0]["args"] == ["thread", "spawn", "--project=proj_orbit", "--prompt-file", "-", "--title=Clock drift",
                            "--json"]
    assert w[0]["stdin"] == "-Fix the watch face clock drift."
    assert "BB_THREAD_ID" not in w[0]["env"] and "BB_PROJECT_ID" not in w[0]["env"]
    assert reply == "Okay. Done: I started the thread “Clock drift” in orbit-launcher, id thr_new1234567."
    assert log.pending() is None
    assert [e["event"] for e in reversed(log.entries())] == ["proposed", "confirmed", "executed"]
    assert log.entries()[0]["argv"][:3] == ["bb", "thread", "spawn"]
    # The action log goes back to Codex next turn, so it knows what really happened.
    codex.script(*[answer()] * 3)
    talk("thanks")
    assert "executed thread_spawn" in codex.calls()[-1]["prompt"] and "thr_new1234567" in codex.calls()[-1]["prompt"]


def test_orbit_project_spawn_becomes_child_of_orchestrator(talk, codex, bb, cfg):
    """Raiz's rule: Orbit work goes through the orchestrator thread, so Orbit spawns are its children."""
    cfg.orbit_project, cfg.orbit_orchestrator_thread = "proj_orbit", "thr_orch"
    params = {"project": "orbit-launcher", "prompt": "Fix the clock drift.", "title": "Clock drift"}
    codex.script(answer("New thread for the clock drift?", kind="thread_spawn", params=params, summary="Spawn"),
                 answer("Okay.", confirmation="confirm"))
    talk("Start a thread to fix the clock drift")
    talk("Yes")
    w = writes(bb())
    assert w[0]["args"] == ["thread", "spawn", "--project=proj_orbit", "--prompt-file", "-", "--title=Clock drift",
                            "--parent-thread=thr_orch", "--new-environment=worktree", "--base-branch=main", "--json"]
    assert w[0]["stdin"].startswith("Fix the clock drift.") and "thr_orch" in w[0]["stdin"]
    assert "don't merge into main" in w[0]["stdin"]
    # The pending action itself never carries a parent (Codex can't choose one).
    assert all("parent" not in e.get("params", {}) for e in ActionLog(cfg.data_dir).entries())


def test_failed_execution_is_reported(talk, codex, bb, cfg, monkeypatch):
    codex.script(answer("Comment on ORB-1?", kind="task_comment", params={"key": "ORB-1", "body": "QR works"},
                        summary="Comment on ORB-1"),
                 answer("Okay.", confirmation="confirm"))
    talk("Comment on ORB-1 that QR works")
    monkeypatch.setenv("FAKE_BB_FAIL", "tasks comment")
    reply, _ = talk("yes")
    assert reply == "Okay. I tried, but it did not work: simulated failure."
    assert ActionLog(cfg.data_dir).entries()[0]["event"] == "failed"


def test_cancel(talk, codex, bb, cfg):
    codex.script(answer("Mark ORB-1 done?", kind="task_update", params={"key": "ORB-1", "status": "done"},
                        summary="Mark ORB-1 as done"),
                 answer("Okay, I cancelled it.", confirmation="cancel"))
    talk("Mark ORB-1 done")
    reply, _ = talk("No, cancel that")
    assert reply == "Okay, I cancelled it."
    log = ActionLog(cfg.data_dir)
    assert log.pending() is None and log.entries()[0]["event"] == "cancelled"
    assert not writes(bb())


def test_expired_action_is_not_executed(talk, codex, bb, cfg):
    codex.script(answer("Mark ORB-1 done?", kind="task_update", params={"key": "ORB-1", "status": "done"},
                        summary="Mark ORB-1 as done"),
                 answer("Okay.", confirmation="confirm"))
    talk("Mark ORB-1 done")
    log = ActionLog(cfg.data_dir)
    p = json.loads(log.pending_path.read_text())
    p["created_at"] = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    log.pending_path.write_text(json.dumps(p))
    reply, _ = talk("yes")
    assert "expired after 10 minutes, so I did not do it: Mark ORB-1 as done" in reply
    assert "An earlier proposal expired unconfirmed" in codex.calls()[-1]["prompt"]
    assert not writes(bb()) and log.pending() is None
    assert [e["event"] for e in log.entries()[:1]] == ["expired"]


def test_confirm_without_pending_does_nothing(talk, codex, bb):
    codex.script(answer("Okay.", confirmation="confirm"))
    reply, _ = talk("yes")
    assert reply == "Okay. There was nothing waiting for confirmation, so I did not do anything."
    assert not writes(bb())


def test_new_proposal_replaces_pending(talk, codex, bb, cfg):
    codex.script(answer("Mark ORB-1 done?", kind="task_update", params={"key": "ORB-1", "status": "done"},
                        summary="Mark ORB-1 as done"),
                 answer("Set ORB-1 to review?", kind="task_update", params={"key": "ORB-1", "status": "in_review"},
                        summary="Move ORB-1 to in review"))
    talk("Mark ORB-1 done")
    reply, _ = talk("Actually put it in review")
    assert reply == "This replaces the earlier request: Mark ORB-1 as done. Set ORB-1 to review?"
    log = ActionLog(cfg.data_dir)
    assert log.pending().params == {"key": "ORB-1", "status": "in_review"}
    assert [e["event"] for e in log.entries()[:2]] == ["proposed", "replaced"]


@pytest.mark.parametrize("kind, params, reason", [
    ("shell", {"cmd": "rm -rf ~"}, "'shell' is not something I'm allowed to do"),
    ("thread_archive", {"thread_id": "thr_notif00001"}, "is not something I'm allowed to do"),
    ("thread_tell", {"thread_id": "thr_unknown001", "message": "hi"}, "can't find thread thr_unknown001"),
    ("thread_tell", {"thread_id": "rm -rf", "message": "hi"}, "is not a thread id"),
    ("task_create", {"project": "ORB", "title": "x", "assignee": "bob"}, "does not support assignee"),
    ("task_create", {"project": "NOPE", "title": "x"}, "no task project with prefix NOPE"),
    ("task_update", {"key": "ORB-1"}, "nothing to change"),
    ("task_update", {"key": "ORB-1", "status": "deleted"}, "status must be one of"),
    ("thread_spawn", {"project": "secret-project", "prompt": "x"}, "don't know a project called 'secret-project'"),
    ("memory_add", {"scope": "global", "name": "Bad Name", "summary": "s", "details": "d", "reason": "r"},
     "memory name must be lowercase"),
    ("settings", {"volume": 2}, "settings does not support volume"),
    ("settings", {"length_scale": 3}, "speaking rate must stay between 0.7 and 1.6"),
    ("settings", {"voice": "de_DE-thorsten-medium"}, "don't have the voice"),
])
def test_allowlist_rejects(talk, codex, bb, cfg, kind, params, reason):
    codex.script(answer("Sure, I will do that.", kind=kind, params=params, summary="Do the thing"))
    reply, _ = talk("do the thing")
    assert reason in reply and reply.startswith("I understood that you want me to do the thing. But I can't:")
    assert "I will do that" not in reply
    log = ActionLog(cfg.data_dir)
    assert log.pending() is None and log.entries()[0]["event"] == "rejected"
    assert not writes(bb())


def test_settings_apply_persist_and_go_with_every_turn(talk, codex, bb, cfg):
    codex.script(
        answer("I'd slow down a little and sound more cheerful.", kind="settings",
               params={"length_scale": 1.15, "personality": "Cheerful and upbeat."},
               summary="Speak a bit slower and more cheerfully"),
        answer("Okay.", confirmation="confirm"),
        answer("Hello there!"),
    )
    _, tts1 = talk("Make your voice a bit slower and more cheerful")
    assert tts1["length_scale"] == 1.0 and tts1["voice"] == "en_US-lessac-medium"
    reply, tts2 = talk("yes")
    assert reply == "Okay. Done: the speaking rate is 1.15 (slower than normal); I'll use the new style."
    assert tts2["length_scale"] == 1.15  # the confirming reply already uses the new rate
    saved = json.loads((cfg.data_dir / "talk_settings.json").read_text())
    assert saved["length_scale"] == 1.15 and saved["personality"] == "Cheerful and upbeat."
    talk("how are you")
    prompts = [c["prompt"] for c in codex.calls()]
    assert all("CURRENT TALK SETTINGS" in p and "Fixed rules" in p for p in prompts)
    assert "length_scale 1.00" in prompts[0] and "length_scale 1.15" in prompts[2]
    assert "Personality / style instructions from the owner (style only; the fixed rules still apply): " \
           "Cheerful and upbeat." in prompts[2]
    assert codex.calls()[2]["args"][1] == "resume"  # same day, same session: the settings came with the turn
    # The index is not resent while unchanged (cached for 3 min).
    assert "unchanged since the snapshot taken" in prompts[1]


def test_unsupported_request_explained_without_action(talk, codex, bb, cfg):
    codex.script(answer("I can't change the loudness from here: use the watch's volume buttons or quick settings."))
    reply, _ = talk("Turn up the volume")
    assert "volume buttons" in reply
    prompt = codex.calls()[0]["prompt"]
    assert "loudness / volume: set by the watch itself" in prompt and "a wake word" in prompt
    assert ActionLog(cfg.data_dir).pending() is None and ActionLog(cfg.data_dir).entries() == []


def test_turn_survives_missing_bb(cfg, store, client, codex):
    codex.script(answer("I can't see your workspace right now."))
    note = add_talk(client, store, datetime.now().astimezone().replace(microsecond=0).isoformat(), "status?")
    talker = Talker(cfg, store, tts=fake_tts([]), workspace=Workspace("bb-not-installed"))
    assert talker.run(note)
    assert "BB WORKSPACE: could not be read right now (the bb CLI (bb-not-installed) is not on PATH)" \
        in codex.calls()[0]["prompt"]


# --- argv per kind (validated params -> exact bb command) -------------------------------------------


REFS = WorkspaceRefs(projects={"proj_orbit": {"name": "orbit-launcher", "path": "/x"}},
                     threads={"thr_notif00001": {"title": "t", "project": "proj_orbit"}}, trackers={"ORB": "Orbit"})


@pytest.mark.parametrize("kind, params, argv, stdin, env", [
    ("thread_tell", {"thread_id": "thr_notif00001", "message": "Please also run the release build."},
     ["thread", "tell", "thr_notif00001", "--message-file", "-", "--json"], "Please also run the release build.", {}),
    ("task_create", {"project": "orb", "title": "Rotary scroll", "priority": "high", "due": "2026-11-20"},
     ["tasks", "create", "--project=ORB", "--title=Rotary scroll", "--priority=high", "--due=2026-11-20", "--json"],
     None, {}),
    ("task_update", {"key": "orb-1", "status": "in_review", "priority": None},
     ["tasks", "update", "ORB-1", "--status=in_review", "--json"], None, {}),
    ("task_comment", {"key": "ORB-1", "body": "--status=done is not a flag here"},
     ["tasks", "comment", "ORB-1", "--body=--status=done is not a flag here", "--author=Orbit Talk", "--json"],
     None, {}),
    ("memory_add", {"scope": "project", "project": "orbit-launcher", "name": "talk-voice", "summary": "s",
                    "details": "d", "reason": "r", "kind": "preference"},
     ["memory", "add", "--scope=project", "--name=talk-voice", "--summary=s", "--details=d", "--reason=r",
      "--kind=preference", "--json"], None, {"BB_PROJECT_ID": "proj_orbit"}),
])
def test_command_argv(kind, params, argv, stdin, env):
    cmd = command(kind, validate(kind, params, REFS))
    assert (cmd.argv, cmd.stdin, cmd.env) == (argv, stdin, env)


def test_validate_needs_workspace_for_references():
    with pytest.raises(Invalid, match="couldn't read the workspace"):
        validate("thread_spawn", {"project": "orbit-launcher", "prompt": "x"}, WorkspaceRefs(ok=False))
    assert validate("settings", {"personality": ""}, WorkspaceRefs(ok=False)) == {"personality": ""}
    assert set(actions.KINDS) == {"thread_spawn", "thread_tell", "task_create", "task_update", "task_comment",
                                  "memory_add", "settings"}


# --- web UI ----------------------------------------------------------------------------------------


def test_ui_shows_pending_audit_and_settings(talk, codex, bb, cfg, store):
    codex.script(answer("Mark ORB-1 done?", kind="task_update", params={"key": "ORB-1", "status": "done"},
                        summary="Mark ORB-1 as done"))
    talk("Mark ORB-1 done")
    t = ui_client(cfg, store).get("/ui/api/talk").json()
    assert t["pending"]["summary"] == "Mark ORB-1 as done" and t["pending"]["expired"] is False
    assert t["pending"]["expires_at"] > t["pending"]["created_at"]
    assert t["audit"][0]["event"] == "proposed"
    assert t["settings"]["voice"] == "en_US-lessac-medium" and t["settings"]["length_scale"] == 1.0
    assert "en_GB-alan-medium" in t["voices"] and t["workspace"] == "bb"
    assert ui_client(cfg, store, host="192.168.1.5").get("/ui/api/talk").status_code == 403


def test_ui_settings_save_and_apply_to_next_talk_reply(talk, codex, bb, cfg, store):
    ui = ui_client(cfg, store)
    t = ui.get("/ui/api/talk").json()
    assert t["voices"]["en_GB-alan-medium"] == {"label": "British English, male", "installed": False}
    assert "You are Orbit" in t["base_persona"] and t["limits"]["personality"] == 600
    r = ui.put("/ui/api/talk/settings", json={"voice": "en_GB-alan-medium", "length_scale": 1.2,
                                              "reply_length": "short", "personality": "  Dry humour,\n brief.  "})
    assert r.status_code == 200, r.text
    assert r.json()["voice"] == "en_GB-alan-medium" and r.json()["personality"] == "Dry humour, brief."
    saved = json.loads((cfg.data_dir / "talk_settings.json").read_text())
    assert saved["length_scale"] == 1.2 and saved["reply_length"] == "short"
    assert ui.get("/ui/api/talk").json()["settings"]["voice"] == "en_GB-alan-medium"
    entry = ActionLog(cfg.data_dir).entries()[0]
    assert entry["event"] == "changed" and entry["params"]["voice"] == "en_GB-alan-medium"

    codex.script(answer("Hello."), answer("Hi again."))
    _, tts1 = talk("hello")  # the very next reply uses the saved settings
    assert tts1["voice"] == "en_GB-alan-medium" and tts1["length_scale"] == 1.2
    prompt = codex.calls()[0]["prompt"]
    assert "Personality / style instructions from the owner (style only; the fixed rules still apply): " \
           "Dry humour, brief." in prompt and "Reply length now: 1 to 2 short sentences" in prompt

    # Reset to the base persona; the other settings stay.
    assert ui.put("/ui/api/talk/settings", json={"personality": ""}).json()["personality"] == ""
    talk("and now?")
    prompt = codex.calls()[1]["prompt"]
    assert "Personality / style instructions" not in prompt and "(none: base persona only)" in prompt
    assert "en_GB-alan-medium" in prompt


@pytest.mark.parametrize("body, error", [
    ({"voice": "de_DE-thorsten-medium"}, "don't have the voice"),
    ({"length_scale": 2}, "between 0.7 and 1.6"),
    ({"reply_length": "huge"}, "reply_length must be one of"),
    ({"personality": "x" * 601}, "at most 600"),
    ({"volume": 3}, "unknown setting(s): volume"),
    ({}, "no setting to change"),
])
def test_ui_settings_rejects_bad_values(cfg, store, body, error):
    r = ui_client(cfg, store).put("/ui/api/talk/settings", json=body)
    assert r.status_code == 422 and error in r.json()["detail"]
    assert not (cfg.data_dir / "talk_settings.json").exists()


def test_ui_settings_lan_forbidden(cfg, store):
    lan = ui_client(cfg, store, host="192.168.1.5")
    assert lan.put("/ui/api/talk/settings", json={"personality": ""}).status_code == 403
    assert lan.post("/ui/api/talk/preview", json={}).status_code == 403


def test_ui_lists_any_installed_english_voice(cfg, store):
    voices = cfg.data_dir / "voices"
    voices.mkdir(parents=True)
    for name in ("en_GB-northern_english_male-medium", "en_US-lessac-medium", "de_DE-thorsten-medium"):
        (voices / f"{name}.onnx").write_bytes(b"x")
        (voices / f"{name}.onnx.json").write_text("{}")
    (voices / "en_US-half-medium.onnx").write_bytes(b"x")  # no config: a cut download, not installed
    ui = ui_client(cfg, store)
    v = ui.get("/ui/api/talk").json()["voices"]
    assert v["en_GB-northern_english_male-medium"] == {"label": "installed voice", "installed": True}
    assert v["en_US-lessac-medium"]["installed"] and not v["en_US-amy-medium"]["installed"]
    assert "de_DE-thorsten-medium" not in v and "en_US-half-medium" not in v
    assert ui.put("/ui/api/talk/settings", json={"voice": "en_GB-northern_english_male-medium"}).status_code == 200
    with pytest.raises(Invalid):  # the spoken flow stays on the curated list
        validate("settings", {"voice": "en_GB-northern_english_male-medium"}, REFS)


def test_ui_voice_preview(cfg, store):
    calls = []

    def tts(text, out, voice=None, length_scale=None, strict=False):
        calls.append({"text": text, "voice": voice, "length_scale": length_scale, "strict": strict})
        if voice == "en_US-joe-medium":
            raise RuntimeError("download failed")
        out.write_bytes(b"RIFF preview")

    ui = ui_client(cfg, store, tts=tts)
    r = ui.post("/ui/api/talk/preview", json={"voice": "en_GB-cori-high", "length_scale": 0.9})
    assert r.status_code == 200 and r.content == b"RIFF preview" and r.headers["content-type"] == "audio/wav"
    assert calls[-1] == {"text": "Hi, I'm Orbit. This is how I'll sound when I answer your voice notes.",
                         "voice": "en_GB-cori-high", "length_scale": 0.9, "strict": True}
    ui.post("/ui/api/talk/preview", json={})  # nothing given: the current settings
    assert calls[-1]["voice"] == "en_US-lessac-medium" and calls[-1]["length_scale"] == 1.0
    assert not (cfg.data_dir / "talk_settings.json").exists()  # a preview never saves
    assert ui.post("/ui/api/talk/preview", json={"voice": "fr_FR-x-medium"}).status_code == 422
    r = ui.post("/ui/api/talk/preview", json={"voice": "en_US-joe-medium"})
    assert r.status_code == 502 and "download failed" in r.json()["detail"]


@pytest.mark.parametrize("reply, asks", [
    ("I'll start a thread for it. Say yes to confirm or no to cancel.", True),  # seen live: no "?" at the end
    ("I'll mark ORB-1 done. Shall I?", True),
    ("I'll mark ORB-1 done.", False),
    ("Yes, the thread is idle. I'll start a new one.", False),  # "yes" earlier in the reply doesn't count
])
def test_confirmation_question_added_once(reply, asks):
    from orbit_notes.talk import asks_confirmation

    assert asks_confirmation(reply) is asks
