"""Transcription with faster-whisper (imported lazily: the server and tests run without a model)."""

from __future__ import annotations

import itertools
import json
import queue
import threading
import time
import traceback

from .config import Config
from .store import TALK_ACTIVE, Note, Store, write_atomic
from .talk import is_stale


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
    """
    Background thread inside `serve`: transcribes pending notes, then new ones as they arrive.
    Talk notes jump the queue, and after their transcript the talk pipeline runs (talk.Talker).
    """

    TALK, NORMAL, STOP = 0, 1, 2  # queue priorities, lower first; stop() comes after queued work (as before)

    def __init__(self, store: Store, transcriber: Transcriber, talker=None):
        self.store, self.transcriber, self.talker = store, transcriber, talker
        self.queue: queue.PriorityQueue[tuple[int, int, str | None]] = queue.PriorityQueue()
        self._seq = itertools.count()  # FIFO within a priority
        self.thread = threading.Thread(target=self._loop, name="transcriber", daemon=True)

    def start(self) -> None:
        for note in self.store.pending_talk():  # interrupted talk turns (e.g. a restart mid-way)
            self.enqueue(note.id)
        for note in self.store.pending_transcripts():
            if not note.is_talk:
                self.enqueue(note.id)
        self.thread.start()

    def enqueue(self, note_id: str) -> None:
        note = self.store.get(note_id)
        talk = bool(note and note.is_talk and note.talk_status in TALK_ACTIVE)
        if talk and (self.talker is None or is_stale(note)):
            # Feedback only while it is still the same day; the note itself is transcribed as usual.
            reason = "talk feedback is off on this server" if self.talker is None else "recorded on an earlier day"
            self.store.set_talk_status(note_id, "error", f"no feedback: {reason}")
            talk = False
        self.queue.put((self.TALK if talk else self.NORMAL, next(self._seq), note_id))

    def stop(self) -> None:
        self.queue.put((self.STOP, next(self._seq), None))

    def _loop(self) -> None:
        while (note_id := self.queue.get()[2]) is not None:
            note = self.store.get(note_id)
            if not note:
                continue
            talk = note.is_talk and self.talker is not None and note.talk_status in TALK_ACTIVE
            if note.transcript_status != "done":
                if talk:
                    self.store.set_talk_status(note.id, "transcribing")
                print(f"whisper: transcribing {note.path}")
                t = time.monotonic()
                ok = self.transcriber.run(self.store, note)
                print(f"whisper: {note.stem} {time.monotonic() - t:.1f}s")
                if not ok:
                    if talk:
                        self.store.set_talk_status(note.id, "error", "transcription failed (the note itself is safe)")
                    continue
            if talk:
                self.talker.run(self.store.get(note_id))
