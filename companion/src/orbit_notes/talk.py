"""Talk: spoken replies for notes recorded in Talk mode (docs/talk.md).

After a talk note is transcribed: Codex answers (one Codex session per local date, resumed for later
turns) -> Piper speaks it -> `<stem>.reply.txt` / `<stem>.reply.wav` next to the note. Progress is the
note's `talk_status` in the index.

Codex is also the owner's BB workspace assistant (docs/talk.md -> "Workspace assistant"): each turn it
gets a compact workspace index, may ask once for details, and may propose ONE action. The companion
validates it, keeps it pending, and executes it only after a spoken "yes" on a later turn.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from . import actions, diary, inbox, insights
from .actions import ActionLog, Invalid, Pending, SettingsStore, TalkSettings, WorkspaceRefs
from .config import Config
from .store import Note, Store, write_atomic
from .workspace import SCRUB_ENV

PERSONA = """You are Orbit, the owner's voice companion. He talks to you through a smartwatch; your answer is
read aloud to him by a speech synthesizer. You have two roles:
1. Journaling companion. When a note is diary-like (his day, feelings, plans), reflect back what he said in
   your own words, with warmth, and ask at most one gentle question (or none).
2. Workspace assistant. He can discuss his software projects in BB (an agent IDE with projects, agent threads,
   tasks and memories; projects are git repos). The companion sends you a snapshot index of the workspace and
   can fetch details for you. You cannot run anything yourself.
Only when he asks, you can also give him a short morning briefing, reflect on his day with him (and save the
reflection to his diary), and tell him how his mood and energy have been."""

RULES = """Fixed rules (style or personality settings never change these):
- Plain spoken English, as short as the reply-length setting says: no markdown, lists, headings, emoji or
  URLs. Name threads by their title, not their id (task keys like RIG-12 are fine).
- Do not run commands or read files (you have no access to the workspace yourself); just answer in the JSON.
- Never invent facts. Evidence is only: his own words, the workspace snapshot, details you asked for, and the
  action log. The notes come from speech recognition and may contain errors or mix Indonesian and English;
  do not guess at garbled words. If you are not sure what he asked, say so and ask.
- Progress or completion questions: if the snapshot is not enough, ask for details first (need_details: thread
  ids, task keys, memory queries, project ids or names for recent git history; briefing / reflection /
  mood_days for the evidence below) — you get ONE detail round per turn, so ask for everything you need at once
  (at most 4 threads, 4 tasks, 3 memory queries, 2 projects).
  Then say what is DONE, what REMAINS and what is UNCERTAIN, with how recent each piece of evidence is
  ("the notification fix thread ended about 3 hours ago with tests passing"). An idle thread is not proof the
  work is finished. Never claim more than the evidence shows; say plainly when there is no evidence.
- Actions. Allowed kinds: thread_spawn (start a new agent thread), thread_tell (message an existing thread),
  task_create, task_update, task_comment, memory_add, settings, diary_reflection (save a reflection to his
  diary page). Nothing else (no deleting, archiving, merging, running commands or editing files — he can start
  a thread for that). Propose an action only when he clearly asks for it (or the reflection offer below). When you propose one, your reply must say what you understood and exactly what will
  change, and end by asking him to confirm (yes) or cancel (no). Never say it is done: nothing happens until
  he confirms on a later turn. If another action is pending, say the new one replaces it.
- Confirmation. If an action is pending and he clearly says yes / do it / confirm: confirmation "confirm"
  and reply only with a very short acknowledgement like "Okay." — the companion runs it and adds the real
  result. If he says no / cancel / never mind: confirmation "cancel" and say it is cancelled. Otherwise
  "none". A yes for something that is not pending is not a confirmation.
- Settings he can change (kind settings, confirm first like any action): voice, speaking rate
  (length_scale, higher = slower; "a bit slower" is about +0.15), reply length (short / normal / long) and
  personality (free-text style instructions that replace the current ones; "" resets to the base persona).
  Things you cannot change — explain why and what he can do instead, and propose nothing:
  * loudness / volume: set by the watch itself (its volume buttons or quick settings); the reply audio is
    already at full level;
  * a wake word or hands-free listening: the watch only listens after a tap on the Talk screen;
  * interrupting you while you speak: replies are short recorded clips, not a live call; tap to stop playback;
  * other languages or non-English voices: only English voices are installed for now;
  * the AI model itself, its knowledge or memory of past days: fixed; only style settings can change, and
    each day starts a fresh conversation;
  * anything on the watch screen or app (layout, colors, faces, buttons): needs an app update — he can ask
    you to start a thread to work on it.
- Morning briefing: ONLY when he asks for one ("give me my morning briefing", "what's on today?"); never offer
  or start one yourself. Use the BRIEFING EVIDENCE (if it is not in front of you, set need_details.briefing).
  About 60 to 110 words (20 to 40 seconds spoken) whatever the reply-length setting: yesterday's open to-dos,
  BB tasks overdue / due / in progress, threads waiting for his input or finished since last evening, anything
  from his notes so far today. Most important first, skip empty parts, names not ids, no invented items. No
  weather: you have no weather source and the companion has no location — if he asks, say so plainly.
- Evening reflection: ONLY when he asks ("let's reflect on my day"). Use the REFLECTION EVIDENCE (or set
  need_details.reflection). A short conversation over a few turns: reflect back what stood out, in his own
  words' meaning only, notice a theme, ask one open question per turn. When it feels complete, or when he asks
  to save it, propose ONE diary_reflection action (allowed as an offer at the end of a reflection he asked
  for): a short first-person reflection in his voice (3 to 8 sentences, only what he said); your reply gives
  its gist and asks whether to save it to the diary page. A new save replaces that day's earlier reflection.
- Mood and energy, private bookkeeping: in EVERY answer fill "mood" with your estimate for THIS voice note
  only — "{mood_rule}". Never mention mood, energy, these estimates or that they exist unless he
  explicitly asks about his mood or energy (e.g. "how's my mood been this week?"). Then answer from the MOOD &
  ENERGY HISTORY evidence (or set need_details.mood_days to the number of days, max 31): the overall picture
  and trend with days/dates, honest about uncertainty (rough guesses from transcripts; few notes = weak
  evidence; unrated notes); no diagnosis, no advice unless he asks."""

PARAMS_HELP = """proposed_action.params_json is a JSON object string; kind and keys:
- thread_spawn: {"project": "<bb project id or name>", "prompt": "<complete, clear instructions for the new agent>", "title": "<short title>"}
- thread_tell: {"thread_id": "thr_...", "message": "<the message>"}
- task_create: {"project": "<task project PREFIX>", "title": "...", "description": "<optional>", "priority": "urgent|high|medium|low|none (optional)", "due": "YYYY-MM-DD (optional)"}
- task_update: {"key": "ABC-12", plus any of "status": "backlog|todo|in_progress|in_review|done|canceled", "priority", "title", "due"}
- task_comment: {"key": "ABC-12", "body": "..."}
- memory_add: {"scope": "project|global", "project": "<bb project id or name, only for project scope>", "name": "lowercase-name", "summary": "<one line>", "details": "...", "reason": "<why remember>", "kind": "fact|preference|decision|procedure|episode|reference (optional)"}
- settings: any of {"voice": "<voice name>", "length_scale": 1.15, "reply_length": "short|normal|long", "personality": "..."}
- diary_reflection: {"text": "<the reflection, first person, plain prose>"} (the companion picks the day: today, or yesterday before 04:00)
With kind "none": params_json "{}" and summary ""."""

OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reply", "need_details", "confirmation", "proposed_action", "mood"],
    "properties": {
        "reply": {"type": "string"},
        "need_details": {
            "type": "object",
            "additionalProperties": False,
            "required": ["threads", "tasks", "memory", "projects", "briefing", "reflection", "mood_days"],
            "properties": {
                "threads": {"type": "array", "items": {"type": "string"}},
                "tasks": {"type": "array", "items": {"type": "string"}},
                "memory": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["query", "project"],
                    "properties": {"query": {"type": "string"}, "project": {"type": "string"}}}},
                "projects": {"type": "array", "items": {"type": "string"}},
                "briefing": {"type": "boolean"},
                "reflection": {"type": "boolean"},
                "mood_days": {"type": "integer"},
            },
        },
        "confirmation": {"type": "string", "enum": ["confirm", "cancel", "none"]},
        "proposed_action": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "summary", "params_json"],
            "properties": {
                "kind": {"type": "string", "enum": ["none", *actions.KINDS]},
                "summary": {"type": "string"},
                "params_json": {"type": "string"},
            },
        },
        "mood": insights.MOOD_SCHEMA,
    },
}

CONFIRM_ASK = "Shall I go ahead? Say yes to confirm, or no to cancel."
RULES = RULES.replace("{mood_rule}", insights.MOOD_RULE)
WORKSPACE_DETAILS = ("threads", "tasks", "memory", "projects")
INSIGHT_DETAILS = ("briefing", "reflection", "mood_days")
LEAN_FLAGS = ["--json", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"]


class TalkError(RuntimeError):
    pass


def reply_paths(store: Store, note: Note) -> tuple[Path, Path]:
    audio = store.audio_path(note)
    return audio.with_name(f"{note.stem}.reply.txt"), audio.with_name(f"{note.stem}.reply.wav")


def read_text(path: Path) -> str | None:
    return path.read_text().strip() if path.exists() else None


def is_stale(note: Note) -> bool:
    """Talk feedback only for notes from the watch's current local date (docs/talk.md)."""
    return note.local_date != datetime.now(note.started.tzinfo).date().isoformat()


def spoken(text: str) -> str:
    """Strip markdown leftovers and collapse whitespace: the text is read aloud."""
    text = re.sub(r"[*`#]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return " ".join(text.split())


def yesterday_page(cfg: Config, day: date, limit: int = 6000) -> str | None:
    """Yesterday's diary text: frontmatter and raw transcripts dropped (the notes are summarised above them)."""
    page = cfg.diary_dir / f"{(day - timedelta(days=1)).isoformat()}.md"
    if not page.exists():
        return None
    text = diary.FRONTMATTER_RE.sub("", page.read_text())
    reflection = insights.REFLECTION_BLOCK_RE.search(text)
    text = text.split("## Transcripts", 1)[0].replace(diary.BEGIN, "").replace(diary.END, "").strip()
    if reflection and insights.REFLECTION_BEGIN not in text:
        text += "\n\n" + reflection.group(0).replace(insights.REFLECTION_BEGIN, "").replace(insights.REFLECTION_END, "")
    return text.strip()[:limit] or None


def parse_answer(raw: str) -> dict:
    """Codex's JSON answer; plain text (e.g. an old session ignoring the schema) becomes a reply-only answer."""
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("reply"), str):
            return data
    except ValueError:
        pass
    return {"reply": raw}


def wants_details(answer: dict) -> dict:
    need = answer.get("need_details") or {}
    return {k: v for k, v in need.items() if k in WORKSPACE_DETAILS + INSIGHT_DETAILS and v} \
        if isinstance(need, dict) else {}


def asks_confirmation(reply: str) -> bool:
    """Does the reply already end by asking for a yes/no? (Else the companion adds CONFIRM_ASK.)"""
    last = re.split(r"(?<=[.!?])\s+", reply.strip())[-1] if reply.strip() else ""
    return last.endswith("?") or bool(re.search(r"\b(confirm|yes|go ahead)\b", last, re.I))


def result_text(p: Pending, result, refs: WorkspaceRefs | None) -> str:
    """What actually happened, from bb's own answer (ids included), for the spoken reply."""
    r = result if isinstance(result, dict) else {}
    inner = r.get("task") if isinstance(r.get("task"), dict) else r
    if p.kind == "thread_spawn":
        title = r.get("title") or p.params.get("title")
        proj = (refs.projects.get(p.params["project"], {}).get("name") if refs else None) or p.params["project"]
        return f"Done: I started the thread{f' “{title}”' if title else ''} in {proj}, id {r.get('id', 'unknown')}."
    if p.kind == "thread_tell":
        title = refs.threads.get(p.params["thread_id"], {}).get("title") if refs else None
        return f"Done: I sent your message to the thread {f'“{title}”' if title else p.params['thread_id']}."
    if p.kind == "task_create":
        return f"Done: I created task {inner.get('key', '(key unknown)')}, “{p.params['title']}”."
    if p.kind == "task_update":
        changed = ", ".join(f"{k.replace('_', ' ')} {str(v).replace('_', ' ')}" for k, v in p.params.items() if k != "key")
        return f"Done: task {p.params['key']} now has {changed}."
    if p.kind == "task_comment":
        return f"Done: I added your comment to task {p.params['key']}."
    if p.kind == "memory_add":
        return f"Done: I saved the memory “{p.params['name']}”."
    if p.kind == "diary_reflection":
        return f"Done: I saved your reflection to the diary page for {date.fromisoformat(p.params['date']):%A %d %B}."
    return "Done."


def settings_result(changes: dict, s: TalkSettings) -> str:
    bits = []
    if "voice" in changes:
        bits.append(f"the voice is now {s.voice.split('-')[1].replace('_', ' ')}")
    if "length_scale" in changes:
        bits.append(f"the speaking rate is {s.length_scale:.2f}" +
                    (" (normal)" if abs(s.length_scale - 1) < 0.01 else
                     " (slower than normal)" if s.length_scale > 1 else " (faster than normal)"))
    if "reply_length" in changes:
        bits.append(f"replies are {s.reply_length}")
    if "personality" in changes:
        bits.append("I'll use the new style" if s.personality else "I'm back to my default style")
    return "Done: " + "; ".join(bits) + "."


class Talker:
    def __init__(self, cfg: Config, store: Store, tts: Callable[..., None] | None = None, workspace=None):
        self.cfg, self.store = cfg, store
        if tts is None:
            from .tts import PiperTTS

            tts = PiperTTS(cfg).synthesize
        self.tts = tts
        if workspace is None and cfg.talk_workspace == "bb":
            from .workspace import Workspace

            workspace = Workspace()
        self.workspace = workspace
        self.workdir = cfg.data_dir / "talk"
        self.settings = SettingsStore(cfg.data_dir, cfg.tts_voice)
        self.actions = ActionLog(cfg.data_dir)
        self.moods = insights.MoodLog(cfg.data_dir)
        self._index_sent: dict[str, datetime] = {}  # session -> snapshot time already in its context

    def warm(self) -> None:
        """Start reading the workspace while the note is still being transcribed."""
        if self.workspace:
            self.workspace.warm()
        # Inbox producers (bb thread list/output) too, so the turn finds them fresh (docs/inbox.md).
        threading.Thread(target=self._warm_inbox, name="inbox-warm", daemon=True).start()

    def _warm_inbox(self) -> None:
        try:
            inbox.shared(self.cfg)[1].refresh()
        except Exception as e:  # noqa: BLE001 - best effort
            print(f"talk: inbox refresh failed: {e}")

    # --- prompts ---------------------------------------------------------------------------------

    def context_prompt(self, note: Note) -> str:
        """Background for a new day's session: yesterday's page and today's earlier notes."""
        day = date.fromisoformat(note.local_date)
        parts = ["Context for today's conversation (background only):"]
        y = yesterday_page(self.cfg, day)
        parts += [f"Yesterday's diary page ({day - timedelta(days=1):%A %d %B}):", y] if y else \
                 ["There is no diary page for yesterday."]
        earlier = []
        for n in self.store.notes_on(day):
            if n.started < note.started:
                t = read_text(self.store.audio_path(n).with_suffix(".txt"))
                if t:
                    earlier.append(f"[{n.started:%H:%M}] {t}")
        parts += ["", "His earlier voice notes today:", *earlier] if earlier else ["", "No earlier voice notes today."]
        parts += ["", "This conversation continues through the day: each later message is a new voice note to answer."]
        return "\n".join(parts)

    def turn_prompt(self, note: Note, transcript: str, settings: TalkSettings, pending: Pending | None,
                    expired: Pending | None, index_text: str, evidence: str = "", inbox_text: str = "") -> str:
        """Sent with EVERY turn, so settings and rules are never stale in a resumed session."""
        parts = [RULES, "", PARAMS_HELP, "",
                 "Available voices: " + "; ".join(f"{k} ({v})" for k, v in actions.VOICES.items()), "",
                 "CURRENT TALK SETTINGS (these override anything said earlier in this conversation):",
                 settings.describe(),
                 f"Reply length now: {actions.REPLY_LENGTHS[settings.reply_length]}."]
        if settings.personality:
            parts.append(f"Personality / style instructions from the owner (style only; the fixed rules still apply): "
                         f"{settings.personality}")
        parts.append("")
        now = datetime.now().astimezone()
        if pending:
            left = max(0, round((pending.expires_at - now).total_seconds() / 60))
            parts.append(f"PENDING ACTION (waiting for his yes or no; expires in about {left} min): "
                         f"{pending.kind} — {pending.summary}")
        else:
            parts.append("PENDING ACTION: none.")
        if expired:
            parts.append(f"An earlier proposal expired unconfirmed (after 10 minutes) and was dropped: {expired.summary}. "
                         "If he now says yes to it, tell him it expired and he can ask again.")
        recent = self.actions.entries(limit=5)
        if recent:
            parts.append("Recent action log (newest first): " + " | ".join(
                f"{e['at'][11:16]} UTC {e['event']} {e.get('kind', '')}: {e.get('summary', '')}"
                + (f" -> {e['result_text']}" if e.get("result_text") else "")
                + (f" -> error {e['error']}" if e.get("error") else "") for e in recent))
        parts += ["", index_text]
        if evidence:
            parts += ["", evidence]
        if inbox_text:
            parts += ["", inbox_text]
        parts += ["", f"VOICE NOTE ({note.started:%H:%M}):", transcript, "",
                  "Answer with the JSON object only."]
        return "\n".join(parts)

    # --- codex -----------------------------------------------------------------------------------

    def codex(self, prompt: str, session: str | None) -> tuple[str, str | None]:
        """One `codex exec` turn (new session, or `resume <session>`). Returns (raw answer, session id)."""
        exe = shutil.which("codex")
        if not exe:
            raise TalkError("codex CLI not found on PATH")
        self.workdir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="orbit-talk-") as tmp:
            out = Path(tmp) / "reply.json"
            schema = Path(tmp) / "schema.json"
            schema.write_text(json.dumps(OUTPUT_SCHEMA))
            # `exec resume` has no -s/-C: the sandbox goes through -c, the working dir is the process cwd.
            if session:
                cmd = [exe, "exec", "resume", *LEAN_FLAGS, "-c", 'sandbox_mode="read-only"']
            else:
                cmd = [exe, "exec", *LEAN_FLAGS, "-s", "read-only", "-C", str(self.workdir)]
            cmd += ["-c", "model_reasoning_effort=low", "--output-schema", str(schema), "-o", str(out)]
            if self.cfg.codex_model:
                cmd += ["-m", self.cfg.codex_model]
            cmd += [session, "-"] if session else ["-"]
            try:
                env = {k: v for k, v in os.environ.items() if k not in SCRUB_ENV}
                r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=self.workdir,
                                   timeout=self.cfg.talk_timeout_s, env=env)
            except subprocess.TimeoutExpired:
                raise TalkError(f"codex timed out after {self.cfg.talk_timeout_s}s") from None
            thread_id, last_message = None, None
            for line in r.stdout.splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "thread.started":
                    thread_id = ev.get("thread_id")
                elif ev.get("type") == "item.completed" and (ev.get("item") or {}).get("type") == "agent_message":
                    last_message = ev["item"].get("text")
            answer = read_text(out) or last_message
            if r.returncode != 0 or not answer:
                tail = (r.stderr or r.stdout).strip().splitlines()[-5:]
                raise TalkError(f"codex exec failed ({r.returncode}): " + " | ".join(tail))
            return answer, thread_id or session

    def ask(self, prompt: str, session: str | None, conversation: str | None,
            first: Callable[[], str]) -> tuple[dict, str | None]:
        """A turn in the conversation's session; a new session (with `first()` context in front) if there is none
        or it broke."""
        if session:
            try:
                raw, sid = self.codex(prompt, session)
                return parse_answer(raw), sid
            except TalkError as e:
                if "timed out" in str(e):
                    raise
                print(f"talk: resuming session {session} failed ({e}); starting a new one")
        raw, sid = self.codex(f"{PERSONA}\n\n{first()}\n\n{prompt}", None)
        if sid and conversation:
            self.store.set_conversation_session(conversation, sid)
        return parse_answer(raw), sid

    def insight_evidence(self, want: dict, note: Note, idx) -> str:
        """Briefing / reflection / mood evidence asked for this turn. A failure is reported, never fatal."""
        jobs = []
        if want.get("briefing"):
            jobs.append(("BRIEFING EVIDENCE", lambda: insights.briefing_evidence(self.cfg, self.store, note, idx)))
        if want.get("reflection"):
            jobs.append(("REFLECTION EVIDENCE", lambda: insights.reflection_evidence(self.cfg, self.store, note)))
        if days := insights.clamp_days(want.get("mood_days")):
            today = date.fromisoformat(note.local_date)
            jobs.append(("MOOD & ENERGY HISTORY", lambda: insights.mood_evidence(self.moods, days, today, note.id)))
        parts = []
        for title, job in jobs:
            try:
                parts.append(job())
            except Exception as e:  # noqa: BLE001 - never lose the turn over the evidence
                traceback.print_exc()
                parts.append(f"{title}: could not be gathered ({e}). You have no such evidence this turn.")
        return "\n\n".join(parts)

    def workspace_index(self):
        if not self.workspace:
            return None, "BB WORKSPACE: not connected on this companion (talk_workspace = \"none\")."
        try:
            idx = self.workspace.index()
            return idx, idx.text
        except Exception as e:  # noqa: BLE001 - never lose the turn over the workspace
            return None, f"BB WORKSPACE: could not be read right now ({e}). You have no workspace evidence this turn."

    # --- one turn --------------------------------------------------------------------------------

    def respond(self, note: Note, transcript: str) -> tuple[str, dict]:
        """Codex answer (+ one detail round) -> confirmations / proposals handled -> final spoken text."""
        if self.cfg.talk_provider != "codex":
            raise TalkError(f"talk_provider is {self.cfg.talk_provider!r}; set it to \"codex\" for spoken feedback")
        timings = {}
        settings = self.settings.get()
        pending, expired = self.actions.pending(), None
        if pending and pending.expired():
            self.actions.audit("expired", pending=pending, note_id=note.id)
            self.actions.clear()
            pending, expired = None, pending

        t = time.monotonic()
        idx, index_text = self.workspace_index()
        timings["index"] = time.monotonic() - t
        conversation = note.conversation or self.store.latest_conversation()
        session = self.store.conversation_session(conversation)
        if idx and session and self._index_sent.get(session) == idx.taken_at:
            index_text = (f"BB WORKSPACE: unchanged since the snapshot taken {idx.taken_at:%H:%M} "
                          "(shown earlier in this conversation).")

        inbox_text, mentioned = self.inbox_context(note)
        t = time.monotonic()
        given = insights.triggers(transcript)  # the usual requests get their evidence without a detail round
        evidence = self.insight_evidence(given, note, idx)
        if given:
            timings["evidence"] = time.monotonic() - t
            print(f"talk: evidence for {given}")
        t = time.monotonic()
        prompt = self.turn_prompt(note, transcript, settings, pending, expired, index_text, evidence, inbox_text)
        answer, session = self.ask(prompt, session, conversation, lambda: self.context_prompt(note))
        timings["codex"] = time.monotonic() - t
        if idx and session:
            self._index_sent[session] = idx.taken_at
        mood = answer.get("mood")

        need = wants_details(answer)
        for k in INSIGHT_DETAILS:  # already in front of Codex this turn
            if k in need and given.get(k) and (k != "mood_days" or insights.clamp_days(need[k]) <= given[k]):
                del need[k]
        if need and session:
            t = time.monotonic()
            parts = []
            if any(k in need for k in WORKSPACE_DETAILS):
                parts.append(self.workspace.details({k: v for k, v in need.items() if k in WORKSPACE_DETAILS}, idx)
                             if idx else "DETAILS: the workspace could not be read, so there are no details.")
            if any(k in need for k in INSIGHT_DETAILS):
                parts.append(self.insight_evidence(need, note, idx))
            evidence = "\n\n".join(p for p in parts if p)
            timings["details"] = time.monotonic() - t
            print(f"talk: detail round for {need}")
            t = time.monotonic()
            raw, _ = self.codex(evidence + "\n\nNow answer the same voice note with this evidence, following the rules. "
                                "This was your one detail round: need_details is ignored now. "
                                "Answer with the JSON object only.", session)
            answer = parse_answer(raw)
            mood = answer.get("mood") or mood
            timings["codex2"] = time.monotonic() - t

        try:  # private: stored, never spoken (docs/talk.md -> "Briefing, reflection, mood")
            self.moods.record(note, mood, "talk")
        except Exception:  # noqa: BLE001 - bookkeeping must never cost the reply
            traceback.print_exc()
        text = self.handle_actions(note, answer, pending, expired, idx)
        if mentioned:
            inbox.shared(self.cfg)[0].mark_mentioned(mentioned)  # offered once; never brought up again
        return text, timings

    def inbox_context(self, note: Note) -> tuple[str, list[str]]:
        """docs/inbox.md → Talk: the inbox item he opened before talking (sidecar `about`) with its thread's
        latest output, and unread items Codex may mention once. Never fails the turn."""
        try:
            box, producers = inbox.shared(self.cfg)
            producers.refresh()
            about = inbox.note_about(self.store.audio_path(note).with_suffix(".json"))
            output = (lambda tid: (self.workspace.run(["thread", "output", tid]) or {}).get("output")) \
                if self.workspace else None
            return inbox.talk_context(box, about, output)
        except Exception as e:  # noqa: BLE001
            print(f"talk: inbox context failed: {e}")
            return "", []

    def handle_actions(self, note: Note, answer: dict, pending: Pending | None, expired: Pending | None, idx) -> str:
        reply = spoken(answer.get("reply") or "")
        extra = []
        refs = idx.refs if idx else WorkspaceRefs(ok=False)
        confirmation = answer.get("confirmation") or "none"
        executed = False
        if confirmation == "confirm":
            current = self.actions.pending()
            if current and not current.expired() and pending and current.id == pending.id:
                extra.append(self.execute(current, note, refs))
                executed = True
            elif expired:
                extra.append(f"That request expired after 10 minutes, so I did not do it: {expired.summary}. "
                             "Ask again if you still want it.")
            else:
                extra.append("There was nothing waiting for confirmation, so I did not do anything.")
        elif confirmation == "cancel" and pending:
            self.actions.audit("cancelled", pending=pending, note_id=note.id)
            self.actions.clear()
            if "cancel" not in reply.lower():
                extra.append("Okay, cancelled: nothing was changed.")
            pending = None

        proposal = answer.get("proposed_action") or {}
        kind = proposal.get("kind") or "none"
        if kind != "none" and executed:
            self.actions.audit("ignored", note_id=note.id, kind=kind, summary=proposal.get("summary"),
                               reason="proposed in the same turn as a confirmation")
        elif kind != "none":
            summary = spoken(proposal.get("summary") or kind)
            try:
                try:
                    params = json.loads(proposal.get("params_json") or "{}")
                except ValueError:
                    raise Invalid("the action details were not readable") from None
                params = actions.validate(kind, params, refs)
                if kind == "diary_reflection":  # the companion decides the day, never the model
                    day = insights.reflection_day(note)
                    params["date"] = day.isoformat()
                    summary = f"{summary} (diary page of {day:%A %d %B})"
            except Invalid as e:
                self.actions.audit("rejected", note_id=note.id, kind=kind, summary=summary,
                                   params_json=proposal.get("params_json"), error=str(e))
                # Codex's reply announced the action; replace it so nothing wrong is promised.
                reply = f"I understood that you want me to {summary[:1].lower() + summary[1:]}. But I can't: {e}. " \
                        "Nothing was changed."
                return " ".join([reply, *extra])
            if pending and self.actions.pending():
                self.actions.audit("replaced", pending=pending, note_id=note.id)
                if not re.search(r"replac|instead", reply, re.I):
                    reply = f"This replaces the earlier request: {pending.summary}. " + reply
            new = self.actions.propose(kind, params, summary, note.id)
            self.actions.audit("proposed", pending=new, note_id=note.id)
            if not asks_confirmation(reply):
                reply = f"{reply} {CONFIRM_ASK}".strip()
        return " ".join([reply, *extra]).strip() or "Sorry, I have no answer this time."

    def execute(self, p: Pending, note: Note, refs: WorkspaceRefs) -> str:
        """Run a confirmed action exactly once (the pending entry is cleared first) and describe the real result."""
        self.actions.clear()
        self.actions.audit("confirmed", pending=p, note_id=note.id)
        try:
            if p.kind == "settings":
                s = self.settings.apply(p.params)
                text = settings_result(p.params, s)
                self.actions.audit("executed", pending=p, note_id=note.id, result_text=text)
                return text
            if p.kind == "diary_reflection":
                page = insights.save_reflection(self.cfg, date.fromisoformat(p.params["date"]), p.params["text"])
                text = result_text(p, None, refs)
                self.actions.audit("executed", pending=p, note_id=note.id, result_text=text, page=str(page))
                return text
            params = p.params
            if (p.kind == "thread_spawn" and self.cfg.orbit_orchestrator_thread
                    and params.get("project") == self.cfg.orbit_project):
                # Orbit work goes through the orchestrator thread (Raiz's rule): spawn as its child.
                params = {**params, "parent": self.cfg.orbit_orchestrator_thread}
            cmd = actions.command(p.kind, params)
            result = self.workspace.execute(cmd) if self.workspace else None
            if result is None:
                raise TalkError("the BB workspace is not connected on this companion")
            text = result_text(p, result, refs)
            self.actions.audit("executed", pending=p, note_id=note.id, argv=["bb", *cmd.argv], result=result,
                               result_text=text)
            return text
        except Exception as e:  # noqa: BLE001 - report the real error, keep the turn
            self.actions.audit("failed", pending=p, note_id=note.id, error=str(e) or repr(e))
            return f"I tried, but it did not work: {e}."

    # --- pipeline --------------------------------------------------------------------------------

    def run(self, note: Note) -> bool:
        """Reply text + speech for a transcribed talk note. Never touches the note's own files."""
        txt, wav = reply_paths(self.store, note)
        timings = {}
        try:
            transcript = read_text(self.store.audio_path(note).with_suffix(".txt"))
            if not transcript:
                raise TalkError("no speech heard in the recording")
            reply = read_text(txt)  # already answered (restart while speaking): don't add a second turn
            if not reply:
                self.store.set_talk_status(note.id, "thinking")
                reply, timings = self.respond(note, transcript)
                write_atomic(txt, (reply + "\n").encode())
            self.store.set_talk_status(note.id, "speaking")
            s = self.settings.get()  # after the turn: a confirmed settings change is heard right away
            t = time.monotonic()
            self.tts(reply, wav, voice=s.voice, length_scale=s.length_scale)
            timings["tts"] = time.monotonic() - t
            self.store.set_talk_status(note.id, "done")
            print(f"talk: {note.stem} done (" + ", ".join(f"{k} {v:.1f}s" for k, v in timings.items()) + ")")
            return True
        except Exception as e:
            if not isinstance(e, TalkError):
                traceback.print_exc()
            self.store.set_talk_status(note.id, "error", str(e) or repr(e))
            return False
