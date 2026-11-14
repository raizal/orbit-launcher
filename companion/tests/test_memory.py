"""Memory & recall (docs/memory.md): episodic index, hybrid search, recall in Talk, facts, corrections, views.
The embedder is the fake from conftest.py (hashed bag of words), so no model is downloaded."""

import json
from datetime import date, datetime, timedelta

import pytest

from orbit_notes import diary, insights, memory
from orbit_notes.memory import Memory, chunk_text, content_words, parse_range
from test_companion import add_note, add_talk, cfg, client, store, ui_client  # noqa: F401 (fixtures)


@pytest.fixture
def mem(cfg, store):
    return Memory(cfg, store)


# --- text helpers -------------------------------------------------------------------------------------


def test_chunking_keeps_short_notes_whole_and_splits_long_ones():
    assert chunk_text("  Short note.  Two sentences. ") == ["Short note. Two sentences."]
    long = " ".join(f"This is sentence number {i} about the launcher and the watch face." for i in range(30))
    pieces = chunk_text(long)
    assert len(pieces) > 3 and all(len(p) <= 2 * memory.CHUNK_TARGET for p in pieces)
    assert " ".join(pieces) == " ".join(long.split())
    runon = "kata " * 400  # no punctuation at all (speech recognition)
    assert all(len(p) <= 2 * memory.CHUNK_TARGET for p in chunk_text(runon))


def test_content_words_drop_question_and_date_words_in_both_languages():
    assert content_words("What did I say about the migration last week?") == ["migration"]
    assert content_words("apa yang aku bilang soal kucing kemarin?") == ["kucing"]


@pytest.mark.parametrize("text, expected", [
    ("what did I say yesterday", (date(2026, 11, 14), date(2026, 11, 14))),
    ("apa yang aku bilang kemarin", (date(2026, 11, 14), date(2026, 11, 14))),
    ("what did I say about the migration last week", (date(2026, 11, 2), date(2026, 11, 8))),
    ("minggu lalu aku ngomong apa", (date(2026, 11, 2), date(2026, 11, 8))),
    ("what did I say 3 days ago", (date(2026, 11, 12), date(2026, 11, 12))),
    ("in the last 10 days", (date(2026, 11, 5), date(2026, 11, 15))),
    ("what did I say in October", (date(2026, 10, 1), date(2026, 10, 31))),
    ("on Monday", (date(2026, 11, 9), date(2026, 11, 9))),
    ("do you remember my cat", None),
])
def test_parse_range(text, expected):
    assert parse_range(text, date(2026, 11, 15)) == expected  # a Sunday


# --- index ----------------------------------------------------------------------------------------------


def test_index_is_incremental_and_search_is_hybrid(client, store, mem):
    a = add_note(client, store, "2026-11-03T21:14:00+07:00", "The database migration to Postgres went fine today.")
    b = add_note(client, store, "2026-11-10T08:00:00+07:00", "Kucingku Mochi tidur di atas laptop lagi.")
    add_note(client, store, "2026-11-11T08:00:00+07:00", "Lunch with Dewi near the office.")
    assert mem.sync() == 12  # per note: transcript + reply; per day: diary page + reflection (empty ones too)
    assert mem.sync() == 0  # unchanged: nothing re-chunked
    assert mem.embed_pending() == 3 and mem.embed_pending() == 0

    hits = mem.search("what did I say about the migration?")
    assert hits[0].note_id == a.id and hits[0].keyword
    assert hits[0].when() == "Tue 03 Nov 2026 21:14" and hits[0].label() == "voice note"
    # meaning only (no shared word): "cat" ~ "kucing" in the fake embedder
    hits = mem.search("my cat", keyword_only=False)
    assert hits[0].note_id == b.id and not hits[0].keyword
    assert mem.search("my cat", keyword_only=True) == []
    assert mem.search("migration", date_from=date(2026, 11, 4)) == [] or \
        all(h.local_date >= "2026-11-04" for h in mem.search("migration", date_from=date(2026, 11, 4)))
    assert all(h.note_id != a.id for h in mem.search("migration", exclude_notes={a.id}))

    # a re-transcription replaces the note's chunks
    store.audio_path(a).with_suffix(".txt").write_text("Now it is about the watch face.\n")
    assert mem.index_note(store.get(a.id)) == 1
    assert not [h for h in mem.search("migration") if h.note_id == a.id]
    assert mem.embed_pending() == 1


def test_talk_replies_and_diary_pages_are_indexed(client, store, cfg, mem):
    n = add_talk(client, store, "2026-11-14T21:44:00+07:00", "Will the server work in Thailand next week?")
    store.audio_path(n).with_name(f"{n.stem}.reply.txt").write_text("It should rediscover the watch on a new network.\n")
    page = cfg.diary_dir / "2026-11-14.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"---\ndate: 2026-11-14\n---\n{diary.BEGIN}\n# Saturday, 14 November 2026\n\nI am going to "
                    f"Thailand next week.\n\n**Mentions:** [[Thailand]]\n\n## Notes\n- 21:44 · x — Thailand\n\n"
                    f"## Transcripts\n\n> quote\n{diary.END}\n")
    insights.reflection_path(cfg, date(2026, 11, 14)).parent.mkdir(parents=True, exist_ok=True)
    insights.reflection_path(cfg, date(2026, 11, 14)).write_text("A calm day of testing.")
    mem.sync()
    sources = {h.source: h for h in mem.search("Thailand network", limit=10)}
    assert set(sources) == {"note", "reply", "diary"}
    assert sources["diary"].text == "I am going to Thailand next week."  # prose only, no headings / links
    assert sources["diary"].when() == "Sat 14 Nov 2026" and sources["note"].label() == "talk turn, his words"
    assert mem.search("calm testing")[0].source == "reflection"
    assert [h.source for h in mem.in_range(date(2026, 11, 14), date(2026, 11, 14))][:2] == ["diary", "reflection"]


def test_worker_indexes_after_transcription(client, store, cfg):
    from orbit_notes.transcribe import Worker

    class FakeTranscriber:
        def run(self, store_, note):
            store_.audio_path(note).with_suffix(".txt").write_text("Planning the Bali trip with Sari.\n")
            store_.set_transcript_status(note.id, "done")
            return True

    m = Memory(cfg, store)
    w = Worker(store, FakeTranscriber(), None, m)
    note = add_note(client, store, "2026-11-12T10:00:00+07:00")
    w.start()
    w.enqueue(note.id)
    w.stop()
    w.thread.join(5)
    m._thread.join(0.1)
    for _ in range(50):  # the memory thread runs at low priority
        if m.search("Bali"):
            break
        import time
        time.sleep(0.05)
    assert m.search("Bali")[0].note_id == note.id
    m.stop()


def test_memory_search_api_is_loopback_only(client, store, cfg):
    add_note(client, store, "2026-11-03T21:14:00+07:00", "Migration plan for the companion database.")
    Memory(cfg, store).sync()  # in `serve` the background indexer does this
    c = ui_client(cfg, store)
    r = c.get("/ui/api/memory/search", params={"q": "migration"})
    assert r.status_code == 200
    hit = r.json()["hits"][0]
    assert hit["text"].startswith("Migration plan") and hit["when"] == "Tue 03 Nov 2026 21:14" and hit["keyword"]
    rng = c.get("/ui/api/memory/search", params={"from": "2026-11-03", "to": "2026-11-03"}).json()
    assert len(rng["hits"]) == 1
    assert c.get("/ui/api/memory/search", params={"q": "x", "from": "nope"}).status_code == 422
    st = c.get("/ui/api/memory/status").json()
    assert st["chunks"] == 1 and st["model"] == "fake"
    assert ui_client(cfg, store, host="192.168.1.20").get("/ui/api/memory/search?q=x").status_code == 403
