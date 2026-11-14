"""Build one Markdown diary page per local date. Only the part between the orbit markers is rewritten."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config import Config
from .store import Note, Store, write_atomic

BEGIN, END = "<!-- orbit:begin -->", "<!-- orbit:end -->"
NO_LLM = '_No LLM configured (`llm_provider = "none"`), so no diary text was written. The notes and transcripts are below._'
FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


@dataclass
class Entry:
    note: Note
    audio: Path
    transcript: str | None  # None = not transcribed yet


@dataclass
class Generated:
    diary: str
    summaries: list[str]  # one per entry, same order


def collect(store: Store, day: date) -> list[Entry]:
    entries = []
    for note in store.notes_on(day):
        audio = store.audio_path(note)
        txt = audio.with_suffix(".txt")
        entries.append(Entry(note, audio, txt.read_text().strip() if txt.exists() else None))
    return entries


# --- LLM providers -------------------------------------------------------------------------------


def first_words(text: str | None, limit: int = 80) -> str:
    if not text:
        return "(not transcribed yet)"
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
Also give a one-line summary (max ~12 words) for each note, in the same language as the diary."""


def generate_anthropic(cfg: Config, day: date, entries: list[Entry]) -> Generated:
    import anthropic  # optional at runtime: only needed for this provider

    language = (
        "Write in the language the notes are mostly spoken in."
        if cfg.diary_language.lower() == "auto"
        else f"Write in {cfg.diary_language}."
    )
    notes = "\n\n".join(
        f"[note {i}] {e.note.started:%H:%M}\n{e.transcript or '(not transcribed yet)'}" for i, e in enumerate(entries)
    )
    schema = {
        "type": "object",
        "properties": {
            "diary": {"type": "string"},
            "summaries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"note": {"type": "integer"}, "summary": {"type": "string"}},
                    "required": ["note", "summary"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["diary", "summaries"],
        "additionalProperties": False,
    }
    extra = {}
    if cfg.llm_model in FALLBACK_MODELS:  # on a safety refusal, the API re-runs the request on a fallback model
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY (or an `ant auth login` profile)
    response = client.beta.messages.create(
        model=cfg.llm_model,
        max_tokens=16000,
        system=SYSTEM_PROMPT.format(language=language),
        messages=[{"role": "user", "content": f"Voice notes from {day:%A, %d %B %Y}, in time order:\n\n{notes}"}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
        **extra,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined to write this diary page")
    data = json.loads(next(b.text for b in response.content if b.type == "text"))
    by_note = {s["note"]: s["summary"].strip() for s in data["summaries"]}
    return Generated(data["diary"].strip(), [by_note.get(i) or first_words(e.transcript) for i, e in enumerate(entries)])


PROVIDERS = {"none": generate_none, "anthropic": generate_anthropic}


# --- rendering -----------------------------------------------------------------------------------


def audio_link(audio: Path, cfg: Config) -> str:
    """Vault-relative wikilink when the audio is inside the vault, else an absolute file:// link."""
    if cfg.vault_dir and audio.resolve().is_relative_to(cfg.vault_dir.resolve()):
        return f"[[{audio.resolve().relative_to(cfg.vault_dir.resolve()).as_posix()}]]"
    return f"[{audio.name}](<{audio.resolve().as_uri()}>)"


def fmt_duration(ms: int | None) -> str:
    if ms is None:
        return "?:??"
    s = round(ms / 1000)
    return f"{s // 60}:{s % 60:02d}"


def render_block(day: date, entries: list[Entry], gen: Generated, cfg: Config) -> str:
    lines = [BEGIN, f"# {day:%A}, {day.day} {day:%B %Y}", "", gen.diary or NO_LLM, "", "## Notes"]
    for e, summary in zip(entries, gen.summaries):
        t = e.note.started
        lines.append(f"- {t:%H:%M} · {fmt_duration(e.note.duration_ms)} — {audio_link(e.audio, cfg)} — {summary}")
    if not entries:
        lines.append("- (no notes this day)")
    lines += ["", "## Transcripts", ""]
    for e in entries:
        lines.append(f"> [!quote]- {e.note.started:%H:%M} · {e.note.stem}")
        body = e.transcript or "(not transcribed yet)"
        lines += [f"> {line}" if line else ">" for line in body.splitlines()]
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
    page = cfg.diary_dir / f"{day.isoformat()}.md"
    gen = PROVIDERS[cfg.llm_provider](cfg, day, entries) if entries else Generated("_No notes recorded this day._", [])
    block = render_block(day, entries, gen, cfg)
    existing = page.read_text() if page.exists() else ""
    write_atomic(page, merge(existing, day, len(entries), block).encode())
    return page

