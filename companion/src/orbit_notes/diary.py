"""Build one Markdown diary page per local date. Only the part between the orbit markers is rewritten."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import insights, memory
from .config import Config
from .store import Note, Store, write_atomic

BEGIN, END = "<!-- orbit:begin -->", "<!-- orbit:end -->"
NO_LLM = '_No LLM configured (`llm_provider = "none"`), so no diary text was written. The notes and transcripts are below._'
FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


@dataclass
class Entry:
    note: Note
    audio: Path
    transcript: str | None  # None = not transcribed yet, "" = transcribed, no speech
    feedback: str | None = None  # talk notes: the spoken reply (<stem>.reply.txt)


@dataclass
class Generated:
    diary: str
    summaries: list[str]  # one per entry, same order
    todos: list[str] = field(default_factory=list)  # things the owner said they need to do
    moods: list[dict | None] = field(default_factory=list)  # private per-note estimates (never on the page)
    raw: dict = field(default_factory=dict)  # the LLM's JSON (facts + tags for memory, docs/memory.md)


def collect(store: Store, day: date) -> list[Entry]:
    entries = []
    for note in store.notes_on(day):
        audio = store.audio_path(note)
        txt = audio.with_suffix(".txt")
        reply = audio.with_name(f"{note.stem}.reply.txt")
        entries.append(Entry(note, audio, txt.read_text().strip() if txt.exists() else None,
                             reply.read_text().strip() if note.is_talk and reply.exists() else None))
    return entries


# --- LLM providers -------------------------------------------------------------------------------


def transcript_label(text: str | None) -> str:
    return "(not transcribed yet)" if text is None else "(no speech)"


def first_words(text: str | None, limit: int = 80) -> str:
    if not text:
        return transcript_label(text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def generate_none(cfg: Config, day: date, entries: list[Entry]) -> Generated:
    return Generated(NO_LLM, [first_words(e.transcript) for e in entries])


SYSTEM_PROMPT = """You write a personal diary page from the owner's voice notes of one day.
Rules:
- Use ONLY what is said in the transcripts. Never invent events, people, places, feelings or details that are not in them.
- If something is unclear or the transcripts are thin, keep the diary short rather than filling gaps.
- Write in the first person, as the owner, in plain prose (a few short paragraphs, no headings).
- Transcripts come from speech recognition and may contain errors; do not guess wildly at garbled words.
- {language}
Also give a one-line summary (max ~12 words) for each note, in the same language as the diary,
and list the concrete to-dos the owner says they need or plan to do (empty list if none; never invent any).
Finally, for each note, privately estimate the speaker's mood and energy from that note alone ({mood_rule}).
These estimates are stored separately: never mention mood or energy scores in the diary text."""

SCHEMA = {
    "type": "object",
    "properties": {
        "diary": {"type": "string"},
        "summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"note": {"type": "integer"}, "summary": {"type": "string"},
                               "mood": insights.MOOD_SCHEMA},
                "required": ["note", "summary", "mood"],
                "additionalProperties": False,
            },
        },
        "todos": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["diary", "summaries", "todos"],
    "additionalProperties": False,
}
memory.extend_diary_schema(SCHEMA)  # + facts[] and summaries[].tags, from the same call (docs/memory.md)


def prompt_parts(cfg: Config, day: date, entries: list[Entry]) -> tuple[str, str]:
    """(system prompt, user message) shared by every LLM provider."""
    language = (
        "Write in the language the notes are mostly spoken in."
        if cfg.diary_language.lower() == "auto"
        else f"Write in {cfg.diary_language}."
    )
    notes = "\n\n".join(
        f"[note {i}] {e.note.started:%H:%M}\n{e.transcript or transcript_label(e.transcript)}" for i, e in enumerate(entries)
    )
    known = memory.Memory(cfg, None, embedder=None).known_facts_text()
    system = SYSTEM_PROMPT.format(language=language, mood_rule=insights.MOOD_RULE) + "\n" + \
        memory.DIARY_RULE.format(known=known)
    return system, f"Voice notes from {day:%A, %d %B %Y}, in time order:\n\n{notes}"


def from_json(data: dict, entries: list[Entry]) -> Generated:
    by_note = {s["note"]: s["summary"].strip() for s in data.get("summaries", [])}
    moods = {s["note"]: s.get("mood") for s in data.get("summaries", [])}
    return Generated(
        data["diary"].strip(),
        [by_note.get(i) or first_words(e.transcript) for i, e in enumerate(entries)],
        [t.strip() for t in data.get("todos", []) if t.strip()],
        [moods.get(i) if e.transcript else None for i, e in enumerate(entries)],
        data,
    )


def generate_anthropic(cfg: Config, day: date, entries: list[Entry]) -> Generated:
    import anthropic  # optional at runtime: only needed for this provider

    system, user = prompt_parts(cfg, day, entries)
    extra = {}
    if cfg.llm_model in FALLBACK_MODELS:  # on a safety refusal, the API re-runs the request on a fallback model
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY (or an `ant auth login` profile)
    response = client.beta.messages.create(
        model=cfg.llm_model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        **extra,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined to write this diary page")
    return from_json(json.loads(next(b.text for b in response.content if b.type == "text")), entries)


def generate_codex(cfg: Config, day: date, entries: list[Entry]) -> Generated:
    """
    Codex CLI in headless mode (`codex exec`), using your Codex login — no API key here.
    Runs in an empty temp dir with a read-only sandbox and no session history; the
    answer is forced into SCHEMA with --output-schema and read from -o.
    """
    exe = shutil.which("codex")
    if not exe:
        raise RuntimeError("codex CLI not found on PATH (install it or set llm_provider)")
    system, user = prompt_parts(cfg, day, entries)
    with tempfile.TemporaryDirectory(prefix="orbit-diary-") as tmp:
        schema = Path(tmp) / "schema.json"
        out = Path(tmp) / "out.json"
        schema.write_text(json.dumps(SCHEMA))
        cmd = [exe, "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "-C", tmp,
               "--color", "never", "--output-schema", str(schema), "-o", str(out)]
        if cfg.codex_model:
            cmd += ["-m", cfg.codex_model]
        prompt = f"{system}\n\nReply only with the JSON object. Do not run commands or read files.\n\n{user}"
        r = subprocess.run(cmd + ["-"], input=prompt, capture_output=True, text=True, timeout=cfg.codex_timeout_s)
        if r.returncode != 0 or not out.exists():
            tail = (r.stderr or r.stdout).strip().splitlines()[-5:]
            raise RuntimeError(f"codex exec failed ({r.returncode}): " + " | ".join(tail))
        return from_json(json.loads(out.read_text()), entries)


PROVIDERS = {"none": generate_none, "anthropic": generate_anthropic, "codex": generate_codex}


# --- rendering -----------------------------------------------------------------------------------


def audio_link(audio: Path, cfg: Config) -> str:
    """Embedded player (`![[…]]`) when the audio is inside the vault, else an absolute file:// link."""
    if cfg.vault_dir and audio.resolve().is_relative_to(cfg.vault_dir.resolve()):
        return f"![[{audio.resolve().relative_to(cfg.vault_dir.resolve()).as_posix()}]]"
    return f"[{audio.name}](<{audio.resolve().as_uri()}>)"


def vault_copy(audio: Path, cfg: Config) -> Path:
    """
    Copy of the note in `<diary folder>/audio/` so Obsidian can play it. Obsidian
    doesn't play raw ADTS `.aac`, so it is remuxed (no re-encode) into `.m4a` with
    ffmpeg; without ffmpeg the `.aac` is copied as-is. The original is never touched.
    """
    out_dir = cfg.diary_dir / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    m4a = out_dir / f"{audio.stem}.m4a"
    if m4a.exists():
        return m4a
    tmp = out_dir / f".{audio.stem}.tmp.m4a"
    if shutil.which("ffmpeg"):
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(audio), "-c", "copy", "-movflags", "+faststart", str(tmp)],
            capture_output=True,
        )
        if r.returncode == 0:
            tmp.replace(m4a)
            return m4a
        tmp.unlink(missing_ok=True)
    copy = out_dir / audio.name
    if not copy.exists():
        shutil.copy2(audio, copy)
    return copy


def fmt_duration(ms: int | None) -> str:
    if ms is None:
        return "?:??"
    s = round(ms / 1000)
    return f"{s // 60}:{s % 60:02d}"


def render_block(day: date, entries: list[Entry], gen: Generated, cfg: Config, links: list[str] = ()) -> str:
    lines = [BEGIN, f"# {day:%A}, {day.day} {day:%B %Y}", "", gen.diary or NO_LLM, ""]
    if links:  # people / places / projects / topics of the day -> Obsidian backlinks and graph
        lines += [memory.MENTIONS_PREFIX + " " + " · ".join(f"[[{n}]]" for n in links), ""]
    lines.append("## Notes")
    for e, summary in zip(entries, gen.summaries):
        t = e.note.started
        lines.append(f"- {t:%H:%M} · {fmt_duration(e.note.duration_ms)} — {audio_link(e.audio, cfg)} — {summary}")
    if not entries:
        lines.append("- (no notes this day)")
    if gen.todos:
        lines += ["", "## To-dos", ""] + [f"- [ ] {t}" for t in gen.todos]
    lines += ["", "## Transcripts", ""]
    for e in entries:
        lines.append(f"> [!quote]- {e.note.started:%H:%M} · {e.note.stem}")
        body = e.transcript or transcript_label(e.transcript)
        lines += [f"> {line}" if line else ">" for line in body.splitlines()]
        if e.feedback:
            lines += [">", f"> **Feedback:** {' '.join(e.feedback.split())}"]
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.S)


def merge(existing: str, day: date, count: int, block: str) -> str:
    """Update date/notes in the frontmatter (other keys kept) and replace only the marked block."""
    fm_lines, rest = [], existing
    if m := FRONTMATTER_RE.match(existing):
        fm_lines, rest = m.group(1).splitlines(), existing[m.end():]
    ours = {"date": day.isoformat(), "notes": str(count)}
    fm_lines = [ln for ln in fm_lines if ln.split(":", 1)[0].strip() not in ours]
    fm = "---\n" + "\n".join([f"{k}: {v}" for k, v in ours.items()] + fm_lines) + "\n---\n"

    start, end = rest.find(BEGIN), rest.find(END)
    if start != -1 and end > start:
        body = rest[:start] + block + rest[end + len(END):]
    else:
        body = block + "\n" + rest.lstrip("\n") if rest.strip() else block + "\n"
    return fm + body


def build(cfg: Config, store: Store, day: date) -> Path:
    if cfg.llm_provider not in PROVIDERS:
        raise ValueError(f"unknown llm_provider {cfg.llm_provider!r}; use one of {', '.join(PROVIDERS)}")
    entries = collect(store, day)
    if cfg.vault_dir:
        entries = [Entry(e.note, vault_copy(e.audio, cfg), e.transcript, e.feedback) for e in entries]
    page = cfg.diary_dir / f"{day.isoformat()}.md"
    gen = PROVIDERS[cfg.llm_provider](cfg, day, entries) if entries else Generated("_No notes recorded this day._", [])
    mem = memory.Memory(cfg, store, embedder=None)  # text only here; `serve` adds the vectors later
    if gen.raw:
        memory.safe_evidence(lambda: mem.after_diary(day, [e.note for e in entries], gen.raw))
    links = memory.safe_evidence(lambda: mem.day_links(day)) or []
    block = render_block(day, entries, gen, cfg, links if isinstance(links, list) else [])
    existing = page.read_text() if page.exists() else ""
    write_atomic(page, insights.restore_reflection(cfg, day, merge(existing, day, len(entries), block)).encode())
    record_moods(cfg, entries, gen)
    memory.safe_evidence(lambda: mem.index_day(day))
    return page


def record_moods(cfg: Config, entries: list[Entry], gen: Generated) -> None:
    """Quiet mood/energy bookkeeping piggybacked on the diary call (no extra LLM call). Talk-turn estimates
    for the same note are kept (insights.MoodLog.record)."""
    if not gen.moods:
        return
    try:
        log = insights.MoodLog(cfg.data_dir)
        for e, m in zip(entries, gen.moods):
            if m is not None:
                log.record(e.note, m, "diary")
    except Exception as e:  # noqa: BLE001 - the page is written; bookkeeping must not fail the build
        print(f"diary: mood estimates not stored: {e}")

