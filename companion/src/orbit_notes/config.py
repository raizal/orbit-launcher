"""Config: config.toml + ORBIT_* env overrides. The token is generated on first run and persisted."""

from __future__ import annotations

import os
import re
import secrets
import socket
import tomllib
from dataclasses import dataclass, fields
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
llm_provider = "none"                    # "none" or "anthropic" (key from ANTHROPIC_API_KEY)
llm_model = "claude-sonnet-5-5"
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
    llm_model: str = "claude-sonnet-5-5"

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
        if value is None or value == "":
            continue
        if f.name == "port":
            value = int(value)
        elif f.name in ("data_dir", "vault_dir"):
            p = Path(str(value)).expanduser()
            value = p if p.is_absolute() else (path.parent / p).resolve()
        setattr(cfg, f.name, value)

    if not cfg.token:
        cfg.token = secrets.token_urlsafe(32)
        set_key(path, "token", cfg.token)
    return cfg
