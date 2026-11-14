"""Transcription with faster-whisper (imported lazily: the server and tests run without a model)."""

from __future__ import annotations

import json
import queue
import threading
import traceback

from .config import Config
from .store import Note, Store, write_atomic


class Transcriber:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # heavy import, first use only

            print(f"whisper: loading model {self.cfg.whisper_model!r} ({self.cfg.whisper_device}/{self.cfg.whisper_compute_type})")
            self._model = WhisperModel(
                self.cfg.whisper_model, device=self.cfg.whisper_device, compute_type=self.cfg.whisper_compute_type
            )
        return self._model

    def transcribe(self, store: Store, note: Note) -> str:
        """Write <stem>.txt and <stem>.segments.json next to the audio; return the text."""
        audio = store.audio_path(note)
        segments, info = self.model.transcribe(str(audio), language=None, vad_filter=True)  # None = auto-detect
        segs = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments]
        text = " ".join(s["text"] for s in segs if s["text"])
        doc = {"language": info.language, "language_probability": round(info.language_probability, 3),
               "duration": round(info.duration, 2), "segments": segs}
        write_atomic(audio.with_suffix(".segments.json"), json.dumps(doc, ensure_ascii=False, indent=1).encode())
        write_atomic(audio.with_suffix(".txt"), (text + "\n").encode())
        store.set_transcript_status(note.id, "done")
        return text

    def run(self, store: Store, note: Note) -> bool:
        try:
            self.transcribe(store, note)
            return True
        except Exception as e:  # keep going; the note stays 'failed' and is retried next start / `transcribe`
            traceback.print_exc()
            store.set_transcript_status(note.id, "failed", repr(e))
            return False


class Worker:
    """Background thread inside `serve`: transcribes pending notes, then new ones as they arrive."""

    def __init__(self, store: Store, transcriber: Transcriber):
        self.store, self.transcriber = store, transcriber
        self.queue: queue.Queue[str | None] = queue.Queue()
        self.thread = threading.Thread(target=self._loop, name="transcriber", daemon=True)

    def start(self) -> None:
        for note in self.store.pending_transcripts():
            self.queue.put(note.id)
        self.thread.start()

    def enqueue(self, note_id: str) -> None:
        self.queue.put(note_id)

    def stop(self) -> None:
        self.queue.put(None)

    def _loop(self) -> None:
        while (note_id := self.queue.get()) is not None:
            note = self.store.get(note_id)
            if note and note.transcript_status != "done":
                print(f"whisper: transcribing {note.path}")
                self.transcriber.run(self.store, note)
