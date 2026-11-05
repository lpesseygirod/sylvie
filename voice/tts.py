"""Text to speech with Kokoro-82M (Apache-2.0) on MLX, one sentence at a time.

The Mac app asks for each sentence it is about to speak (POST /api/voice/speak) and plays the
WAV through its echo-cancelling audio pipeline; if this fails it falls back to the system voice.
Phonemes come from espeak-ng (voice/g2p.py). Decision T27; Lea approved the GPL parts.
"""

from __future__ import annotations

import io
import re
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from voice.g2p import G2P

DEFAULT_VOICES = {"en": "af_heart", "es": "ef_dora", "fr": "ff_siwis"}
# espeak reads "Lea" as "Lee" in English and French; spell names the way they should sound.
DEFAULT_RESPELL = {"en": {"Lea": "Leia"}, "es": {"Sylvie": "Silvi"}, "fr": {"Lea": "Léa"}}
MAX_PHONEMES = 500  # Kokoro's context is 512 tokens
FILES = ["config.json", "kokoro-v1_0.safetensors", "README.md", "voices/*.safetensors"]  # `make download`


@dataclass
class Speech:
    wav: bytes
    seconds: float
    ms: float  # time to synthesize


def to_wav(audio: np.ndarray, rate: int) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def chunk_phonemes(ps: str, limit: int = MAX_PHONEMES) -> list[str]:
    """Long sentences are cut at the last pause (punctuation, then space) before the limit."""
    out = []
    while len(ps) > limit:
        cut = max((ps.rfind(c, 0, limit) for c in ".!?;:,—"), default=-1)
        if cut <= 0:
            cut = ps.rfind(" ", 0, limit)
        cut = cut + 1 if cut > 0 else limit
        out.append(ps[:cut].strip())
        ps = ps[cut:].strip()
    return [p for p in out + [ps] if p]


class TextToSpeech:
    def __init__(
        self,
        model_id: str = "mlx-community/Kokoro-82M-bf16",
        voices: dict | None = None,
        speed: float = 1.0,
        respell: dict | None = None,
    ):
        self.model_id = model_id
        self.voices = {**DEFAULT_VOICES, **(voices or {})}
        self.respell = DEFAULT_RESPELL if respell is None else respell
        self.speed = float(speed)
        self.g2p = G2P()
        self._model = None
        self._packs: dict[str, object] = {}
        self._lock = threading.Lock()

    def _ensure(self):
        if self._model is None:
            from huggingface_hub import snapshot_download
            from mlx_audio.tts.utils import load_model

            self._path = snapshot_download(self.model_id, allow_patterns=FILES, local_files_only=True)
            self._model = load_model(Path(self._path), model_type="kokoro")
        return self._model

    def _pack(self, voice: str):
        if voice not in self._packs:
            from mlx_audio.tts.models.kokoro.voice import load_voice_tensor

            self._packs[voice] = load_voice_tensor(f"{self._path}/voices/{voice}.safetensors")
        return self._packs[voice]

    def load(self) -> None:
        with self._lock:
            self._ensure()

    def warm(self) -> None:
        for lang in self.voices:
            self.synthesize("Hola." if lang == "es" else "Bonjour." if lang == "fr" else "Hello.", lang)

    @property
    def sample_rate(self) -> int:
        return self._ensure().sample_rate

    def synthesize(self, text: str, language: str = "en") -> Speech:
        t0 = time.perf_counter()
        lang = language if language in self.voices else "en"
        for word, spoken in (self.respell.get(lang) or {}).items():
            text = re.sub(rf"\b{re.escape(word)}\b", spoken, text)
        ps = self.g2p(text, lang)
        with self._lock:
            model = self._ensure()
            pack = self._pack(self.voices[lang])
            parts = []
            for chunk in chunk_phonemes(ps):
                out = model(chunk, pack[len(chunk) - 1], self.speed, return_output=True)
                parts.append(np.array(out.audio, dtype=np.float32).reshape(-1))
        audio = np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)
        rate = model.sample_rate
        return Speech(to_wav(audio, rate), len(audio) / rate, (time.perf_counter() - t0) * 1000)
