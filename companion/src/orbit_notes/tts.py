"""Text-to-speech with Piper (`piper-tts`), imported lazily. Voices are downloaded once into <data_dir>/voices/."""

from __future__ import annotations

import io
import shutil
import tempfile
import threading
import wave
from pathlib import Path

from .config import Config
from .store import write_atomic


class PiperTTS:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.voices_dir = cfg.data_dir / "voices"
        self._voice = None
        self._lock = threading.Lock()

    def model_path(self) -> Path:
        """The voice's .onnx, downloaded on first use (into a temp dir, then moved, so a cut download never sticks)."""
        name = self.cfg.tts_voice
        model = self.voices_dir / f"{name}.onnx"
        config = self.voices_dir / f"{name}.onnx.json"
        if model.exists() and config.exists():
            return model
        from piper.download_voices import download_voice

        self.voices_dir.mkdir(parents=True, exist_ok=True)
        print(f"piper: downloading voice {name!r} into {self.voices_dir}")
        with tempfile.TemporaryDirectory(dir=self.voices_dir, prefix=".download-") as tmp:
            download_voice(name, Path(tmp))
            shutil.move(Path(tmp) / config.name, config)
            shutil.move(Path(tmp) / model.name, model)
        return model

    @property
    def voice(self):
        with self._lock:
            if self._voice is None:
                from piper import PiperVoice  # heavy import (onnxruntime), first use only

                self._voice = PiperVoice.load(self.model_path())
            return self._voice

    def synthesize(self, text: str, out: Path) -> None:
        """Write mono 16-bit WAV of `text` to `out` (atomically)."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            self.voice.synthesize_wav(text, wf)
        write_atomic(out, buf.getvalue())
