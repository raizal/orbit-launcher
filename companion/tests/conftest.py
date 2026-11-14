"""Shared test setup: memory uses a tiny deterministic fake embedder (no model download in tests)."""

import hashlib

import numpy as np
import pytest

from orbit_notes import memory

SYNONYMS = {"kucing": "cat", "kucingku": "cat", "perjalanan": "trip", "travel": "trip", "batal": "cancel"}


class FakeEmbedder:
    """Bag of content words hashed into 512 dims (+ a few Indonesian->English synonyms), unit length."""

    name, dim, _session = "fake", 512, True

    def load(self):
        pass

    def embed(self, texts, kind="passage"):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in memory.content_words(t):
                w = SYNONYMS.get(w, w)
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1
            out[i, 0] += 0.05  # never a zero vector
            out[i] /= np.linalg.norm(out[i])
        return out


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    monkeypatch.setattr(memory, "make_embedder", lambda cfg: FakeEmbedder())
