"""Talk capabilities on request: morning briefing, evening reflection (saved after a spoken yes), quiet mood
tracking (docs/talk.md -> "Briefing, reflection, mood"). Fake `bb` + fake `codex` on PATH, as in test_workspace."""

import json
from datetime import date, datetime, timedelta

import pytest

from orbit_notes import diary, insights
from orbit_notes.actions import ActionLog
from orbit_notes.insights import MoodLog
from orbit_notes.store import Note
from orbit_notes.talk import CONFIRM_ASK
from test_companion import add_note, add_talk, cfg, client, store, ui_client  # noqa: F401 (fixtures)
from test_workspace import answer, bb, codex, repo, talk, writes  # noqa: F401 (fixtures)


def mood(m=0, e=3, conf="medium", why="sounds ordinary"):
    return {"mood": m, "energy": e, "confidence": conf, "rationale": why}


def say(reply="Okay.", *, m=None, **kw):
    a = answer(reply, **kw)
    a["need_details"].update(briefing=False, reflection=False, mood_days=0)
    for k in ("briefing", "reflection", "mood_days"):
        if k in (kw.get("need") or {}):
            a["need_details"][k] = kw["need"][k]
    if m is not None:
        a["mood"] = m
    return a


def now_local():
    return datetime.now().astimezone().replace(microsecond=0)


def today():
    return (now_local() - timedelta(minutes=59)).date()  # the talk fixture's notes start ~1 h ago


def iso(minutes_ago):
    return (now_local() - timedelta(minutes=minutes_ago)).isoformat()


# --- triggers ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("Give me my morning briefing", {"briefing": True}),
    ("What's on today?", {"briefing": True}),
    ("Let's reflect on my day", {"reflection": True}),
    ("How's my mood been this week?", {"mood_days": 7}),
    ("how has my energy been this month", {"mood_days": 30}),
    ("what was my mood over the last 10 days", {"mood_days": 10}),
    ("I'm in a bad mood, the build broke again", {}),  # about mood, not a question about the history
    ("I had lunch with Dewi", {}),
])
def test_triggers(text, expected):
    assert insights.triggers(text) == expected


def test_reflection_day_rolls_over_before_4am():
    def note(at):
        return Note("x", "p", "s", at, at[:10], 1, "done", mode="talk")
    assert insights.reflection_day(note("2026-11-14T23:10:00+07:00")) == date(2026, 11, 14)
    assert insights.reflection_day(note("2026-11-15T01:30:00+07:00")) == date(2026, 11, 14)
    assert insights.reflection_day(note("2026-11-15T06:00:00+07:00")) == date(2026, 11, 15)


# --- briefing ----------------------------------------------------------------------------------------


@pytest.fixture
def briefing_world(cfg, store, client, bb, tmp_path):
    """Yesterday's page with one open and one ticked to-do; a task due today; a note from earlier today."""
    y = today() - timedelta(days=1)
    page = cfg.diary_dir / f"{y.isoformat()}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"---\ndate: {y}\n---\n{diary.BEGIN}\n# Day\n\nText.\n\n## Notes\n- x\n\n## To-dos\n\n"
                    f"- [ ] Call the bank about the card\n- [x] Buy rice\n\n## Transcripts\n\n{diary.END}\n")
    data = json.loads((tmp_path / "bb.json").read_text())
    data["tasks"].append({"key": "ORB-7", "title": "Release build", "status": "todo", "priority": "high",
                          "dueDate": today().isoformat(), "updatedAt": "2026-11-01T00:00:00Z"})
    (tmp_path / "bb.json").write_text(json.dumps(data))
    add_note(client, store, iso(80), "Remember the dentist at four")


def test_briefing_on_request_gets_evidence_in_the_first_call(talk, codex, bb, briefing_world):
    codex.script(say("Good morning. Call the bank, the release build is due today...", m=mood(1, 4)))
    reply, _ = talk("Give me my morning briefing")
    assert reply.startswith("Good morning.")
    calls = codex.calls()
    assert len(calls) == 1  # no detail round needed
    p = calls[0]["prompt"]
    ev = p[p.index("BRIEFING EVIDENCE gathered"):p.index("VOICE NOTE (")]
    assert "Weather: NOT AVAILABLE — the companion has no location" in ev
    assert "- Call the bank about the card" in ev and "Buy rice" not in ev  # ticked in Obsidian = done
    assert 'ORB-7 "Release build" — due today' in ev and 'ORB-1 "Pair over QR" — in progress' in ev
    assert "ORB-2" not in ev  # done
    assert '[personal] thr_talk000001 "Talk mode" (project orbit-launcher) — WAITING FOR HIS INPUT' in ev
    assert '"Fix notification sound" (project orbit-launcher) — stopped' in ev
    assert "Remember the dentist at four" in ev
    assert "Morning briefing: ONLY when he asks" in p and "the companion has no location — if he asks, say so" in p
    assert not writes(bb())


def test_briefing_asked_through_need_details(talk, codex, briefing_world):
    codex.script(say("Let me look.", need={"briefing": True}), say("Here's your morning: call the bank."))
    reply, _ = talk("Anything I should know this morning?")
    first, second = codex.calls()
    assert "BRIEFING EVIDENCE gathered" not in first["prompt"]
    assert "BRIEFING EVIDENCE gathered" in second["prompt"] and "Call the bank about the card" in second["prompt"]
    assert reply == "Here's your morning: call the bank."


def test_briefing_without_yesterdays_page_uses_transcripts(talk, codex, client, store):
    add_note(client, store, (now_local() - timedelta(days=1, minutes=30)).isoformat(), "I must renew my passport")
    codex.script(say("Renew your passport."))
    talk("what's on today")
    p = codex.calls()[0]["prompt"]
    assert "has no diary page yet" in p and "I must renew my passport" in p


def test_briefing_already_given_is_not_fetched_twice(talk, codex, briefing_world):
    codex.script(say("Here.", need={"briefing": True}))
    talk("morning briefing please")
    assert len(codex.calls()) == 1


# --- reflection --------------------------------------------------------------------------------------


def test_reflection_conversation_save_after_yes_and_survives_rebuild(talk, codex, cfg, store, client, bb, monkeypatch):
    monkeypatch.setattr(insights, "DAY_ROLLOVER_H", 0)  # the clock may be past midnight here; rollover tested above
    add_note(client, store, iso(61), "Long day, the demo went well but I skipped lunch")
    text = "Today the demo went well, though I skipped lunch. I want to slow down a little tomorrow."
    codex.script(
        say("The demo went well, but you skipped lunch. What made today feel long?"),
        say("Here's a short reflection: the demo went well, you skipped lunch, slow down tomorrow. "
            "Shall I save it to today's diary page?", kind="diary_reflection", params={"text": text},
            summary="Save a reflection on today"),
        say("Okay.", confirmation="confirm"),
    )
    reply, _ = talk("Let's reflect on my day")
    p = codex.calls()[0]["prompt"]
    ev = p[p.index("REFLECTION EVIDENCE for"):p.index("VOICE NOTE (")]
    assert "the demo went well but I skipped lunch" in ev and "No reflection saved for that day yet." in ev
    assert "Evening reflection: ONLY when he asks" in p
    day = today()
    page = cfg.diary_dir / f"{day.isoformat()}.md"
    assert not page.exists()

    reply, _ = talk("Mostly the meetings. Yes, let's wrap it up")
    assert reply.endswith("Shall I save it to today's diary page?")  # Codex asked; no extra CONFIRM_ASK
    log = ActionLog(cfg.data_dir)
    pending = log.pending()
    assert pending.kind == "diary_reflection" and pending.params == {"text": text, "date": day.isoformat()}
    assert f"(diary page of {day:%A %d %B})" in pending.summary
    assert not page.exists()  # nothing before the yes

    reply, _ = talk("yes")
    assert reply == f"Okay. Done: I saved your reflection to the diary page for {day:%A %d %B}."
    assert text in page.read_text() and "## Reflection" in page.read_text()
    assert (cfg.data_dir / "reflections" / f"{day.isoformat()}.md").read_text().strip() == text
    assert not writes(bb())  # local action, no bb call

    # A diary rebuild rewrites only its own block: the reflection stays, once.
    diary.build(cfg, store, day)
    built = page.read_text()
    assert built.count("## Reflection") == 1 and text in built and diary.BEGIN in built
    assert built.index(diary.END) < built.index(insights.REFLECTION_BEGIN)
    # Page deleted in the vault -> the next build restores it from the companion copy.
    page.unlink()
    diary.build(cfg, store, day)
    assert page.read_text().count(text) == 1


def test_second_reflection_replaces_the_first(cfg):
    day = date(2026, 11, 14)
    insights.save_reflection(cfg, day, "First reflection of the day, quite short.")
    insights.save_reflection(cfg, day, "Second, better reflection of the day.")
    page = (cfg.diary_dir / "2026-11-14.md").read_text()
    assert "Second, better" in page and "First reflection" not in page and page.count("## Reflection") == 1


def test_reflection_too_short_is_rejected(talk, codex, cfg):
    codex.script(say("Save it?", kind="diary_reflection", params={"text": "ok"}, summary="Save a reflection"))
    reply, _ = talk("save my reflection")
    assert "too short" in reply and ActionLog(cfg.data_dir).pending() is None


# --- mood --------------------------------------------------------------------------------------------


def test_talk_turn_records_mood_quietly(talk, codex, cfg, store):
    codex.script(say("That sounds like a lovely afternoon.", m=mood(2, 4, "high", "says it was lovely")))
    reply, _ = talk("I had a lovely afternoon at the beach")
    assert reply == "That sounds like a lovely afternoon."
    rows = MoodLog(cfg.data_dir).since(today())
    assert [(r["mood"], r["energy"], r["confidence"], r["source"], r["mode"]) for r in rows] == \
        [(2, 4, "high", "talk", "talk")]
    p = codex.calls()[0]["prompt"]
    assert "Never mention mood, energy, these estimates" in p
    assert "MOOD & ENERGY HISTORY" not in p  # not asked -> not in front of Codex


def test_mood_from_detail_round_and_bad_values_ignored(talk, codex, cfg):
    codex.script(say("Checking.", need={"threads": ["thr_notif00001"]}, m=mood(0, 3, "low")),
                 say("It finished.", m=mood(-1, 2, "medium", "sounds tired")),
                 say("Okay.", m={"mood": 7, "energy": 3, "confidence": "high", "rationale": "x"}))
    talk("did the notification thread finish? I'm tired")
    talk("ok")
    rows = MoodLog(cfg.data_dir).since(today())
    assert [(r["mood"], r["energy"]) for r in rows] == [(-1, 2)]  # final answer's estimate; mood 7 dropped


def test_mood_question_answered_from_history(talk, codex, cfg, store, client):
    log = MoodLog(cfg.data_dir)
    for days_ago, m, e, conf in [(3, -1, 2, "medium"), (3, 0, 3, "low"), (1, 1, 4, "high")]:
        started = (now_local() - timedelta(days=days_ago)).isoformat()
        note = add_note(client, store, started, "words")
        log.record(store.get(note.id), mood(m, e, conf, f"note {days_ago}"), "diary")
    add_note(client, store, (now_local() - timedelta(days=2)).isoformat(), "not rated yet")
    codex.script(say("Lower midweek, better yesterday; rough guesses from a few notes."))
    talk("How's my mood been this week?")
    p = codex.calls()[0]["prompt"]
    ev = p[p.index("MOOD & ENERGY HISTORY,"):p.index("VOICE NOTE (")]
    d3 = (now_local() - timedelta(days=3)).date().isoformat()
    assert f"{d3} (" in ev and "average mood -1.0, energy 2.0, from 1 of 2 rated note(s) (low-confidence ones left out" in ev
    assert "mood +1, energy 4, high confidence — note 1" in ev
    assert "1 transcribed note(s) in this range have no estimate yet" in ev
    assert "not measurements" in ev


def test_diary_build_rates_normal_notes_without_overriding_talk(cfg, store, client, monkeypatch):
    day = date(2026, 11, 13)
    n1 = add_note(client, store, "2026-11-13T08:00:00+07:00", "Great run this morning")
    n2 = add_talk(client, store, "2026-11-13T09:00:00+07:00", "ugh meetings")
    n3 = add_note(client, store, "2026-11-13T10:00:00+07:00")  # not transcribed: no estimate
    MoodLog(cfg.data_dir).record(store.get(n2.id), mood(-1, 2, "high", "talk says ugh"), "talk")

    def fake(cfg_, day_, entries):
        data = {"diary": "A day.", "todos": [], "summaries": [
            {"note": i, "summary": f"s{i}", "mood": mood(2 - i, 5 - i, "medium", f"diary {i}")}
            for i in range(len(entries))]}
        return diary.from_json(data, entries)

    monkeypatch.setitem(diary.PROVIDERS, "fake", fake)
    cfg.llm_provider = "fake"
    page = diary.build(cfg, store, day).read_text()
    assert "mood" not in page.lower() and "energy" not in page.lower()  # private: never on the page
    rows = {r["note_id"]: r for r in MoodLog(cfg.data_dir).since(day)}
    assert (rows[n1.id]["mood"], rows[n1.id]["source"]) == (2, "diary")
    assert (rows[n2.id]["mood"], rows[n2.id]["source"]) == (-1, "talk")  # talk estimate kept
    assert n3.id not in rows
    assert "mood" in diary.SCHEMA["properties"]["summaries"]["items"]["required"]
    assert "never mention mood or energy scores in the diary text" in diary.prompt_parts(cfg, day, [])[0]


def test_ui_mood_table(cfg, store, client):
    n = add_note(client, store, (now_local() - timedelta(days=1)).isoformat(), "fine")
    MoodLog(cfg.data_dir).record(store.get(n.id), mood(1, 4, "medium", "fine"), "diary")
    r = ui_client(cfg, store).get("/ui/api/mood?days=7")
    assert r.status_code == 200
    body = r.json()
    assert body["days"][0]["mood"] == 1 and body["days"][0]["energy"] == 4
    assert body["notes"][0]["rationale"] == "fine" and body["unrated"] == 0
    assert ui_client(cfg, store, host="192.168.1.20").get("/ui/api/mood").status_code == 403
