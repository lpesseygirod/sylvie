"""Turns text into vectors for memory and document search.

Uses Qwen3-Embedding (multilingual, EN/ES/FR) through mlx-lm. Pooling: the hidden state of
the last token (<|endoftext|>, which the tokenizer appends), L2-normalised — this is how
Qwen3-Embedding is meant to be used. Queries get a short instruction prefix; documents don't.
"""

from __future__ import annotations

import hashlib
import re
import struct
import threading

import numpy as np

QUERY_INSTRUCTION = (
    "Instruct: Given a question or message, retrieve relevant notes that help answer it\nQuery:"
)


def to_blob(vec: np.ndarray) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec.astype(np.float32).tolist())


class Embedder:
    def __init__(self, model_id: str, dim: int = 1024, max_tokens: int = 512):
        self.model_id = model_id
        self.dim = dim
        self.max_tokens = max_tokens
        self._model = None
        self._tok = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._model is None:
            from mlx_lm import load

            self._model, self._tok = load(self.model_id)

    def unload(self) -> None:
        self._model, self._tok = None, None  # reloads on next use

    def embed(self, texts: list[str], query: bool = False, batch_size: int = 16) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        import mlx.core as mx

        with self._lock:
            self._ensure()
            out = []
            for i in range(0, len(texts), batch_size):
                batch = [(QUERY_INSTRUCTION + t) if query else t for t in texts[i : i + batch_size]]
                ids = [self._tok.encode(t)[-self.max_tokens :] for t in batch]
                lengths = [len(x) for x in ids]
                width = max(lengths)
                pad = self._tok.pad_token_id or 0
                arr = mx.array([x + [pad] * (width - len(x)) for x in ids])
                # Right padding is safe: attention is causal, so pads after the last real token
                # can't change its hidden state.
                hidden = self._model.model(arr)
                idx = mx.array([n - 1 for n in lengths])
                last = hidden[mx.arange(len(ids)), idx]
                last = last / mx.linalg.norm(last, axis=-1, keepdims=True)
                out.append(np.array(last.astype(mx.float32)))
            return np.concatenate(out)[:, : self.dim]

    def embed_one(self, text: str, query: bool = False) -> np.ndarray:
        return self.embed([text], query=query)[0]


class HashEmbedder:
    """Deterministic bag-of-words embedder for tests (no model needed)."""

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def embed(self, texts: list[str], query: bool = False, batch_size: int = 16) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, t in enumerate(texts):
            for w in re.findall(r"\w+", t.lower()):
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                out[row, h % self.dim] += 1.0
            n = np.linalg.norm(out[row])
            if n:
                out[row] /= n
        return out

    def embed_one(self, text: str, query: bool = False) -> np.ndarray:
        return self.embed([text], query=query)[0]
