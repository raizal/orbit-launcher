"""Config: config.toml + ORBIT_* env overrides. The token is generated on first run and persisted."""

from __future__ import annotations

import os
import re
import secrets
import socket
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

LOCAL_CONFIG = Path("config.toml")
USER_CONFIG = Path("~/.config/orbit-notes/config.toml").expanduser()

TEMPLATE = """\
# Orbit Notes companion config. Relative paths are resolved against this file's folder.
# Every key can be overridden by an env var: ORBIT_<KEY> (e.g. ORBIT_TOKEN, ORBIT_PORT).
name = "{name}"
port = 8765
host = "0.0.0.0"
# data_dir = "~/.local/share/orbit-notes"
# vault_dir = "~/Obsidian/MyVault"        # unset -> diary pages go to <data_dir>/diary/
diary_folder = "Diary"
whisper_model = "small"                  # tiny / base / small / medium / large-v3
diary_language = "auto"                  # "auto" = same language as the notes, or e.g. "English"
llm_provider = "none"                    # "none", "codex" (your `codex` CLI login) or "anthropic" (ANTHROPIC_API_KEY)
llm_model = "claude-sonnet-5-5"          # for "anthropic"
codex_model = ""                         # for "codex"; empty = Codex default
# Talk (spoken feedback for notes recorded in Talk mode on the watch, see docs/talk.md)
talk_provider = "codex"                  # "codex" (your `codex` CLI login) or "none"
tts_voice = "en_US-lessac-medium"        # Piper voice, downloaded once into <data_dir>/voices/
talk_timeout_s = 120
orbit_project = "proj_puvrp7a3i8"          # BB project of Orbit itself
orbit_orchestrator_thread = "thr_834czrmc82"  # threads spawned in orbit_project become its children ("" = off)
talk_workspace = "bb"                    # "bb": Talk can read your BB workspaces and propose actions (confirmed by voice); "none"
# talk_workspaces = ["personal", "work"]  # restrict to these; default: every ~/.bb (personal) / ~/.bb-<name> bb data dir
# orbit_workspace = "personal"            # the workspace orbit_project lives in
# [[workspaces]]                          # instead of discovery (put at the END of this file: TOML tables come last)
# name = "work"
# data_dir = "~/.bb-work"
# server_url = "http://127.0.0.1:39886"  # optional; default: read from <data_dir>/bb-app-runtime.json every call
# Memory & recall (docs/memory.md): local multilingual embeddings, downloaded once into ~/.cache/huggingface
memory_embedder = "multilingual-e5-small"  # or "none" (keyword search only)
# memory_page = "~/Obsidian/MyVault/Orbit/Memory.md"   # unset -> <diary folder>/../Orbit/Memory.md
"""


@dataclass
class Config:
    path: Path
    name: str = socket.gethostname().split(".")[0]
    port: int = 8765
    host: str = "0.0.0.0"
    data_dir: Path = Path("~/.local/share/orbit-notes").expanduser()
    vault_dir: Path | None = None
    diary_folder: str = "Diary"
    token: str = ""
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    diary_language: str = "auto"
    llm_provider: str = "none"
    codex_model: str = ""
    codex_timeout_s: int = 300
    llm_model: str = "claude-sonnet-5-5"
    talk_provider: str = "codex"
    tts_voice: str = "en_US-lessac-medium"
    talk_timeout_s: int = 120
    talk_workspace: str = "bb"
    talk_workspaces: list[str] = field(default_factory=list)  # empty = all discovered (docs/talk.md)
    workspaces: list[dict] = field(default_factory=list)  # [[workspaces]] name / data_dir / server_url
    orbit_workspace: str = "personal"
    orbit_project: str = "proj_puvrp7a3i8"
    orbit_orchestrator_thread: str = "thr_834czrmc82"
    memory_embedder: str = "multilingual-e5-small"
    memory_page: Path | None = None

    @property
    def diary_dir(self) -> Path:
        if self.vault_dir:
            return self.vault_dir / self.diary_folder
        return self.data_dir / "diary"


def find_config(explicit: Path | None) -> Path:
    if explicit:
        return explicit
    if LOCAL_CONFIG.exists():
        return LOCAL_CONFIG
    return USER_CONFIG


def set_key(path: Path, key: str, value: str) -> None:
    """Set a top-level string key in a TOML file, keeping everything else (comments included)."""
    line = f'{key} = "{value}"'
    text = path.read_text() if path.exists() else ""
    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.M)
    text = pattern.sub(line, text, count=1) if pattern.search(text) else f"{line}\n{text}"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def load(explicit: Path | None = None, *, create: bool = True) -> Config:
    path = find_config(explicit).expanduser().resolve()
    if not path.exists() and create:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE.format(name=Config.name))
        os.chmod(path, 0o600)
    raw = tomllib.loads(path.read_text()) if path.exists() else {}

    cfg = Config(path=path)
    for f in fields(Config):
        if f.name == "path":
            continue
        value = os.environ.get(f"ORBIT_{f.name.upper()}", raw.get(f.name))
        if f.name == "workspaces":  # tables: config file only
            value = raw.get(f.name)
            if not isinstance(value, list):
                continue
        if value is None or value == "":
            continue
        if f.name == "talk_workspaces":
            value = [str(x).strip() for x in (value.split(",") if isinstance(value, str) else value) if str(x).strip()]
        elif f.name in ("port", "codex_timeout_s", "talk_timeout_s"):
            value = int(value)
        elif f.name in ("data_dir", "vault_dir", "memory_page"):
            p = Path(str(value)).expanduser()
            value = p if p.is_absolute() else (path.parent / p).resolve()
        setattr(cfg, f.name, value)

    if not cfg.token:
        cfg.token = secrets.token_urlsafe(32)
        set_key(path, "token", cfg.token)
    return cfg
