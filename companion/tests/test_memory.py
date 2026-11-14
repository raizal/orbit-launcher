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


# --- recall in Talk (step 2) -------------------------------------------------------------------------------

from test_workspace import answer, bb, codex, repo, talk  # noqa: E402,F401 (fixtures)


def prompt_of(call):
    return call["prompt"]


def test_recall_question_gets_dated_hits(talk, codex, client, store, cfg):
    add_note(client, store, "2026-11-03T21:14:00+07:00", "The database migration to Postgres went fine today.")
    add_note(client, store, "2026-11-04T09:00:00+07:00", "Bought new running shoes.")
    Memory(cfg, store).sync()
    codex.script(answer("On Tuesday 3 November you said the migration went fine."))
    talk("What did I say about the migration?")
    p = prompt_of(codex.calls()[0])
    assert "MEMORY (retrieved by the companion" in p
    assert "He is asking about the past." in p
    assert '- Tue 03 Nov 2026 21:14, voice note: "The database migration to Postgres went fine today."' in p
    assert "running shoes" not in p.split("MEMORY (retrieved")[1]
    assert "never invent or guess a memory" in p  # the recall rules go with every turn


def test_recall_without_a_match_says_so(talk, codex, client, store, cfg):
    add_note(client, store, "2026-11-03T21:14:00+07:00", "The database migration to Postgres went fine today.")
    Memory(cfg, store).sync()
    codex.script(answer("I don't have any memory of that."))
    talk("Do you remember what I said about my sister's wedding?")
    p = prompt_of(codex.calls()[0])
    # the fake embedder still ranks *something*; the instruction makes Codex judge relevance
    assert "a search hit is NOT necessarily about what he asks" in p or "NOTHING in his notes matched" in p


def test_everyday_turn_uses_keyword_hits_and_skips_this_conversation(talk, codex, client, store, cfg):
    add_note(client, store, "2026-11-03T21:14:00+07:00", "Dewi said the Bandung office moves in December.")
    Memory(cfg, store).sync()
    codex.script(answer("Nice."), answer("Okay."))
    talk("Had coffee with Dewi this morning.")
    p = prompt_of(codex.calls()[0])
    assert "Earlier notes sharing words with this voice note" in p and "Bandung office" in p
    Memory(cfg, store).sync()  # in `serve` the worker indexes each turn after it was answered
    talk("Dewi again, she brought cake.")
    p2 = prompt_of(codex.calls()[1])
    assert "Had coffee with Dewi" not in p2.split("MEMORY (retrieved")[1]  # same conversation: already in context


def test_detail_round_recall_search(talk, codex, client, store, cfg):
    add_note(client, store, "2026-11-03T21:14:00+07:00", "The database migration to Postgres went fine today.")
    Memory(cfg, store).sync()
    codex.script(answer("Let me check.", need={"recall": [{"query": "Postgres", "from": "2026-11-01", "to": "2026-11-05"},
                                                           {"query": "wedding", "from": "", "to": ""}]}),
                 answer("On 3 November you said the migration to Postgres went fine."))
    reply, _ = talk("Tell me about the database thing.")
    assert reply == "On 3 November you said the migration to Postgres went fine."
    p = prompt_of(codex.calls()[1])
    assert 'RECALL SEARCH "Postgres" between 2026-11-01 and 2026-11-05:' in p
    assert '- Tue 03 Nov 2026 21:14, voice note: "The database migration to Postgres went fine today."' in p
    assert 'RECALL SEARCH "wedding":' in p


def test_turn_schema_has_recall():
    from orbit_notes.talk import OUTPUT_SCHEMA

    nd = OUTPUT_SCHEMA["properties"]["need_details"]
    assert "recall" in nd["required"] and nd["properties"]["recall"]["items"]["required"] == ["query", "from", "to"]


def test_memory_failure_never_costs_the_turn(talk, codex, monkeypatch):
    monkeypatch.setattr(Memory, "turn_evidence", lambda *a: 1 / 0)
    codex.script(answer("Okay."))
    reply, _ = talk("What did I say yesterday?")
    assert reply == "Okay."
    assert "MEMORY: could not be searched right now" in prompt_of(codex.calls()[0])


# --- facts, corrections, forget, views (step 3) -------------------------------------------------------------


def fact(text, topic, kind="fact", confidence="high", relation="new", fact_id=""):
    return {"text": text, "topic": topic, "kind": kind, "confidence": confidence, "relation": relation,
            "fact_id": fact_id}


def remembered(reply="Okay.", facts=(), tags=(), **kw):
    a = answer(reply, **kw)
    a["memory"] = {"facts": list(facts), "tags": list(tags)}
    return a


def active(cfg, store):
    return {f.text: f for f in Memory(cfg, store).facts()}


def test_turn_facts_are_stored_with_provenance_and_the_view_is_written(talk, codex, cfg, store):
    codex.script(remembered("What a lovely cat.", [fact("His cat is called Mocha.", "Mocha", "person"),
                                                   fact("He might like tea.", "tea", confidence="low")],
                            [{"kind": "person", "name": "Mocha"}, {"kind": "topic", "name": "cats"}]))
    talk("My cat Mocha slept on my laptop again.")
    facts = active(cfg, store)
    assert list(facts) == ["His cat is called Mocha."]  # low confidence: dropped
    f = facts["His cat is called Mocha."]
    turn_note = store.get(f.sources[0]["note_id"])
    assert turn_note.is_talk and f.first_seen == turn_note.local_date and f.sources[0]["via"] == "talk"
    view = (cfg.diary_dir.parent / "Orbit" / "Memory.md").read_text()
    assert "> Generated by Orbit — edit at http://localhost:8765/ui/" in view
    assert f"## [[Mocha]]\n\n- His cat is called Mocha. _(person)_ — [[{turn_note.local_date}]]" in view
    assert Memory(cfg, store).day_links(date.fromisoformat(turn_note.local_date)) == ["Mocha", "cats"]


def test_known_facts_go_into_every_turn_and_confirm_or_replace(talk, codex, cfg, store):
    fid = Memory(cfg, store).add_fact("He lives in Jakarta.", "Jakarta", "place").id
    codex.script(remembered("Noted.", [fact("He lives in Jakarta.", "Jakarta", "place")]),  # same sentence -> confirms
                 remembered("Big move!", [fact("He lives in Bandung.", "home", "place", relation="replaces",
                                               fact_id=fid)]))
    talk("Still in Jakarta for now.")
    assert len(Memory(cfg, store).facts()) == 1
    assert Memory(cfg, store).fact(fid).history[-1]["event"] == "confirmed"
    talk("We moved to Bandung last weekend!")
    p = codex.calls()[-1]["prompt"]
    assert f"- {fid} — He lives in Jakarta. [Jakarta, place; first heard" in p  # known facts with ids + dates
    old = Memory(cfg, store).fact(fid)
    assert old.status == "retired" and old.history[-1]["event"] == "replaced"
    new = active(cfg, store)["He lives in Bandung."]
    assert new.history[0]["replaces"] == fid


def test_unsure_replacement_is_not_applied(talk, codex, cfg, store):
    fid = Memory(cfg, store).add_fact("He lives in Jakarta.", "Jakarta", "place").id
    codex.script(remembered("Are you moving?", [fact("He lives in Bandung.", "home", "place", confidence="medium",
                                                     relation="replaces", fact_id=fid)]))
    talk("Maybe Bandung soon.")
    assert list(active(cfg, store)) == ["He lives in Jakarta."]


def test_voice_correction_needs_a_yes(talk, codex, cfg, store):
    fid = Memory(cfg, store).add_fact("His cat is called Mocha.", "cat", "person").id
    codex.script(remembered("You want me to correct your cat's name to Mochi. Shall I?", kind="memory_fact",
                            summary="Correct your cat's name to Mochi",
                            params={"op": "correct", "fact_id": fid, "text": "His cat is called Mochi."}),
                 remembered("Okay.", confirmation="confirm"),
                 remembered("Mochi, got it."))
    reply, _ = talk("That's wrong, my cat is Mochi not Mocha.")
    assert "Shall I?" in reply
    assert list(active(cfg, store)) == ["His cat is called Mocha."]  # nothing changed before the yes
    from orbit_notes.actions import ActionLog
    pending = ActionLog(cfg.data_dir).pending()
    assert pending.kind == "memory_fact" and "replacing “His cat is called Mocha.” with “His cat is called Mochi.”" \
        in pending.summary
    reply, _ = talk("Yes.")
    assert reply == "Okay. Done: I corrected it. I now remember: His cat is called Mochi."
    assert list(active(cfg, store)) == ["His cat is called Mochi."]
    old = Memory(cfg, store).fact(fid)
    assert old.status == "corrected" and old.history[-1]["by"] == active(cfg, store)["His cat is called Mochi."].id
    talk("How is Mochi?")
    p = codex.calls()[-1]["prompt"].split("MEMORY (retrieved")[1]
    assert "His cat is called Mochi." in p and "Mocha." not in p
    assert "Mochi" in (cfg.diary_dir.parent / "Orbit" / "Memory.md").read_text()


def test_voice_correction_of_unknown_fact_is_rejected(talk, codex, cfg, store):
    codex.script(remembered("I'll fix it.", kind="memory_fact", summary="Correct the fact",
                            params={"op": "correct", "fact_id": "f-abcdef", "text": "His cat is Mochi."}))
    reply, _ = talk("That's wrong.")
    assert reply == "I understood that you want me to correct the fact. But I can't: I have no current fact " \
                    "f-abcdef. Nothing was changed."


def test_forget_by_voice_retires_facts_and_hides_notes(talk, codex, client, cfg, store):
    add_note(client, store, "2026-11-03T21:14:00+07:00", "Lunch with Rina at the mall, she talked about Budi.")
    add_note(client, store, "2026-11-04T21:14:00+07:00", "Long run in the park.")
    Memory(cfg, store).sync()
    Memory(cfg, store).add_fact("Rina is his former colleague.", "Rina", "person")
    codex.script(remembered("You want me to forget Rina. Shall I?", kind="memory_forget", summary="Forget Rina",
                            params={"query": "Rina"}),
                 remembered("Okay.", confirmation="confirm"))
    reply, _ = talk("Please forget everything about Rina.")
    from orbit_notes.actions import ActionLog
    assert "(forgets 1 fact: “Rina is his former colleague.”, and 1 note excerpt hidden from recall; the recordings " \
           "themselves stay)" in ActionLog(cfg.data_dir).pending().summary
    reply, _ = talk("Yes, do it.")
    assert "Done: I forgot “Rina”: 1 fact retired and 1 note excerpt hidden from recall." in reply
    m = Memory(cfg, store)
    assert m.facts() == [] and m.search("Rina mall lunch", keyword_only=False) == [] or \
        all("Rina" not in h.text for h in m.search("Rina mall lunch"))
    assert m.search("run park")  # other notes unaffected
    later = add_note(client, store, "2026-11-05T21:14:00+07:00", "Rina called again.")
    m.index_note(later)
    assert all(h.note_id != later.id for h in m.search("Rina called"))
    assert store.audio_path(later).with_suffix(".txt").exists()  # raw notes are never deleted


def test_diary_build_distills_facts_tags_and_links(client, store, cfg, monkeypatch):
    a = add_note(client, store, "2026-11-13T07:02:10+07:00", "Coffee with Dewi, she starts at Gojek next month.")
    b = add_note(client, store, "2026-11-13T19:00:00+07:00", "I want to run three times a week.")
    Memory(cfg, store).add_fact("Dewi is his sister.", "Dewi", "person")
    seen = {}

    def fake(cfg_, day, entries):
        seen["system"], _ = diary.prompt_parts(cfg_, day, entries)
        return diary.from_json({
            "diary": "Coffee with Dewi. I want to run more.",
            "summaries": [{"note": 0, "summary": "Coffee with Dewi", "mood": None,
                           "tags": [{"kind": "person", "name": "Dewi"}, {"kind": "place", "name": "Gojek"}]},
                          {"note": 1, "summary": "Running goal", "mood": None,
                           "tags": [{"kind": "topic", "name": "running"}]}],
            "todos": [],
            "facts": [{**fact("He wants to run three times a week.", "running", "goal"), "note": 1}],
        }, entries)

    monkeypatch.setitem(diary.PROVIDERS, "fake", fake)
    cfg.llm_provider = "fake"
    page = diary.build(cfg, store, date(2026, 11, 13)).read_text()
    assert "Known facts (id: fact [topic]):\n- f-" in seen["system"] and "Dewi is his sister. [Dewi]" in seen["system"]
    assert "**Mentions:** [[Dewi]] · [[Gojek]] · [[running]]" in page
    goal = active(cfg, store)["He wants to run three times a week."]
    assert goal.kind == "goal" and goal.sources == [{"note_id": b.id, "local_date": "2026-11-13", "via": "diary"}]
    m = Memory(cfg, store)
    assert m.search("Coffee Dewi")[0].source in ("diary", "note")  # the page prose is indexed too
    assert {h.note_id for h in m.search("Dewi", tag="Gojek")} <= {a.id, None}
    # rebuild keeps one set of diary tags per note, links unchanged
    page2 = diary.build(cfg, store, date(2026, 11, 13)).read_text()
    assert page2.count("**Mentions:**") == 1 and len(m.facts()) == 2


def test_memory_web_api(client, store, cfg):
    c = ui_client(cfg, store)
    r = c.post("/ui/api/memory/facts", json={"text": "His cat is called Mochi.", "topic": "Mochi", "kind": "person"})
    assert r.status_code == 200
    fid = r.json()["id"]
    assert c.post("/ui/api/memory/facts", json={"text": "x", "topic": "y"}).status_code == 422
    r = c.put(f"/ui/api/memory/facts/{fid}", json={"text": "His cat is called Mochi, a ginger tabby."})
    assert r.json()["history"][-1]["before"] == {"text": "His cat is called Mochi."}
    assert c.put(f"/ui/api/memory/facts/{fid}", json={"status": "gone"}).status_code == 422
    assert c.put("/ui/api/memory/facts/f-000000", json={"text": "abcdef"}).status_code == 404
    assert c.put(f"/ui/api/memory/facts/{fid}", json={"status": "retired"}).json()["status"] == "retired"
    assert c.get("/ui/api/memory/facts").json()["facts"] == []
    assert len(c.get("/ui/api/memory/facts?status=all").json()["facts"]) == 1
    assert c.delete(f"/ui/api/memory/facts/{fid}").status_code == 200
    assert c.get("/ui/api/memory/facts?status=all").json()["facts"] == []
    assert "Nothing yet" in (cfg.diary_dir.parent / "Orbit" / "Memory.md").read_text()

    add_note(client, store, "2026-11-03T21:14:00+07:00", "Lunch with Rina.")
    Memory(cfg, store).sync()
    assert c.post("/ui/api/memory/forget", json={"query": ""}).status_code == 422
    assert c.post("/ui/api/memory/forget", json={"query": "rina", "preview": True}).json() == {"facts": [], "chunks": 1}
    assert c.post("/ui/api/memory/forget", json={"query": "rina"}).json() == {"facts": 0, "chunks": 1}
    assert c.get("/ui/api/memory/search?q=rina").json()["hits"] == []
    entry = c.get("/ui/api/memory/forgotten").json()["forgotten"][0]
    assert entry["term"] == "rina"
    assert c.delete(f"/ui/api/memory/forgotten/{entry['id']}").status_code == 200
    assert len(c.get("/ui/api/memory/search?q=rina").json()["hits"]) == 1
    assert ui_client(cfg, store, host="10.0.0.5").get("/ui/api/memory/facts").status_code == 403


def test_turn_schema_has_memory_and_new_kinds():
    from orbit_notes.talk import OUTPUT_SCHEMA, PARAMS_HELP, RULES

    assert "memory" in OUTPUT_SCHEMA["required"]
    assert OUTPUT_SCHEMA["properties"]["memory"]["properties"]["facts"]["items"]["required"] == \
        ["text", "topic", "kind", "confidence", "relation", "fact_id"]
    assert {"memory_fact", "memory_forget"} <= set(OUTPUT_SCHEMA["properties"]["proposed_action"]["properties"]
                                                   ["kind"]["enum"])
    assert '- memory_forget: {"query"' in PARAMS_HELP and "memory_fact and memory_forget" in RULES
