"""Talk with several BB workspaces (docs/talk.md -> "Several workspaces"): two fake bb servers.

The fake `bb` answers from the world of the workspace named by BB_DATA_DIR, and only if BB_SERVER_URL is
that workspace's server (the real CLI silently talks to the default server when the URL is missing).
"""

import json
import os
from datetime import datetime, timedelta

import pytest

from orbit_notes import workspace as wsmod
from orbit_notes.actions import ActionLog
from orbit_notes.inbox import Inbox, ThreadsProducer
from orbit_notes.talk import CONFIRM_ASK, Talker
from orbit_notes.workspace import Workspace, Workspaces, WorkspaceSpec, configured_specs, discover
from test_companion import add_talk, cfg, client, fake_tts, store, ui_client  # noqa: F401 (fixtures)
from test_workspace import answer, codex, iso_ago, ms_ago, writes  # noqa: F401 (fixtures)

FAKE_BB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("0.49.0"); sys.exit(0)
stdin = sys.stdin.read()
with open(os.environ["FAKE_BB_LOG"], "a") as f:
    f.write(json.dumps({"args": args, "stdin": stdin,
                        "env": {k: v for k, v in os.environ.items() if k.startswith("BB_")}}) + "\n")
def out(x):
    print(x if isinstance(x, str) else json.dumps(x)); sys.exit(0)
def fail(msg, code="x"):
    print(json.dumps({"ok": False, "error": {"code": code, "message": msg}})); sys.exit(1)
worlds = json.load(open(os.environ["FAKE_BB_WORLDS"]))
w = worlds.get(os.environ.get("BB_DATA_DIR", ""))
if w is None:
    fail("no BB_DATA_DIR")
if os.environ.get("BB_SERVER_URL") != w["server"]:
    fail("talked to the wrong server: " + str(os.environ.get("BB_SERVER_URL")))
a = args
for broken in w.get("broken", []):  # version skew: the old server rejects this request
    if " ".join(a).startswith(broken):
        fail("HTTP 400: unrecognized request body (schema mismatch)")
if a[:2] == ["project", "list"]: out(w["projects"])
if a[:2] == ["thread", "list"]: out(w["threads"])
if a[:3] == ["tasks", "project", "list"]: out({"projects": w["trackers"]})
if a[:2] == ["tasks", "list"]:
    prefix = [x for x in a if x.startswith("--project=")][0].split("=", 1)[1]
    out({"tasks": [t for t in w["tasks"] if t["key"].startswith(prefix + "-")]})
if a[:2] == ["thread", "log"]: out(w["logs"].get(a[2]) or fail("thread not found", "thread_not_found"))
if a[:2] == ["thread", "output"]: out({"output": w["outputs"].get(a[2], "")})
if a[:2] == ["tasks", "show"]: out(w["task_details"].get(a[2]) or fail("task not found", "task_not_found"))
if a[:2] == ["memory", "search"]:
    pid = os.environ.get("BB_PROJECT_ID")
    out({"ok": True, "memories": [m for m in w["memories"] if m.get("projectId") in (None, pid)]})
if a[:2] == ["thread", "spawn"]: out({"id": w["new_thread"], "status": "active", "title": "New"})
if a[:2] == ["thread", "tell"]: out({"threadId": a[2], "delivered": True})
if a[:2] == ["tasks", "create"]: out({"task": {"key": w["new_task"]}})
if a[:2] in (["tasks", "update"], ["tasks", "comment"]): out({"task": {"key": a[2]}})
if a[:2] == ["memory", "add"]: out({"ok": True, "memory": {"name": "x"}})
fail("unknown command " + " ".join(a))
'''

PERSONAL_URL, WORK_URL = "http://127.0.0.1:38886", "http://127.0.0.1:39886"


def world(server, *, projects, threads, trackers, tasks, new_thread, new_task, memories=(), broken=(), logs=None,
          details=None, outputs=None):
    return {"server": server, "projects": projects, "threads": threads, "trackers": trackers, "tasks": tasks,
            "new_thread": new_thread, "new_task": new_task, "memories": list(memories), "broken": list(broken),
            "logs": logs or {}, "task_details": details or {}, "outputs": outputs or {}}


def thread(tid, pid, title, status="idle", **kw):
    return {"id": tid, "projectId": pid, "title": title, "status": status, "updatedAt": ms_ago(hours=1),
            "visibility": "visible", "archivedAt": None, **kw}


def task_detail(title):
    return {"task": {"title": title, "status": "todo", "priority": "high", "createdAt": iso_ago(days=1),
                     "updatedAt": iso_ago(hours=2)}, "subtasks": [], "comments": []}


@pytest.fixture
def two(tmp_path, monkeypatch, no_real_bb_workspaces):
    """~/.bb (personal, bb 0.49) and ~/.bb-work (work, bb 0.44) with their own runtime files and data."""
    home = no_real_bb_workspaces
    dirs = {"personal": home / ".bb", "work": home / ".bb-work"}
    for name, url, version in (("personal", PERSONAL_URL, "0.49.0"), ("work", WORK_URL, "0.44.0")):
        dirs[name].mkdir()
        (dirs[name] / "bb.db").write_bytes(b"")
        (dirs[name] / "bb-app-runtime.json").write_text(json.dumps(
            {"pid": os.getpid(), "serverUrl": url, "version": version, "surface": "desktop"}))
    (home / ".bb-file-manager").mkdir()  # not a bb data dir (no runtime file)
    worlds = {
        str(dirs["personal"]): world(
            PERSONAL_URL,
            projects=[{"id": "proj_orbit", "name": "orbit-launcher", "sources": [{"path": "/nope", "isDefault": True}]}],
            threads=[thread("thr_orbit00001", "proj_orbit", "Talk workspaces"),
                     thread("thr_same000001", "proj_orbit", "Personal twin")],
            trackers=[{"prefix": "ORB", "name": "Orbit", "linkedBbProjectId": "proj_orbit"},
                      {"prefix": "DUP", "name": "Dup personal"}],
            tasks=[{"key": "ORB-1", "title": "Pair over QR", "status": "in_progress", "priority": "high",
                    "updatedAt": iso_ago(hours=3)},
                   {"key": "DUP-1", "title": "Personal dup", "status": "todo", "updatedAt": iso_ago(hours=3)}],
            memories=[{"id": "mp", "name": "watch-density", "projectId": "proj_orbit", "summary": "Vendor density 0.75"}],
            logs={"thr_orbit00001": "Assistant: workspaces half done", "thr_same000001": "Assistant: personal twin log"},
            details={"ORB-1": task_detail("Pair over QR"), "DUP-1": task_detail("Personal dup")},
            new_thread="thr_newpers001", new_task="ORB-9"),
        str(dirs["work"]): world(
            WORK_URL,
            projects=[{"id": "proj_tools", "name": "tools", "sources": [{"path": "/nope2", "isDefault": True}]},
                      {"id": "proj_orbit", "name": "orbit-clone"}],  # same id as personal's Orbit project
            threads=[thread("thr_work00001", "proj_tools", "Reply to Alex", "error"),
                     thread("thr_same000001", "proj_tools", "Work twin")],
            trackers=[{"prefix": "WORK", "name": "Work replies", "linkedBbProjectId": "proj_tools"},
                      {"prefix": "DUP", "name": "Dup work"}],
            tasks=[{"key": "WORK-3", "title": "Reply Alex: FE-100 rollout plan", "status": "todo",
                    "priority": "high", "updatedAt": iso_ago(hours=1), "dueDate": datetime.now().date().isoformat()},
                   {"key": "DUP-1", "title": "Work dup", "status": "todo", "updatedAt": iso_ago(hours=3)}],
            memories=[{"id": "mw", "name": "work-posts-own-voice", "projectId": None, "summary": "Use the work-post skill"}],
            logs={"thr_work00001": "Assistant: drafted the reply", "thr_same000001": "Assistant: work twin log"},
            details={"WORK-3": task_detail("Reply Alex: FE-100 rollout plan"), "DUP-1": task_detail("Work dup")},
            outputs={"thr_work00001": "Drafted the reply to Alex."},
            broken=["tasks show WORK-3"],
            new_thread="thr_newwork001", new_task="WORK-7"),
    }
    worlds_path = tmp_path / "worlds.json"
    worlds_path.write_text(json.dumps(worlds))
    bin_dir = tmp_path / "bbbin2"
    bin_dir.mkdir()
    (bin_dir / "bb").write_text(FAKE_BB)
    (bin_dir / "bb").chmod(0o755)
    log = tmp_path / "bb2-calls.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_BB_WORLDS", str(worlds_path))
    monkeypatch.setenv("FAKE_BB_LOG", str(log))
    # Inherited from a BB thread / the personal app: must never reach any workspace's bb.
    for k, v in {"BB_THREAD_ID": "thr_mine", "BB_PROJECT_ID": "proj_mine", "BB_SERVER_URL": PERSONAL_URL,
                 "BB_DATA_DIR": str(dirs["personal"]), "BB_HOST_DAEMON_PORT": "38887", "BB_CLI": "/x/bb",
                 "BB_ENVIRONMENT_ID": "env_mine", "BB_THREAD_STORAGE": "/tmp/x"}.items():
        monkeypatch.setenv(k, v)
    wsmod._client_versions.clear()

    class Two:
        def calls(self, ws=None):
            rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return [r for r in rows if ws is None or r["env"].get("BB_DATA_DIR") == str(dirs[ws])]

        def runtime(self, ws, **changes):
            p = dirs[ws] / "bb-app-runtime.json"
            p.write_text(json.dumps({**json.loads(p.read_text()), **changes}))

    t = Two()
    t.dirs, t.home = dirs, home
    return t


def ws_answer(*a, workspace="", **kw):
    out = answer(*a, **kw)
    out["proposed_action"]["workspace"] = workspace
    return out


@pytest.fixture
def talk2(cfg, store, client, two, codex):
    cfg.talk_workspace = "bb"
    tts_calls = []
    talker = Talker(cfg, store, tts=fake_tts([], tts_calls))

    def turn(text, minute=[0]):  # noqa: B006 - a counter across turns
        minute[0] += 1
        started = (datetime.now().astimezone().replace(microsecond=0) - timedelta(minutes=60 - minute[0])).isoformat()
        note = add_talk(client, store, started, text)
        assert talker.run(note), store.get(note.id).talk_error
        return store.audio_path(note).with_name(f"{note.stem}.reply.txt").read_text().strip()

    turn.talker = talker
    return turn


# --- discovery + config ------------------------------------------------------------------------------


def test_discovery_personal_first_and_config_overrides(two, cfg, tmp_path):
    (two.home / ".bb-lab").mkdir()  # bb.db but no runtime file: its app is closed → still listed
    (two.home / ".bb-lab" / "bb.db").write_bytes(b"")
    assert [s.name for s in discover()] == ["personal", "lab", "work"]
    (two.home / ".bb-lab" / "bb.db").unlink()
    assert [(s.name, s.data_dir) for s in discover()] == [("personal", two.dirs["personal"]), ("work", two.dirs["work"])]
    assert [s.name for s in configured_specs(cfg)] == ["personal", "work"]
    cfg.talk_workspaces = ["work"]
    assert [s.name for s in configured_specs(cfg)] == ["work"]
    cfg.talk_workspaces = ["personal", "lab"]  # "lab" isn't there: kept (reported unreachable), never replaced
    assert [(s.name, s.data_dir) for s in configured_specs(cfg)] == [("personal", two.dirs["personal"]),
                                                                    ("lab", two.home / ".bb-lab")]
    cfg.talk_workspaces = []
    cfg.workspaces = [{"name": "Work", "data_dir": str(two.dirs["work"]), "server_url": "http://127.0.0.1:1"}]
    assert configured_specs(cfg) == [WorkspaceSpec("work", two.dirs["work"], "http://127.0.0.1:1")]


def test_config_file_reads_workspaces_and_restriction(tmp_path, monkeypatch):
    from orbit_notes import config

    path = tmp_path / "c.toml"
    path.write_text('token = "t"\ntalk_workspaces = ["personal", "work"]\norbit_workspace = "personal"\n'
                    '[[workspaces]]\nname = "work"\ndata_dir = "~/.bb-work"\n')
    c = config.load(path)
    assert c.talk_workspaces == ["personal", "work"] and c.workspaces[0]["name"] == "work"
    monkeypatch.setenv("ORBIT_TALK_WORKSPACES", "work")
    assert config.load(path).talk_workspaces == ["work"]


# --- index ---------------------------------------------------------------------------------------------


def exact_env(two, ws):
    url = PERSONAL_URL if ws == "personal" else WORK_URL
    return {"BB_DATA_DIR": str(two.dirs[ws]), "BB_SERVER_URL": url}


def test_merged_index_is_labelled_and_refs_keyed_by_workspace(two, cfg):
    idx = Workspaces.from_config(cfg).index()
    text = idx.text
    assert text.startswith("BB WORKSPACE SNAPSHOT taken ") and "2 BB workspaces: personal, work" in text
    assert "=== [personal] workspace (snapshot taken " in text and "=== [work] workspace (snapshot taken " in text
    assert "- [personal] proj_orbit orbit-launcher — /nope" in text and "- [work] proj_tools tools — /nope2" in text
    assert '- [work] thr_work00001 (project tools) "Reply to Alex" — error' in text
    assert '- [personal] thr_same000001 (project orbit-launcher) "Personal twin"' in text
    assert '- [work] thr_same000001 (project tools) "Work twin"' in text  # same id, kept apart
    assert '- [work] WORK-3 "Reply Alex: FE-100 rollout plan"' in text and '- [personal] ORB-1 "Pair over QR"' in text
    assert idx.refs.get("work").threads["thr_same000001"]["title"] == "Work twin"
    assert idx.refs.get("personal").threads["thr_same000001"]["title"] == "Personal twin"
    assert idx.refs.get("work").projects["proj_orbit"]["name"] == "orbit-clone"
    # Every call went to exactly one workspace with BOTH variables, and nothing inherited leaked.
    for c in two.calls():
        ws = "work" if c["env"]["BB_DATA_DIR"] == str(two.dirs["work"]) else "personal"
        assert c["env"] == exact_env(two, ws), c
    assert {len(two.calls("personal")), len(two.calls("work"))} == {2 + 1 + 2}  # projects, threads, trackers, 2 lists


def test_server_url_is_read_at_call_time(two, cfg):
    wss = Workspaces.from_config(cfg)
    two.runtime("work", serverUrl="http://127.0.0.1:40000")  # bb restarted on another port
    idx = wss.index()
    assert "=== [work] workspace: NOT AVAILABLE" in idx.text and "wrong server: http://127.0.0.1:40000" in idx.text
    assert "=== [personal] workspace (snapshot" in idx.text


def test_unreachable_workspace_never_falls_back(two, cfg):
    (two.dirs["work"] / "bb-app-runtime.json").unlink()  # work bb app closed
    wss = Workspaces.from_config(cfg)
    idx = wss.index()
    assert "=== [work] workspace: NOT AVAILABLE right now (the work workspace is unreachable: its bb app is not " \
           "running" in idx.text
    assert "never answer from the other workspace instead" in idx.text
    assert "- [personal] proj_orbit orbit-launcher" in idx.text and idx.unavailable.keys() == {"work"}
    assert not two.calls("work") and all(c["env"] == exact_env(two, "personal") for c in two.calls())
    # Details / actions for work fail clearly instead of running against personal.
    text = wss.details({"threads": ["work:thr_work00001"]}, idx)
    assert "## Thread thr_work00001 [work]\n(could not be read: the work workspace is unreachable" in text
    with pytest.raises(wsmod.WorkspaceError, match="work workspace is unreachable"):
        wss.execute(__import__("orbit_notes.actions", fromlist=["Command"]).Command(["thread", "list"]), "work")
    assert not two.calls("work")


def test_stale_runtime_file_is_unreachable(two, cfg):
    two.runtime("work", pid=2 ** 22 + 12345)  # no such process
    idx = Workspaces.from_config(cfg).index()
    assert "stale bb-app-runtime.json" in idx.text and not two.calls("work")


# --- details ---------------------------------------------------------------------------------------------


def test_details_go_to_the_owning_workspace(two, cfg):
    wss = Workspaces.from_config(cfg)
    idx = wss.index()
    n = len(two.calls())
    text = wss.details({"threads": ["thr_work00001", "thr_same000001", "personal:thr_orbit00001"],
                        "tasks": ["ORB-1", "DUP-1"],
                        "memory": [{"query": "voice", "project": "", "workspace": "work"},
                                   {"query": "density", "project": "orbit-launcher", "workspace": ""}]}, idx)
    new = two.calls()[n:]
    logs = {(c["args"][2], c["env"]["BB_DATA_DIR"]) for c in new if c["args"][:2] == ["thread", "log"]}
    assert logs == {("thr_work00001", str(two.dirs["work"])), ("thr_same000001", str(two.dirs["work"])),
                    ("thr_same000001", str(two.dirs["personal"])), ("thr_orbit00001", str(two.dirs["personal"]))}
    assert "## Thread thr_same000001 [personal]" in text and "personal twin log" in text
    assert "## Thread thr_same000001 [work]" in text and "work twin log" in text
    assert "## Task DUP-1 [personal]" in text and "## Task DUP-1 [work]" in text
    shows = {(c["args"][2], c["env"]["BB_DATA_DIR"]) for c in new if c["args"][:2] == ["tasks", "show"]}
    assert ("ORB-1", str(two.dirs["personal"])) in shows and ("ORB-1", str(two.dirs["work"])) not in shows
    searches = [c for c in new if c["args"][:2] == ["memory", "search"]]
    assert {(c["env"]["BB_DATA_DIR"], c["env"].get("BB_PROJECT_ID")) for c in searches} == {
        (str(two.dirs["work"]), "proj_tools"), (str(two.dirs["work"]), "proj_orbit"),  # every work project
        (str(two.dirs["personal"]), "proj_orbit")}  # the named project, in the workspace that has it
    assert "- [work] work-posts-own-voice (global" in text and "- [personal] watch-density (orbit-launcher" in text
    for c in new:
        assert {k: v for k, v in c["env"].items() if k != "BB_PROJECT_ID"} in (exact_env(two, "personal"),
                                                                                exact_env(two, "work"))


def test_version_skew_failure_degrades_with_a_clear_message(two, cfg):
    wss = Workspaces.from_config(cfg)
    text = wss.details({"tasks": ["WORK-3"], "threads": ["thr_work00001"]}, wss.index())
    assert ("## Task WORK-3 [work]\n(could not be read: bb tasks show is not available in the work workspace "
            "(bb 0.44.0, the CLI is 0.49.0): HTTP 400: unrecognized request body (schema mismatch))") in text
    assert "drafted the reply" in text  # the rest of the round still works
    # A plain "not found" is not blamed on the version.
    text = wss.details({"tasks": ["WORK-99"]}, wss.index())
    assert "(could not be read: task not found)" in text


# --- turns: routing, labels, ambiguity ---------------------------------------------------------------


def test_turn_prompt_has_both_workspaces_and_rules(talk2, codex, two):
    codex.script(ws_answer("Your work workspace has one task due today."))
    talk2("What's going on in my work workspace?")
    p = codex.calls()[0]["prompt"]
    assert "=== [work] workspace (snapshot taken" in p and "=== [personal] workspace (snapshot taken" in p
    assert "Every project, thread, task and memory line is labelled with its BB workspace" in p
    assert "proposed_action.workspace" in p


def test_action_inferred_from_id_says_workspace_and_runs_there(talk2, codex, two, cfg):
    codex.script(ws_answer("You want a comment on WORK-3 saying the plan is ready.", kind="task_comment",
                           params={"key": "WORK-3", "body": "Plan ready"}, summary="Comment on WORK-3"),
                 ws_answer("Okay.", confirmation="confirm"))
    reply = talk2("Comment on WORK-3 that the plan is ready")
    assert reply == ("In your work workspace, task WORK-3: You want a comment on WORK-3 saying the plan is ready. "
                     + CONFIRM_ASK)
    log = ActionLog(cfg.data_dir)
    assert log.pending().workspace == "work"
    assert log.pending().summary == "Comment on WORK-3 (in your work workspace, task WORK-3)"
    reply = talk2("yes")
    assert reply == "Okay. Done in your work workspace: I added your comment to task WORK-3."
    [w] = writes(two.calls())
    assert w["args"][:3] == ["tasks", "comment", "WORK-3"] and w["env"] == exact_env(two, "work")
    events = [(e["event"], e.get("workspace")) for e in reversed(log.entries())]
    assert events == [("proposed", "work"), ("confirmed", "work"), ("executed", "work")]


def test_codex_named_workspace_is_checked(talk2, codex, two, cfg):
    codex.script(ws_answer("Message the Alex thread in your personal workspace?", kind="thread_tell", workspace="personal",
                           params={"thread_id": "thr_work00001", "message": "hi"}, summary="Message the Alex thread"),
                 ws_answer("Okay?", kind="thread_tell", workspace="lab",
                           params={"thread_id": "thr_work00001", "message": "hi"}, summary="Message the Alex thread"))
    reply = talk2("Tell the Alex thread hi")
    assert "But I can't: thread thr_work00001 is not in your personal workspace; it is in your work workspace." in reply
    reply = talk2("Tell the Alex thread hi")
    assert "there is no BB workspace called 'lab' (I know personal and work)" in reply
    assert ActionLog(cfg.data_dir).pending() is None and not writes(two.calls())


@pytest.mark.parametrize("kind, params", [
    ("memory_add", {"scope": "global", "name": "x-note", "summary": "s", "details": "d", "reason": "r"}),
    ("task_create", {"project": "DUP", "title": "Something"}),  # DUP exists in both
    ("thread_tell", {"thread_id": "thr_same000001", "message": "hi"}),  # same id in both
])
def test_unclear_workspace_asks_instead_of_guessing(talk2, codex, two, cfg, kind, params):
    codex.script(ws_answer("Okay, shall I?", kind=kind, params=params, summary="Do the thing"))
    reply = talk2("do the thing")
    assert reply.startswith("I understood that you want me to do the thing. But ")
    assert ("I don't know which workspace it is for:" in reply) if kind == "memory_add" else \
        ("exists in both your personal and work workspaces:" in reply)
    assert reply.endswith("is it your personal or your work workspace? Nothing was changed yet.")
    log = ActionLog(cfg.data_dir)
    assert log.pending() is None and log.entries()[0]["event"] == "rejected" and log.entries()[0]["needs"] == "workspace"
    assert not writes(two.calls())


def test_orchestrator_parenting_only_for_personal_orbit(talk2, codex, two, cfg):
    cfg.orbit_project, cfg.orbit_orchestrator_thread = "proj_orbit", "thr_orch"
    params = {"project": "proj_orbit", "prompt": "Do it.", "title": "T"}
    codex.script(ws_answer("New thread in your work workspace?", kind="thread_spawn", workspace="work", params=params,
                           summary="Spawn"),
                 ws_answer("Okay.", confirmation="confirm"),
                 ws_answer("New thread in your personal workspace?", kind="thread_spawn", workspace="personal",
                           params=params, summary="Spawn"),
                 ws_answer("Okay.", confirmation="confirm"))
    talk2("start a thread in work orbit-clone")
    reply = talk2("yes")
    assert reply == "Okay. Done in your work workspace: I started the thread “New” in orbit-clone, id thr_newwork001."
    talk2("start a thread in orbit")
    reply = talk2("yes")
    assert "Done in your personal workspace: I started the thread “New” in orbit-launcher, id thr_newpers001." in reply
    work_spawn, pers_spawn = writes(two.calls())
    assert work_spawn["env"] == exact_env(two, "work") and not any("--parent-thread" in a for a in work_spawn["args"])
    assert "thr_orch" not in work_spawn["stdin"]
    assert pers_spawn["env"] == exact_env(two, "personal") and "--parent-thread=thr_orch" in pers_spawn["args"]


def test_failure_in_one_workspace_is_reported_with_it(talk2, codex, two, cfg):
    codex.script(ws_answer("Comment?", kind="task_comment", params={"key": "WORK-3", "body": "x"}, summary="Comment"),
                 ws_answer("Okay.", confirmation="confirm"))
    talk2("comment on work-3")
    (two.dirs["work"] / "bb-app-runtime.json").unlink()
    reply = talk2("yes")
    assert reply.startswith("Okay. I tried in your work workspace, but it did not work: the work workspace is unreachable")
    assert not writes(two.calls())


def test_briefing_covers_both_workspaces(talk2, codex, two):
    codex.script(ws_answer("Good morning."))
    talk2("Give me my morning briefing")
    p = codex.calls()[0]["prompt"]
    ev = p[p.index("BRIEFING EVIDENCE gathered"):p.index("VOICE NOTE (")]
    assert '- [work] WORK-3 "Reply Alex: FE-100 rollout plan" — due today' in ev
    assert '- [personal] ORB-1 "Pair over QR" — in progress' in ev
    assert '- [work] thr_work00001 "Reply to Alex" (project tools)' in ev


def test_ui_lists_workspaces_and_labels_pending(talk2, codex, two, cfg, store):
    codex.script(ws_answer("Comment?", kind="task_comment", params={"key": "WORK-3", "body": "x"}, summary="Comment"))
    talk2("comment on work-3")
    t = ui_client(cfg, store).get("/ui/api/talk").json()
    assert [(w["name"], w["reachable"], w.get("version")) for w in t["workspaces"]] == [
        ("personal", True, "0.49.0"), ("work", True, "0.44.0")]
    assert t["pending"]["workspace"] == "work" and t["audit"][0]["workspace"] == "work"


# --- inbox -----------------------------------------------------------------------------------------------


def test_inbox_watches_threads_in_their_workspace(two, cfg, tmp_path):
    from datetime import timezone

    audit = cfg.data_dir / "talk_audit.jsonl"
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    audit.write_text(json.dumps({"at": at, "event": "executed", "kind": "thread_tell", "workspace": "work",
                                 "params": {"thread_id": "thr_work00001"}}) + "\n"
                     + json.dumps({"at": at, "event": "executed", "kind": "thread_tell",  # from before workspaces
                                   "params": {"thread_id": "thr_orbit00001"}}) + "\n")
    box = Inbox(cfg.data_dir)
    p = ThreadsProducer(Workspaces.from_config(cfg), audit)
    p.run(box)  # first run: records state
    items = {i.ref: i for i in p.run(box)} or None
    # Both are older than the first run → quiet; what matters is where each was read and how it is keyed.
    lists = {c["env"]["BB_DATA_DIR"] for c in two.calls() if c["args"][:2] == ["thread", "list"]}
    assert lists == {str(two.dirs["work"]), str(two.dirs["personal"])}
    outputs = [c for c in two.calls() if c["args"][:2] == ["thread", "output"]]
    # (the work thread is in "error": no output needed)
    assert {(c["args"][2], c["env"]["BB_DATA_DIR"]) for c in outputs} == {("thr_orbit00001", str(two.dirs["personal"]))}
    assert set(box.state("threads")["threads"]) == {"work:thr_work00001", "thr_orbit00001"}
    assert items is None


def test_inbox_item_for_a_work_thread_is_labelled(two, cfg):
    from datetime import timezone

    audit = cfg.data_dir / "talk_audit.jsonl"
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    box = Inbox(cfg.data_dir)
    p = ThreadsProducer(Workspaces.from_config(cfg), audit)
    p.run(box)  # the producer's first run happens before the tell
    worlds = json.loads(open(os.environ["FAKE_BB_WORLDS"]).read())
    w = worlds[str(two.dirs["work"])]
    at = datetime.now(timezone.utc) - timedelta(minutes=1)
    w["threads"][0].update(status="idle", updatedAt=int(datetime.now(timezone.utc).timestamp() * 1000) + 5000)
    open(os.environ["FAKE_BB_WORLDS"], "w").write(json.dumps(worlds))
    audit.write_text(json.dumps({"at": at.isoformat(), "event": "executed", "kind": "thread_tell", "workspace": "work",
                                 "params": {"thread_id": "thr_work00001"}}) + "\n")
    [item] = p.run(box)
    assert item.title == "Work: Reply to Alex is done" and item.ref == "work:thr_work00001"
    assert item.body == "Drafted the reply to Alex."
    # Talk's "tell me more" reads that thread's output from the work workspace.
    assert Workspaces.from_config(cfg).thread_output(item.ref) == "Drafted the reply to Alex."


def test_single_workspace_keeps_old_refs(two, cfg):
    cfg.talk_workspaces = ["personal"]
    wss = Workspaces.from_config(cfg)
    assert not wss.multi and wss.ref_for("personal", "thr_x") == "thr_x"
    assert isinstance(wss.get(None), Workspace)
