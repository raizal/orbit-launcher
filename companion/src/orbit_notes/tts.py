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
        self._voices: dict[str, object] = {}
        self._lock = threading.Lock()

    def model_path(self, name: str | None = None) -> Path:
        """The voice's .onnx, downloaded on first use (into a temp dir, then moved, so a cut download never sticks)."""
        name = name or self.cfg.tts_voice
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

    def voice(self, name: str | None = None):
        name = name or self.cfg.tts_voice
        with self._lock:
            if name not in self._voices:
                from piper import PiperVoice  # heavy import (onnxruntime), first use only

                self._voices[name] = PiperVoice.load(self.model_path(name))
            return self._voices[name]

    def synthesize(self, text: str, out: Path, voice: str | None = None, length_scale: float | None = None,
                   strict: bool = False) -> None:
        """Write mono 16-bit WAV of `text` to `out` (atomically). An unusable voice falls back to the config's,
        unless `strict` (voice previews: hearing the wrong voice would mislead)."""
        from piper import SynthesisConfig

        try:
            v = self.voice(voice)
        except Exception as e:  # noqa: BLE001 - e.g. download failed: still answer, in the default voice
            if strict:
                raise
            print(f"piper: voice {voice!r} unavailable ({e}); using {self.cfg.tts_voice!r}")
            v = self.voice(None)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            v.synthesize_wav(text, wf, syn_config=SynthesisConfig(length_scale=length_scale))
        write_atomic(out, buf.getvalue())
