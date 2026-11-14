import os
import hashlib
import hmac
import json
import uuid
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orbit_notes import config as config_mod
from orbit_notes import diary
from orbit_notes.api import create_app
from orbit_notes.store import Store

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for k in ("ORBIT_TOKEN", "ORBIT_DATA_DIR", "ORBIT_VAULT_DIR", "ORBIT_LLM_PROVIDER", "ORBIT_NAME"):
        monkeypatch.delenv(k, raising=False)
    path = tmp_path / "config.toml"
    path.write_text(f'name = "home-pc"\ntoken = "{TOKEN}"\ndata_dir = "data"\n')
    return config_mod.load(path)


@pytest.fixture
def store(cfg):
    return Store(cfg.data_dir)


@pytest.fixture
def client(cfg, store):
    stored = []
    c = TestClient(create_app(cfg, store, on_stored=stored.append))
    c.stored = stored
    return c


def make_note(started_at="2026-11-13T12:08:42+07:00", audio=b"fake adts audio", **over):
    meta = {"id": str(uuid.uuid4()), "started_at": started_at, "duration_ms": 14230,
            "sha256": hashlib.sha256(audio).hexdigest(), "device": "H10", "app_version": "0.1.0"}
    meta.update(over)
    return meta, audio


def put(client, meta, audio, filename="2026-11-13_12-08-42+0700.aac", headers=AUTH, note_id=None):
    return client.put(
        f"/v1/notes/{note_id or meta['id']}",
        files={"audio": (filename, audio, "audio/aac")},
        data={"meta": json.dumps(meta)},
        headers=headers,
    )


# --- health ---------------------------------------------------------------------------------------


def test_health_proof(client):
    r = client.get("/v1/health", params={"nonce": "abc123"})
    assert r.status_code == 200
    expected = hmac.new(TOKEN.encode(), b"abc123", hashlib.sha256).hexdigest()
    assert r.json() == {"name": "home-pc", "api": 1, "proof": expected}


def test_health_needs_nonce_and_is_rate_limited(client):
    assert client.get("/v1/health").status_code == 422
    codes = [client.get("/v1/health", params={"nonce": str(i)}).status_code for i in range(40)]
    assert codes[0] == 200 and codes[-1] == 429


# --- auth -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}])
def test_auth_required(client, headers):
    meta, audio = make_note()
    assert put(client, meta, audio, headers=headers).status_code == 401
    assert client.head(f"/v1/notes/{meta['id']}", headers=headers).status_code == 401


# --- upload ---------------------------------------------------------------------------------------


def test_put_head_and_idempotent(client, cfg, store):
    meta, audio = make_note()
    assert client.head(f"/v1/notes/{meta['id']}", headers=AUTH).status_code == 404

    r = put(client, meta, audio)
    assert r.status_code == 201
    assert r.json() == {"id": meta["id"], "sha256": meta["sha256"]}
    assert client.head(f"/v1/notes/{meta['id']}", headers=AUTH).status_code == 200

    folder = cfg.data_dir / "notes/2026/11/13"
    assert (folder / "2026-11-13_12-08-42+0700.aac").read_bytes() == audio
    assert json.loads((folder / "2026-11-13_12-08-42+0700.json").read_text())["id"] == meta["id"]
    assert client.stored == [meta["id"]]

    r2 = put(client, meta, audio)  # retry after a dropped connection
    assert r2.status_code == 200 and r2.json() == r.json()
    assert client.stored == [meta["id"]]
    assert len(list(folder.iterdir())) == 2
    assert not list(cfg.data_dir.rglob("*.tmp"))


def test_put_conflict_on_different_sha(client):
    meta, audio = make_note()
    assert put(client, meta, audio).status_code == 201
    other = b"different audio"
    meta2 = dict(meta, sha256=hashlib.sha256(other).hexdigest())
    assert put(client, meta2, other).status_code == 409


def test_put_sha_mismatch_rejected(client, cfg):
    meta, audio = make_note()
    r = put(client, meta, audio + b"corrupted")
    assert r.status_code == 422
    assert client.head(f"/v1/notes/{meta['id']}", headers=AUTH).status_code == 404
    assert not (cfg.data_dir / "notes").exists()


def test_put_id_mismatch_rejected(client):
    meta, audio = make_note()
    assert put(client, meta, audio, note_id=str(uuid.uuid4())).status_code == 422


def test_put_rejects_naive_started_at(client):
    meta, audio = make_note(started_at="2026-11-13T12:08:42")
    assert put(client, meta, audio).status_code == 422


# --- storage path from started_at --------------------------------------------------------------------


@pytest.mark.parametrize(
    "started_at, filename, expected",
    [
        # just before midnight in UTC+7
        ("2026-11-13T23:59:59+07:00", "2026-11-13_23-59-59+0700.aac", "notes/2026/11/13/2026-11-13_23-59-59+0700.aac"),
        # just after midnight in UTC+7 = 17:00 UTC on the 13th -> folder is the watch's local date (14th)
        ("2026-11-14T00:00:01+07:00", "2026-11-14_00-00-01+0700.aac", "notes/2026/11/14/2026-11-14_00-00-01+0700.aac"),
        # late evening in UTC-5 = next day in UTC -> still the 13th
        ("2026-11-13T23:30:00-05:00", "2026-11-13_23-30-00-0500.aac", "notes/2026/11/13/2026-11-13_23-30-00-0500.aac"),
        # year boundary
        ("2026-12-31T23:59:00+05:30", "2026-12-31_23-59-00+0530.aac", "notes/2026/12/31/2026-12-31_23-59-00+0530.aac"),
        # unsafe / odd filename -> stem derived from started_at
        ("2026-11-13T08:00:00+07:00", "../../etc/passwd", "notes/2026/11/13/2026-11-13_08-00-00+0700.aac"),
        ("2026-11-13T08:00:00+00:00", None, "notes/2026/11/13/2026-11-13_08-00-00+0000.aac"),
    ],
)
def test_storage_path_from_started_at(client, store, cfg, started_at, filename, expected):
    meta, audio = make_note(started_at=started_at)
    assert put(client, meta, audio, filename=filename or "upload.bin").status_code == 201
    note = store.get(meta["id"])
    assert note.path == expected
    assert (cfg.data_dir / expected).exists()


def test_same_second_never_overwrites(client, store):
    a_meta, a = make_note(audio=b"a")
    b_meta, b = make_note(audio=b"b")
    assert put(client, a_meta, a).status_code == 201
    assert put(client, b_meta, b).status_code == 201
    assert store.audio_path(store.get(a_meta["id"])).read_bytes() == b"a"
    assert store.audio_path(store.get(b_meta["id"])).read_bytes() == b"b"


# --- diary ----------------------------------------------------------------------------------------


def add_note(client, store, started_at, text=None, audio=None):
    meta, data = make_note(started_at=started_at, audio=audio or started_at.encode())
    assert put(client, meta, data, filename="x").status_code == 201
    note = store.get(meta["id"])
    if text is not None:
        store.audio_path(note).with_suffix(".txt").write_text(text + "\n")
        store.set_transcript_status(note.id, "done")
    return note


def test_diary_groups_by_local_date(client, store):
    add_note(client, store, "2026-11-13T07:02:10+07:00", "morning coffee")
    add_note(client, store, "2026-11-13T23:30:00-05:00", "late in new york")  # 04:30 UTC on the 14th
    add_note(client, store, "2026-11-14T00:10:00+07:00", "after midnight")    # 17:10 UTC on the 13th
    add_note(client, store, "2026-11-12T23:59:00+07:00", "day before")
    texts = [e.transcript for e in diary.collect(store, date(2026, 11, 13))]
    assert texts == ["morning coffee", "late in new york"]  # ordered by actual time
    assert [e.transcript for e in diary.collect(store, date(2026, 11, 14))] == ["after midnight"]


def test_diary_page_with_no_llm(client, store, cfg):
    add_note(client, store, "2026-11-13T07:02:10+07:00", "Bangun pagi, minum kopi.")
    add_note(client, store, "2026-11-13T12:08:42+07:00")  # not transcribed yet
    page = diary.build(cfg, store, date(2026, 11, 13))
    assert page == cfg.data_dir / "diary" / "2026-11-13.md"
    text = page.read_text()
    assert text.startswith("---\ndate: 2026-11-13\nnotes: 2\n---\n")
    assert "# Friday, 13 November 2026" in text
    assert "No LLM configured" in text
    assert "- 07:02 · 0:14 — [2026-11-13_07-02-10+0700.aac](<file://" in text
    assert "> Bangun pagi, minum kopi." in text
    assert "(not transcribed yet)" in text


def test_diary_embeds_vault_copy_of_audio(client, store, cfg, tmp_path):
    cfg.vault_dir = cfg.data_dir  # vault set -> audio copied next to the diary pages and embedded
    add_note(client, store, "2026-11-13T07:02:10+07:00", "hello")
    text = diary.build(cfg, store, date(2026, 11, 13)).read_text()
    copies = list((cfg.diary_dir / "audio").glob("2026-11-13_07-02-10+0700.*"))
    assert len(copies) == 1  # .m4a when ffmpeg can remux it; test bytes aren't real audio -> plain .aac copy
    rel = copies[0].relative_to(cfg.vault_dir).as_posix()
    assert f"![[{rel}]]" in text
    assert (cfg.data_dir / "notes/2026/11/13/2026-11-13_07-02-10+0700.aac").exists()  # original untouched


def test_diary_rewrite_preserves_text_outside_markers(client, store, cfg):
    add_note(client, store, "2026-11-13T07:02:10+07:00", "first version")
    page = diary.build(cfg, store, date(2026, 11, 13))
    edited = page.read_text().replace("---\n", "---\nmood: good\n", 1)
    edited = edited.replace(diary.BEGIN, "My own intro.\n\n" + diary.BEGIN) + "\nMy own footer.\n"
    edited = edited.replace("notes: 1", "notes: 1\ntags: [diary]")
    page.write_text(edited)

    add_note(client, store, "2026-11-13T20:00:00+07:00", "second note")
    text = diary.build(cfg, store, date(2026, 11, 13)).read_text()
    assert "My own intro.\n\n" + diary.BEGIN in text
    assert text.endswith("My own footer.\n")
    assert "notes: 2" in text and "notes: 1" not in text
    assert "tags: [diary]" in text and "mood: good" in text
    assert "second note" in text and text.count(diary.BEGIN) == 1


def test_merge_into_existing_page_without_markers():
    out = diary.merge("I wrote this by hand.\n", date(2026, 11, 13), 0, f"{diary.BEGIN}\nX\n{diary.END}")
    assert out == f"---\ndate: 2026-11-13\nnotes: 0\n---\n{diary.BEGIN}\nX\n{diary.END}\nI wrote this by hand.\n"


def test_anthropic_provider_is_pluggable(client, store, cfg, monkeypatch):
    add_note(client, store, "2026-11-13T07:02:10+07:00", "went to the market")
    calls = []

    def fake(cfg_, day, entries):
        calls.append([e.transcript for e in entries])
        return diary.Generated("I went to the market.", ["Market trip"])

    monkeypatch.setitem(diary.PROVIDERS, "anthropic", fake)
    cfg.llm_provider = "anthropic"
    text = diary.build(cfg, store, date(2026, 11, 13)).read_text()
    assert calls == [["went to the market"]]
    assert "I went to the market." in text and "— Market trip" in text


def test_anthropic_prompt_forbids_invention():
    assert "Never invent events" in diary.SYSTEM_PROMPT


# --- config ---------------------------------------------------------------------------------------


def test_token_generated_and_persisted(tmp_path, monkeypatch):
    monkeypatch.delenv("ORBIT_TOKEN", raising=False)
    path = tmp_path / "config.toml"
    first = config_mod.load(path)
    assert len(first.token) >= 40 and first.port == 8765 and first.host == "0.0.0.0"
    assert config_mod.load(path).token == first.token
    monkeypatch.setenv("ORBIT_TOKEN", "from-env")
    monkeypatch.setenv("ORBIT_PORT", "9000")
    over = config_mod.load(path)
    assert over.token == "from-env" and over.port == 9000


def test_anthropic_provider_parses_structured_output(client, store, cfg, monkeypatch):
    import types

    import anthropic

    add_note(client, store, "2026-11-13T07:02:10+07:00", "went to the market")
    seen = {}

    class FakeMessages:
        def create(self, **kw):
            seen.update(kw)
            out = {"diary": "I went to the market.", "summaries": [{"note": 0, "summary": "Market trip"}]}
            return types.SimpleNamespace(stop_reason="end_turn",
                                         content=[types.SimpleNamespace(type="text", text=json.dumps(out))])

    class FakeClient:
        beta = types.SimpleNamespace(messages=FakeMessages())

    monkeypatch.setattr(anthropic, "Anthropic", lambda: FakeClient())
    cfg.llm_provider = "anthropic"
    text = diary.build(cfg, store, date(2026, 11, 13)).read_text()
    assert seen["model"] == "claude-sonnet-5-5"
    assert "went to the market" in seen["messages"][0]["content"]
    assert "I went to the market." in text and "— Market trip" in text


# --- local web UI ---------------------------------------------------------------------------------


def ui_client(cfg, store, host="testclient"):
    from orbit_notes.ui import mount_ui

    app = create_app(cfg, store)
    queued = []
    mount_ui(app, cfg, store, queued.append)
    c = TestClient(app, client=(host, 50000))
    c.queued = queued
    return c


def test_ui_lists_days_notes_and_transcripts(client, store, cfg):
    add_note(client, store, "2026-11-13T07:02:10+07:00", "hello there")
    add_note(client, store, "2026-11-14T08:00:00+07:00", "")
    ui = ui_client(cfg, store)
    assert ui.get("/ui/api/days").json() == [{"date": "2026-11-14", "count": 1}, {"date": "2026-11-13", "count": 1}]
    notes = ui.get("/ui/api/notes", params={"day": "2026-11-13"}).json()
    assert [(n["time"], n["transcript"]) for n in notes] == [("07:02", "hello there")]
    assert ui.get("/ui/api/notes", params={"day": "2026-11-14"}).json()[0]["transcript"] == ""
    assert ui.get(f"/ui/api/notes/{notes[0]['id']}/audio").content == b"2026-11-13T07:02:10+07:00"
    assert ui.get("/ui/api/status").json()["notes"] == 2


def test_ui_retranscribe_and_diary(client, store, cfg):
    add_note(client, store, "2026-11-13T07:02:10+07:00", "hello")
    ui = ui_client(cfg, store)
    note_id = ui.get("/ui/api/notes", params={"day": "2026-11-13"}).json()[0]["id"]
    assert ui.post(f"/ui/api/notes/{note_id}/transcribe").status_code == 200
    assert ui.queued == [note_id] and store.get(note_id).transcript_status == "pending"
    assert ui.get("/ui/api/diary/2026-11-13").json()["markdown"] is None
    built = ui.post("/ui/api/diary/2026-11-13").json()
    assert "# Friday, 13 November 2026" in built["markdown"]
    assert ui.get("/ui/api/diary/2026-11-13").json()["markdown"] == built["markdown"]
    assert ui.get("/ui/api/diary/13-11-2026").status_code == 422


def test_ui_is_local_only(client, store, cfg):
    lan = ui_client(cfg, store, host="192.168.1.24")
    for path in ("/ui/api/status", "/ui/api/days", "/ui/api/pairing.png", "/ui/", "/"):
        assert lan.get(path).status_code == 403, path
    # The watch API stays reachable from the LAN.
    assert lan.get("/v1/health", params={"nonce": "n"}).status_code == 200
    assert ui_client(cfg, store).get("/ui/api/pairing.png").headers["content-type"] == "image/png"


def test_ui_serves_vault_audio_only_inside_vault(client, store, cfg, tmp_path):
    cfg.vault_dir = tmp_path / "vault"
    (cfg.vault_dir / "Diary/audio").mkdir(parents=True)
    (cfg.vault_dir / "Diary/audio/a.m4a").write_bytes(b"m4a")
    (cfg.vault_dir / "secret.md").write_text("private")
    (tmp_path / "outside.m4a").write_bytes(b"x")
    ui = ui_client(cfg, store)
    r = ui.get("/ui/api/vault/Diary/audio/a.m4a")
    assert r.status_code == 200 and r.content == b"m4a" and r.headers["content-type"] == "audio/mp4"
    assert ui.get("/ui/api/vault/secret.md").status_code == 404  # not audio
    assert ui.get("/ui/api/vault/..%2Foutside.m4a").status_code == 404  # escapes the vault


FAKE_CODEX = """#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
assert args[0] == "exec" and "--ephemeral" in args and args[args.index("-s") + 1] == "read-only"
schema = json.load(open(args[args.index("--output-schema") + 1]))
assert schema["required"] == ["diary", "summaries", "todos"]
assert "[note 0] 07:02" in prompt and "buy rice" in prompt
out = {"diary": "I went to the market.", "summaries": [{"note": 0, "summary": "Market plan"}], "todos": ["Buy rice"]}
open(args[args.index("-o") + 1], "w").write(json.dumps(out))
"""


def test_diary_codex_provider(client, store, cfg, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "codex"
    fake.write_text(FAKE_CODEX)
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    cfg.llm_provider = "codex"
    add_note(client, store, "2026-11-13T07:02:10+07:00", "I need to buy rice at the market")
    text = diary.build(cfg, store, date(2026, 11, 13)).read_text()
    assert "I went to the market." in text
    assert "— Market plan" in text
    assert "## To-dos\n\n- [ ] Buy rice" in text


def test_diary_codex_missing_cli(client, store, cfg, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    cfg.llm_provider = "codex"
    add_note(client, store, "2026-11-13T07:02:10+07:00", "hello")
    with pytest.raises(RuntimeError, match="codex CLI not found"):
        diary.build(cfg, store, date(2026, 11, 13))


# --- talk (docs/talk.md) ----------------------------------------------------------------------------

import sqlite3
from datetime import datetime

from orbit_notes.store import SCHEMA
from orbit_notes.talk import Talker
from orbit_notes.transcribe import Worker

FAKE_TALK_CODEX = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
log = os.environ["FAKE_CODEX_LOG"]
calls = [json.loads(l) for l in open(log)] if os.path.exists(log) else []
open(log, "a").write(json.dumps({"args": args, "prompt": prompt, "cwd": os.getcwd()}) + "\\n")
if os.environ.get("FAKE_CODEX_FAIL"):
    print("boom", file=sys.stderr); sys.exit(3)
resume = args[1] == "resume"
sid = args[-2] if resume else "sess-%d" % sum(1 for c in calls if c["args"][1] != "resume")
print(json.dumps({"type": "thread.started", "thread_id": sid}))
print(json.dumps({"type": "turn.started"}))
reply = "- That sounds **like** a full day. Reply %d." % (len(calls) + 1)
print(json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": reply}}))
open(args[args.index("-o") + 1], "w").write(reply)
"""


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text(FAKE_TALK_CODEX)
    (bin_dir / "codex").chmod(0o755)
    log = tmp_path / "codex-calls.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    return lambda: [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def fake_tts(spoken):
    def tts(text, out):
        spoken.append(text)
        out.write_bytes(b"RIFF fake wav")
    return tts


def add_talk(client, store, started_at, text=None):
    meta, data = make_note(started_at=started_at, audio=b"talk " + started_at.encode(), mode="talk")
    assert put(client, meta, data, filename="x").status_code == 201
    note = store.get(meta["id"])
    if text is not None:
        store.audio_path(note).with_suffix(".txt").write_text(text + "\n")
        store.set_transcript_status(note.id, "done")
    return store.get(meta["id"])


def test_migration_adds_talk_columns_to_existing_db(tmp_path):
    old = SCHEMA.split("    mode ")[0].rstrip().rstrip(",") + "\n);\n"  # the v1 table, before talk
    db = sqlite3.connect(tmp_path / "index.sqlite")
    db.executescript(old)
    db.execute("INSERT INTO notes (id, path, sha256, started_at, started_utc, local_date, received_at)"
               " VALUES ('a', 'notes/x.aac', 'ff', '2026-11-13T07:00:00+07:00', '2026-11-13T00:00:00+00:00',"
               " '2026-11-13', 'now')")
    db.commit()
    db.close()
    store = Store(tmp_path)
    note = store.get("a")
    assert note.mode is None and note.talk_status is None and note.transcript_status == "pending"
    Store(tmp_path)  # opening twice is a no-op
    store.set_talk_session(date(2026, 11, 13), "s1")
    assert store.talk_session(date(2026, 11, 13)) == "s1"


def test_talk_mode_stored_in_index(client, store):
    talk = add_talk(client, store, "2026-11-13T07:02:10+07:00")
    plain = add_note(client, store, "2026-11-13T08:00:00+07:00")
    assert (talk.mode, talk.talk_status) == ("talk", "queued")
    assert (plain.mode, plain.talk_status) == (None, None)


def test_reply_endpoint_states(client, store):
    assert client.get(f"/v1/notes/{uuid.uuid4()}/reply", headers=AUTH).status_code == 404
    plain = add_note(client, store, "2026-11-13T06:00:00+07:00", "plain")
    assert client.get(f"/v1/notes/{plain.id}/reply", headers=AUTH).status_code == 404  # not a talk note

    note = add_talk(client, store, "2026-11-13T07:02:10+07:00", "I walked by the river")
    url = f"/v1/notes/{note.id}/reply"
    assert client.get(url).status_code == 401
    assert client.get(url + ".wav").status_code == 401
    r = client.get(url, headers=AUTH)
    assert (r.status_code, r.json()) == (202, {"status": "queued"})
    for status in ("transcribing", "thinking", "speaking"):
        store.set_talk_status(note.id, status)
        r = client.get(url, headers=AUTH)
        assert (r.status_code, r.json()) == (202, {"status": status})
    assert client.get(url + ".wav", headers=AUTH).status_code == 404  # no audio yet

    store.set_talk_status(note.id, "error", "codex exec failed (1)")
    r = client.get(url, headers=AUTH)
    assert (r.status_code, r.json()) == (200, {"status": "error", "error": "codex exec failed (1)"})

    audio = store.audio_path(note)
    audio.with_name(f"{note.stem}.reply.txt").write_text("What a calm walk.\n")
    audio.with_name(f"{note.stem}.reply.wav").write_bytes(b"RIFFwav")
    store.set_talk_status(note.id, "done")
    r = client.get(url, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"status": "done", "text": "What a calm walk.", "audio": f"/v1/notes/{note.id}/reply.wav",
                        "transcript": "I walked by the river"}
    w = client.get(url + ".wav", headers=AUTH)
    assert w.status_code == 200 and w.content == b"RIFFwav" and w.headers["content-type"] == "audio/wav"


def test_talk_one_codex_session_per_day(client, store, cfg, fake_codex):
    spoken = []
    talker = Talker(cfg, store, tts=fake_tts(spoken))
    (cfg.diary_dir).mkdir(parents=True)
    (cfg.diary_dir / "2026-11-12.md").write_text(
        "---\ndate: 2026-11-12\n---\n<!-- orbit:begin -->\n# Thursday\n\nI planted tomatoes.\n\n"
        "## Transcripts\n\n> raw stuff\n<!-- orbit:end -->\n")
    add_note(client, store, "2026-11-13T06:30:00+07:00", "Woke up early, coffee.")
    first = add_talk(client, store, "2026-11-13T07:02:10+07:00", "Feeling a bit tired today")
    second = add_talk(client, store, "2026-11-13T21:00:00+07:00", "The day went better than expected")
    next_day = add_talk(client, store, "2026-11-14T08:00:00+07:00", "New day")

    assert talker.run(first) and talker.run(second) and talker.run(next_day)
    calls = fake_codex()
    assert len(calls) == 3

    a = calls[0]["args"]
    assert a[:2] == ["exec", "--json"] and "resume" not in a
    for flag in ("--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"):
        assert flag in a
    assert a[a.index("-s") + 1] == "read-only" and "model_reasoning_effort=low" in a
    assert a[a.index("-C") + 1] == str(cfg.data_dir / "talk") and calls[0]["cwd"] == str(cfg.data_dir / "talk")
    p = calls[0]["prompt"]
    assert "journaling companion" in p and "I planted tomatoes." in p and "raw stuff" not in p
    assert "[06:30] Woke up early, coffee." in p and "Feeling a bit tired today" in p

    b = calls[1]["args"]
    assert b[:3] == ["exec", "resume", "--json"] and b[-2:] == ["sess-0", "-"]
    assert 'sandbox_mode="read-only"' in b and "--ignore-user-config" in b
    assert "The day went better than expected" in calls[1]["prompt"] and "journaling companion" not in calls[1]["prompt"]

    c = calls[2]["args"]
    assert "resume" not in c  # a new local date starts a new session
    assert "There is no diary page for yesterday." in calls[2]["prompt"]  # no page for the 13th
    assert "Feeling a bit tired" not in calls[2]["prompt"]  # yesterday's notes are not "earlier today"
    assert store.talk_session(date(2026, 11, 13)) == "sess-0"
    assert store.talk_session(date(2026, 11, 14)) == "sess-1"

    # Reply files, spoken text without markdown, status done.
    txt = store.audio_path(first).with_name(f"{first.stem}.reply.txt")
    assert txt.read_text() == "That sounds like a full day. Reply 1.\n"
    assert store.audio_path(first).with_name(f"{first.stem}.reply.wav").exists()
    assert spoken[0] == "That sounds like a full day. Reply 1."
    assert store.get(first.id).talk_status == "done"


def test_talk_codex_failure_keeps_note(client, store, cfg, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_FAIL", "1")
    note = add_talk(client, store, "2026-11-13T07:02:10+07:00", "hello")
    assert not Talker(cfg, store, tts=fake_tts([])).run(note)
    n = store.get(note.id)
    assert n.talk_status == "error" and "codex exec failed (3)" in n.talk_error and "boom" in n.talk_error
    assert store.audio_path(n).exists()
    r = client.get(f"/v1/notes/{note.id}/reply", headers=AUTH)
    assert r.status_code == 200 and r.json()["status"] == "error"


def test_talk_no_speech_is_an_error_without_codex(client, store, cfg, fake_codex):
    note = add_talk(client, store, "2026-11-13T07:02:10+07:00", "")
    assert not Talker(cfg, store, tts=fake_tts([])).run(note)
    assert store.get(note.id).talk_error == "no speech heard in the recording"
    assert fake_codex() == []


class FakeTranscriber:
    def __init__(self, store):
        self.store, self.order = store, []

    def run(self, store, note):
        self.order.append(note.id)
        store.audio_path(note).with_suffix(".txt").write_text(f"words of {note.id}\n")
        store.set_transcript_status(note.id, "done")
        return True


def now_iso():
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def test_worker_talk_jumps_queue_and_gets_feedback(client, store, cfg, fake_codex):
    plain = [add_note(client, store, f"2026-11-1{i}T08:00:00+07:00") for i in range(3)]
    talk = add_talk(client, store, now_iso())
    stale = add_talk(client, store, "2026-11-01T08:00:00+07:00")  # uploaded on a later day: no feedback
    tr = FakeTranscriber(store)
    w = Worker(store, tr, Talker(cfg, store, tts=fake_tts([])))
    w.start()  # queues everything pending; talk first
    w.stop()
    w.thread.join(timeout=30)
    assert tr.order[0] == talk.id
    assert set(tr.order) == {talk.id, stale.id, *(n.id for n in plain)}  # all still transcribed
    assert store.get(talk.id).talk_status == "done"
    s = store.get(stale.id)
    assert s.talk_status == "error" and "earlier day" in s.talk_error
    assert len(fake_codex()) == 1


def test_worker_marks_talk_error_when_transcription_fails(client, store, cfg, fake_codex):
    talk = add_talk(client, store, now_iso())

    class Failing:
        def run(self, store, note):
            store.set_transcript_status(note.id, "failed", "bad audio")
            return False

    w = Worker(store, Failing(), Talker(cfg, store, tts=fake_tts([])))
    w.start()
    w.stop()
    w.thread.join(timeout=30)
    assert store.get(talk.id).talk_status == "error" and fake_codex() == []


def test_diary_shows_feedback_in_talk_callout(client, store, cfg):
    note = add_talk(client, store, "2026-11-13T07:02:10+07:00", "Feeling tired")
    store.audio_path(note).with_name(f"{note.stem}.reply.txt").write_text("Rest sounds good.\nTake it slow.\n")
    plain = add_note(client, store, "2026-11-13T08:00:00+07:00", "plain note")
    store.audio_path(plain).with_name(f"{plain.stem}.reply.txt").write_text("ignored: not a talk note\n")
    text = diary.build(cfg, store, date(2026, 11, 13)).read_text()
    assert "> Feeling tired\n>\n> **Feedback:** Rest sounds good. Take it slow.\n" in text
    assert "ignored" not in text


def test_ui_shows_talk_reply(client, store, cfg):
    note = add_talk(client, store, "2026-11-13T07:02:10+07:00", "hi")
    store.audio_path(note).with_name(f"{note.stem}.reply.txt").write_text("Hello.\n")
    store.audio_path(note).with_name(f"{note.stem}.reply.wav").write_bytes(b"RIFF")
    store.set_talk_status(note.id, "done")
    ui = ui_client(cfg, store)
    n = ui.get("/ui/api/notes", params={"day": "2026-11-13"}).json()[0]
    assert (n["mode"], n["talk_status"], n["reply"], n["reply_audio"]) == ("talk", "done", "Hello.", True)
    assert ui.get(f"/ui/api/notes/{note.id}/reply.wav").content == b"RIFF"
