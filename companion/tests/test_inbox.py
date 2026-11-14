"""Orbit Inbox (docs/inbox.md): store, endpoints, CLI, the threads producer (fake bb), Talk `about` context."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from orbit_notes import inbox as inbox_mod
from orbit_notes.api import create_app
from orbit_notes.cli import app as cli_app
from orbit_notes.inbox import Inbox, InvalidItem, NewItem, Producers, ThreadsProducer, summary, talk_context
from orbit_notes.talk import Talker
from orbit_notes.workspace import Workspace
from test_companion import AUTH, add_talk, cfg, client, fake_tts, store, ui_client  # noqa: F401 (fixtures)
from test_workspace import FAKE_CODEX, answer, codex  # noqa: F401 (fixture)

# The workspace fake from test_workspace.py plus `thread output`, reading a data file the test rewrites.
FAKE_BB = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
data = json.load(open(os.environ["FAKE_BB_DATA"]))
with open(os.environ["FAKE_BB_LOG"], "a") as f:
    f.write(json.dumps(a) + "\n")
def out(x):
    print(json.dumps(x)); sys.exit(0)
if a[:2] == ["project", "list"]: out(data.get("projects", []))
if a[:2] == ["thread", "list"]: out(data["threads"])
if a[:3] == ["tasks", "project", "list"]: out({"projects": []})
if a[:2] == ["thread", "output"]:
    if a[2] in data["outputs"]: out({"output": data["outputs"][a[2]]})
    print(json.dumps({"ok": False, "error": {"message": "no such thread"}})); sys.exit(1)
print(json.dumps({"ok": False, "error": {"message": "unknown " + " ".join(a)}})); sys.exit(1)
'''


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class FakeBB:
    def __init__(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bbbin"
        bin_dir.mkdir()
        (bin_dir / "bb").write_text(FAKE_BB)
        (bin_dir / "bb").chmod(0o755)
        self.data_path, self.log = tmp_path / "bb.json", tmp_path / "bb-calls.jsonl"
        self.data = {"projects": [{"id": "proj_orbit", "name": "orbit-launcher", "sources": []}],
                     "threads": [], "outputs": {}}
        self.save()
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        monkeypatch.setenv("FAKE_BB_DATA", str(self.data_path))
        monkeypatch.setenv("FAKE_BB_LOG", str(self.log))

    def save(self):
        self.data_path.write_text(json.dumps(self.data))

    def thread(self, tid, title, status, updated: datetime, output=None, pending=False):
        self.data["threads"] = [t for t in self.data["threads"] if t["id"] != tid] + [{
            "id": tid, "title": title, "status": status, "updatedAt": ms(updated), "projectId": "proj_orbit",
            "hasPendingInteraction": pending, "archivedAt": None, "visibility": "visible"}]
        if output is not None:
            self.data["outputs"][tid] = output
        self.save()

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []


@pytest.fixture
def bb(tmp_path, monkeypatch):
    return FakeBB(tmp_path, monkeypatch)


@pytest.fixture
def box(cfg):
    return Inbox(cfg.data_dir)


@pytest.fixture(autouse=True)
def fresh_shared():
    inbox_mod._shared.clear()  # one Inbox/Producers per data dir per process; tests start clean
    yield
    inbox_mod._shared.clear()


def audit(cfg, *entries):
    with (cfg.data_dir / "talk_audit.jsonl").open("a") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def spawned(tid, title, at: datetime):
    return {"at": at.isoformat(), "event": "executed", "kind": "thread_spawn", "params": {"title": title},
            "result": {"id": tid, "title": title, "status": "starting"}}


def told(tid, at: datetime):
    return {"at": at.isoformat(), "event": "executed", "kind": "thread_tell", "params": {"thread_id": tid, "message": "x"}}


NOW = datetime.now(timezone.utc)


# --- store -----------------------------------------------------------------------------------------


def test_store_add_changes_cursor_and_read(box):
    assert box.changes(0) == (0, [])
    a = box.add(NewItem("Backup finished", "All 3 disks are fine."))
    b = box.add(NewItem("Cancel feature is done", kind="thread_done", ref="thr_abc1234567"))
    assert (a.rev, b.rev) == (1, 2) and a.id.startswith("in-")
    cursor, items = box.changes(0)
    assert cursor == 2 and [i.id for i in items] == [b.id, a.id]  # newest first
    assert box.changes(2) == (2, [])
    # Reading bumps the item's rev: a watch polling since=2 sees the read state change.
    r = box.mark_read(a.id)
    assert r.read_at and r.rev == 3
    assert [(i.id, bool(i.read_at)) for i in box.changes(2)[1]] == [(a.id, True)]
    assert box.mark_read(a.id).rev == 3  # already read: no change, no new rev
    assert [i.id for i in box.unread()] == [b.id]
    assert box.mark_read(a.id, read=False).read_at is None
    assert box.mark_read("in-nope") is None


def test_store_dedupe_validation_and_cap(box, monkeypatch):
    assert box.add(NewItem("x", dedupe="k1")) is not None
    assert box.add(NewItem("x again", dedupe="k1")) is None
    with pytest.raises(InvalidItem):
        box.add(NewItem("   "))
    with pytest.raises(InvalidItem):
        box.add(NewItem("ok", kind="Bad Kind!"))
    long = box.add(NewItem("t" * 300, "b" * 5000))
    assert len(long.title) == inbox_mod.TITLE_MAX and long.title.endswith("…")
    assert len(long.body) == inbox_mod.BODY_MAX
    monkeypatch.setattr(inbox_mod, "KEEP_ITEMS", 3)
    for n in range(5):
        box.add(NewItem(f"n{n}"))
    assert [i.title for i in box.latest()] == ["n4", "n3", "n2"]


def test_summary_is_one_spoken_paragraph():
    out = ("**Cancel** is in place in both the recorder and `Talk`. Unit tests pass and the APKs build. "
           "Nothing is installed.\n\n**Branch:** `bb/x`, commit `6993012`.")
    s = summary(out, limit=80)
    assert s == "Cancel is in place in both the recorder and Talk. Unit tests pass and the APKs build."[:80].rsplit(". ", 1)[0] + "."
    assert "*" not in summary(out) and "`" not in summary(out)
    assert summary("") == ""


# --- endpoints -------------------------------------------------------------------------------------


def test_inbox_endpoints(client, box):
    assert client.get("/v1/inbox").status_code == 401
    assert client.post("/v1/inbox/in-x/read").status_code == 401
    r = client.get("/v1/inbox", headers=AUTH)
    assert r.json() == {"cursor": 0, "unread": 0, "items": []}
    a = box.add(NewItem("Backup finished", "All good.", ref="r1"))
    r = client.get("/v1/inbox", params={"since": 0}, headers=AUTH).json()
    assert r["cursor"] == 1 and r["unread"] == 1
    assert r["items"] == [{"id": a.id, "rev": 1, "created_at": a.created_at, "kind": "note", "title": "Backup finished",
                           "body": "All good.", "ref": "r1", "read_at": None}]
    assert client.get("/v1/inbox", params={"since": 1}, headers=AUTH).json()["items"] == []
    read = client.post(f"/v1/inbox/{a.id}/read", headers=AUTH)
    assert read.status_code == 200 and read.json()["read_at"]
    r = client.get("/v1/inbox", params={"since": 1}, headers=AUTH).json()
    assert r["cursor"] == 2 and r["unread"] == 0 and r["items"][0]["read_at"]
    assert client.post("/v1/inbox/in-missing/read", headers=AUTH).status_code == 404


def test_ui_inbox_add_list_read_and_lan_forbidden(cfg, store):
    ui = ui_client(cfg, store)
    r = ui.post("/ui/api/inbox", json={"title": "Orchestrator: inbox merged", "body": "Installed on the watch."})
    assert r.status_code == 200
    item = r.json()
    assert ui.post("/ui/api/inbox", json={"title": ""}).status_code == 422
    assert ui.post("/ui/api/inbox", json={"title": "x", "evil": 1}).status_code == 422
    listed = ui.get("/ui/api/inbox").json()
    assert listed["unread"] == 1 and listed["items"][0]["id"] == item["id"]
    assert ui.post(f"/ui/api/inbox/{item['id']}/read", json={}).json()["read_at"]
    assert ui.post(f"/ui/api/inbox/{item['id']}/read", json={"read": False}).json()["read_at"] is None
    lan = ui_client(cfg, store, host="192.168.1.50")
    assert lan.post("/ui/api/inbox", json={"title": "x"}).status_code == 403
    assert lan.get("/ui/api/inbox").status_code == 403
    # The watch's token API sees what the UI added.
    watch = TestClient(create_app(cfg, store))
    assert watch.get("/v1/inbox", headers=AUTH).json()["items"][0]["title"] == "Orchestrator: inbox merged"


def test_cli_inbox_add_list_read(cfg):
    runner = CliRunner()
    r = runner.invoke(cli_app, ["inbox", "add", "--title", "Build is green", "--body", "All tests pass.",
                                "--config", str(cfg.path)])
    assert r.exit_code == 0, r.output
    item = json.loads(r.output)
    assert item["title"] == "Build is green" and item["kind"] == "note"
    r = runner.invoke(cli_app, ["inbox", "list", "--config", str(cfg.path)])
    assert "● " + item["id"] in r.output and "Build is green" in r.output
    r = runner.invoke(cli_app, ["inbox", "read", item["id"], "--config", str(cfg.path)])
    assert r.exit_code == 0 and json.loads(r.output)["read_at"]
    assert runner.invoke(cli_app, ["inbox", "add", "--title", " ", "--config", str(cfg.path)]).exit_code != 0


# --- threads producer (fake bb) --------------------------------------------------------------------


def producer(cfg, box):
    return ThreadsProducer(Workspace(), cfg.data_dir / "talk_audit.jsonl")


def test_threads_producer_done_input_error(cfg, box, bb):
    p = producer(cfg, box)
    # Spawned yesterday and already finished before the inbox existed: recorded silently, no backlog.
    audit(cfg, spawned("thr_old0000001", "Old work", NOW - timedelta(days=1)))
    bb.thread("thr_old0000001", "Old work", "idle", NOW - timedelta(hours=20), "Old output.")
    assert p.run(box) == []

    # Spawned by Talk now; running → nothing; then idle with output → "is done" with a one-line summary.
    audit(cfg, spawned("thr_cancel00001", "Cancel Talk and voice-note recordings", NOW))
    bb.thread("thr_cancel00001", "Cancel Talk and voice-note recordings", "active", NOW + timedelta(seconds=5))
    assert p.run(box) == []
    bb.thread("thr_cancel00001", "Cancel Talk and voice-note recordings", "idle", NOW + timedelta(minutes=9),
              "**Cancel** is in place in both screens. Unit tests pass.\n\n**Branch:** `bb/cancel`.")
    [done] = p.run(box)
    assert done.kind == "thread_done" and done.ref == "thr_cancel00001"
    assert done.title == "Cancel Talk and voice-note recordings is done"
    assert done.body == "Cancel is in place in both screens. Unit tests pass."
    assert box.add(done) is not None
    # Same output, touched again (e.g. read in BB): quiet. bb thread output is not re-read without a change.
    assert p.run(box) == []
    bb.thread("thr_cancel00001", "Cancel Talk and voice-note recordings", "idle", NOW + timedelta(minutes=10),
              "**Cancel** is in place in both screens. Unit tests pass.\n\n**Branch:** `bb/cancel`.")
    assert p.run(box) == []

    # Talk messaged it again; it asks a question → "needs your input" once.
    audit(cfg, told("thr_cancel00001", NOW + timedelta(minutes=11)))
    bb.thread("thr_cancel00001", "Cancel Talk and voice-note recordings", "idle", NOW + timedelta(minutes=12),
              pending=True)
    [ask] = p.run(box)
    assert ask.kind == "thread_input" and ask.title.endswith("needs your input")
    assert p.run(box) == []
    # Answered, and the next turn ends with a different output → done again.
    bb.thread("thr_cancel00001", "Cancel Talk and voice-note recordings", "idle", NOW + timedelta(minutes=20),
              "Second round finished. Merged nothing.")
    [again] = p.run(box)
    assert again.kind == "thread_done" and again.body == "Second round finished. Merged nothing."

    bb.thread("thr_cancel00001", "Cancel Talk and voice-note recordings", "error", NOW + timedelta(minutes=30))
    [err] = p.run(box)
    assert err.kind == "thread_error" and err.title.endswith("stopped with an error")
    assert p.run(box) == []
    outputs = [c for c in bb.calls() if c[:2] == ["thread", "output"]]
    assert {c[2] for c in outputs} == {"thr_old0000001", "thr_cancel00001"}


def test_threads_producer_ignores_unwatched_and_old(cfg, box, bb):
    p = producer(cfg, box)
    assert p.run(box) == []  # first run (no audit): baseline only
    assert bb.calls() == []  # nothing watched → bb is not even called
    audit(cfg, spawned("thr_ancient0001", "Ancient", NOW - timedelta(days=9)))
    bb.thread("thr_ancient0001", "Ancient", "idle", NOW + timedelta(minutes=1), "Done.")
    bb.thread("thr_other000001", "Not from Talk", "idle", NOW + timedelta(minutes=1), "Done.")
    assert p.run(box) == []


def test_producers_cached_and_isolated(cfg, box, bb):
    class Boom:
        name = "boom"

        def run(self, inbox):
            raise RuntimeError("broken producer")

    class One:
        name, calls = "one", 0

        def run(self, inbox):
            One.calls += 1
            return [NewItem("Update available", kind="app_update", dedupe="app:42")]

    ps = Producers(box, [Boom(), One()], ttl_s=60)
    assert ps.refresh() == 1
    assert ps.refresh() == 0 and One.calls == 1  # cached for 60 s
    assert ps.refresh(force=True) == 0 and One.calls == 2  # deduped
    assert [i.title for i in box.latest()] == ["Update available"]


def test_watch_poll_runs_producers_lazily(cfg, store, bb):
    cfg.talk_workspace = "bb"
    audit(cfg, spawned("thr_cancel00001", "Cancel feature", NOW))
    bb.thread("thr_cancel00001", "Cancel feature", "active", NOW + timedelta(seconds=1))
    c = TestClient(create_app(cfg, store))
    assert c.get("/v1/inbox", headers=AUTH).json()["items"] == []  # first run: baseline
    bb.thread("thr_cancel00001", "Cancel feature", "idle", NOW + timedelta(minutes=5), "It works now.")
    _, producers = inbox_mod.shared(cfg)
    producers._last = float("-inf")  # the 60 s cache has passed
    r = c.get("/v1/inbox", headers=AUTH).json()
    assert [(i["title"], i["body"], i["ref"]) for i in r["items"]] == [("Cancel feature is done", "It works now.",
                                                                        "thr_cancel00001")]
    lists = [x for x in bb.calls() if x[:2] == ["thread", "list"]]
    c.get("/v1/inbox", headers=AUTH)
    assert len([x for x in bb.calls() if x[:2] == ["thread", "list"]]) == len(lists)  # cached


# --- Talk ------------------------------------------------------------------------------------------


def test_talk_context_about_and_mention_once(box):
    a = box.add(NewItem("Cancel feature is done", "Cancel works in both screens.", "thread_done", "thr_cancel00001"))
    b = box.add(NewItem("Backup finished", "All good."))
    old = box.add(NewItem("Ancient news"))
    text, mentioned = talk_context(box, a.id, lambda tid: f"FULL OUTPUT of {tid}", now=datetime.fromisoformat(old.created_at) + timedelta(hours=1))
    assert "HE OPENED THIS INBOX ITEM" in text and "Cancel feature is done: Cancel works in both screens." in text
    assert "FULL OUTPUT of thr_cancel00001" in text
    assert "- Backup finished: All good." in text and "Cancel feature is done: Cancel works" in text.split("UNREAD")[0]
    assert set(mentioned) == {a.id, b.id, old.id}  # the opened item is the topic: never brought up again
    box.mark_mentioned(mentioned)
    text, mentioned = talk_context(box, None)
    assert text == "" and mentioned == []
    # Older than 24 h: not brought up.
    c = box.add(NewItem("Fresh"))
    text, mentioned = talk_context(box, None, now=datetime.fromisoformat(c.created_at) + timedelta(days=2))
    assert mentioned == []


@pytest.fixture
def talker(cfg, store, client, bb, codex):  # noqa: F811
    cfg.talk_workspace = "bb"
    return Talker(cfg, store, tts=fake_tts([]))


def test_talk_turn_about_item_and_unread_mention(cfg, store, client, bb, codex, talker):  # noqa: F811
    box, _ = inbox_mod.shared(cfg)
    bb.thread("thr_cancel00001", "Cancel feature", "idle", NOW, "Cancel is in place. Tests pass. Branch bb/cancel.")
    item = box.add(NewItem("Cancel feature is done", "Cancel is in place.", "thread_done", "thr_cancel00001"))
    other = box.add(NewItem("Backup finished", "All good."))
    codex.script(answer("It finished: cancel works in both screens. By the way, the backup finished."),
                 answer("Sure."))
    started = (datetime.now().astimezone().replace(microsecond=0) - timedelta(minutes=5)).isoformat()
    note = add_talk(client, store, started, "Tell me more about that.", about=item.id)
    assert talker.run(note), store.get(note.id).talk_error
    prompt = codex.calls()[0]["prompt"]
    assert "HE OPENED THIS INBOX ITEM" in prompt and "- Cancel feature is done: Cancel is in place." in prompt
    assert "Latest output of that thread (thr_cancel00001)" in prompt and "Branch bb/cancel." in prompt
    assert "- Backup finished: All good." in prompt
    assert prompt.index("UNREAD ON HIS WATCH") < prompt.index("VOICE NOTE")
    assert box.get(other.id).mentioned_at and box.get(item.id).mentioned_at

    # Next turn (no about): the backup is not brought up again.
    started = (datetime.now().astimezone().replace(microsecond=0) - timedelta(minutes=4)).isoformat()
    note2 = add_talk(client, store, started, "Thanks.")
    assert talker.run(note2)
    prompt2 = codex.calls()[1]["prompt"]
    assert "INBOX" not in prompt2 and "Backup finished" not in prompt2


def test_talk_turn_survives_broken_inbox(cfg, store, client, bb, codex, talker, monkeypatch):  # noqa: F811
    monkeypatch.setattr(inbox_mod, "talk_context", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    codex.script(answer("Hello."))
    started = (datetime.now().astimezone().replace(microsecond=0) - timedelta(minutes=5)).isoformat()
    note = add_talk(client, store, started, "Hi.", about="in-whatever")
    assert talker.run(note)
