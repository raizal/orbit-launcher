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
