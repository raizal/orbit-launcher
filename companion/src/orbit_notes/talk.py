"""Talk: short spoken feedback for notes recorded in Talk mode (docs/talk.md).

After a talk note is transcribed: Codex writes the feedback (one Codex session per local
date, resumed for later turns) -> Piper speaks it -> `<stem>.reply.txt` / `<stem>.reply.wav`
next to the note. Progress is the note's `talk_status` in the index.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import traceback
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from . import diary
from .config import Config
from .store import Note, Store, write_atomic

PERSONA = """You are a warm, thoughtful journaling companion. The owner talks to you through a smartwatch
like a spoken diary; your answer is read aloud to them by a speech synthesizer.
Rules:
- Reply in English, in 2 to 4 short sentences, as plain spoken text: no markdown, no lists, no emoji, no headings.
- Reflect back what they said, in your own words, with warmth. Ask at most one gentle question (or none).
- Never invent facts about their life: use only what they told you (in their notes and the context below).
- The notes come from speech recognition and may contain errors or mix Indonesian and English; do not guess at garbled words.
- Do not run commands or read files. Just answer."""

TURN_REMINDER = "(Answer as before: English, 2 to 4 short spoken sentences, at most one gentle question, no markdown.)"

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
    text = text.split("## Transcripts", 1)[0].replace(diary.BEGIN, "").replace(diary.END, "").strip()
    return text[:limit] or None


class Talker:
    def __init__(self, cfg: Config, store: Store, tts: Callable[[str, Path], None] | None = None):
        self.cfg, self.store = cfg, store
        if tts is None:
            from .tts import PiperTTS

            tts = PiperTTS(cfg).synthesize
        self.tts = tts
        self.workdir = cfg.data_dir / "talk"

    # --- prompts ---------------------------------------------------------------------------------

    def first_prompt(self, note: Note, transcript: str) -> str:
        day = date.fromisoformat(note.local_date)
        parts = [PERSONA, "", "Context for today's conversation (background only):"]
        y = yesterday_page(self.cfg, day)
        parts += [f"Yesterday's diary page ({day - timedelta(days=1):%A %d %B}):", y] if y else \
                 ["There is no diary page for yesterday."]
        earlier = []
        for n in self.store.notes_on(day):
            if n.started < note.started:
                t = read_text(self.store.audio_path(n).with_suffix(".txt"))
                if t:
                    earlier.append(f"[{n.started:%H:%M}] {t}")
        parts += ["", "Their earlier voice notes today:", *earlier] if earlier else ["", "No earlier voice notes today."]
        parts += ["", "This conversation continues through the day: each later message is a new voice note to answer.",
                  "", self.turn(note, transcript)]
        return "\n".join(parts)

    @staticmethod
    def turn(note: Note, transcript: str) -> str:
        return f"Voice note ({note.started:%H:%M}):\n{transcript}"

    # --- codex -----------------------------------------------------------------------------------

    def codex(self, prompt: str, session: str | None) -> tuple[str, str | None]:
        """One `codex exec` turn (new session, or `resume <session>`). Returns (reply, session id)."""
        exe = shutil.which("codex")
        if not exe:
            raise TalkError("codex CLI not found on PATH")
        self.workdir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="orbit-talk-") as tmp:
            out = Path(tmp) / "reply.txt"
            # `exec resume` has no -s/-C: the sandbox goes through -c, the working dir is the process cwd.
            if session:
                cmd = [exe, "exec", "resume", *LEAN_FLAGS, "-c", 'sandbox_mode="read-only"']
            else:
                cmd = [exe, "exec", *LEAN_FLAGS, "-s", "read-only", "-C", str(self.workdir)]
            cmd += ["-c", "model_reasoning_effort=low", "-o", str(out)]
            if self.cfg.codex_model:
                cmd += ["-m", self.cfg.codex_model]
            cmd += [session, "-"] if session else ["-"]
            try:
                r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=self.workdir,
                                   timeout=self.cfg.talk_timeout_s)
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
            reply = read_text(out) or last_message
            if r.returncode != 0 or not reply:
                tail = (r.stderr or r.stdout).strip().splitlines()[-5:]
                raise TalkError(f"codex exec failed ({r.returncode}): " + " | ".join(tail))
            return spoken(reply), thread_id

    def feedback(self, note: Note, transcript: str) -> str:
        if self.cfg.talk_provider != "codex":
            raise TalkError(f"talk_provider is {self.cfg.talk_provider!r}; set it to \"codex\" for spoken feedback")
        day = date.fromisoformat(note.local_date)
        session = self.store.talk_session(day)
        if session:
            try:
                return self.codex(f"{self.turn(note, transcript)}\n\n{TURN_REMINDER}", session)[0]
            except TalkError as e:
                if "timed out" in str(e):
                    raise
                print(f"talk: resuming session {session} failed ({e}); starting a new one")
        reply, thread_id = self.codex(self.first_prompt(note, transcript), None)
        if thread_id:
            self.store.set_talk_session(day, thread_id)
        return reply

    # --- pipeline --------------------------------------------------------------------------------

    def run(self, note: Note) -> bool:
        """Feedback text + speech for a transcribed talk note. Never touches the note's own files."""
        txt, wav = reply_paths(self.store, note)
        timings = {}
        try:
            transcript = read_text(self.store.audio_path(note).with_suffix(".txt"))
            if not transcript:
                raise TalkError("no speech heard in the recording")
            reply = read_text(txt)  # already answered (restart while speaking): don't add a second turn
            if not reply:
                self.store.set_talk_status(note.id, "thinking")
                t = time.monotonic()
                reply = self.feedback(note, transcript)
                timings["codex"] = time.monotonic() - t
                write_atomic(txt, (reply + "\n").encode())
            self.store.set_talk_status(note.id, "speaking")
            t = time.monotonic()
            self.tts(reply, wav)
            timings["tts"] = time.monotonic() - t
            self.store.set_talk_status(note.id, "done")
            print(f"talk: {note.stem} done (" + ", ".join(f"{k} {v:.1f}s" for k, v in timings.items()) + ")")
            return True
        except Exception as e:
            if not isinstance(e, TalkError):
                traceback.print_exc()
            self.store.set_talk_status(note.id, "error", str(e) or repr(e))
            return False
