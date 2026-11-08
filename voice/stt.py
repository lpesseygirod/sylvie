"""Speech to text with Whisper large-v3-turbo (mlx-whisper), limited to English, Spanish and French.

Audio arrives as WAV bytes (16-bit PCM or float, any rate — resampled to 16 kHz) and is only
ever held in memory: nothing is written to disk, so "deleted right after transcription"
is automatic (plan, Phase 2).
"""

from __future__ import annotations

import io
import re
import threading
import time
import wave
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000
LANGUAGES = ("en", "es", "fr")
# A short hint in Whisper's "previous text" slot: teaches it the spelling of "Sylvie" and that Lea
# mixes languages. Measured on test audio: fixed "Silvie" and a garbled ES/EN mixed sentence.
INITIAL_PROMPT = "Sylvie, Lea. Hola Sylvie, can you check esto? Oui, merci Sylvie."


# What Whisper writes when it hears music or near-silence (subtitle credits from its training data).
# Measured 2026-11-08: 30 s of quiet or ducked music came back as "Sylvie. Merci.", "Sylvie. Thank
# you." and "Sous-titrage ST' 501 So, boy.", and Sylvie answered them as if Lea had spoken.
NOISE_RE = re.compile(
    r"sous-?titr|amara\.org|subtitles? by|thanks? (you )?for watching|suscr[ií]bete|gracias por ver|"
    r"merci d'avoir regard",
    re.I,
)
BARE_THANKS = {
    "merci",
    "thank you",
    "thanks",
    "gracias",
    "you",
    "bye",
    "merci beaucoup",
    "thank you very much",
}
LONG_RECORDING_S = 15  # a real "Sylvie, merci" ends in a few seconds; 15 s+ of it is noise


def is_noise_transcript(text: str, seconds_audio: float) -> bool:
    if NOISE_RE.search(text):
        return True
    rest = re.sub(r"^\W*sylvie\W*", "", text.strip(), flags=re.I).strip(" .!?¡¿,").lower()
    return not rest or (rest in BARE_THANKS and seconds_audio >= LONG_RECORDING_S)


@dataclass
class Transcript:
    text: str
    language: str
    seconds_audio: float
    ms: float


def wav_to_float32(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data)) as w:
        rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        frames = w.readframes(w.getnframes())
    if width == 2:
        audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        audio = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        audio = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    else:
        raise ValueError(f"unsupported sample width {width}")
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        n = int(len(audio) * SAMPLE_RATE / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    return audio


class SpeechToText:
    def __init__(self, model_id: str = "mlx-community/whisper-large-v3-turbo", languages=LANGUAGES):
        self.model_id = model_id
        self.languages = tuple(languages)
        self._model = None
        self._lock = threading.Lock()

    def _ensure(self):
        # Use mlx_whisper's own model holder: transcribe() loads through it, so this keeps one
        # copy of Whisper in memory instead of two.
        if self._model is None:
            import mlx.core as mx
            from mlx_whisper.transcribe import ModelHolder

            self._model = ModelHolder.get_model(self.model_id, mx.float16)
        return self._model

    def load(self) -> None:
        with self._lock:
            self._ensure()

    def unload(self) -> None:
        with self._lock:
            from mlx_whisper.transcribe import ModelHolder

            self._model = None
            ModelHolder.model, ModelHolder.model_path = None, None

    def detect_language(self, audio: np.ndarray) -> str:
        """Whisper's language detection, restricted to Lea's languages."""
        import mlx.core as mx
        from mlx_whisper.audio import N_FRAMES, log_mel_spectrogram, pad_or_trim
        from mlx_whisper.decoding import detect_language

        model = self._ensure()
        mel = log_mel_spectrogram(audio, n_mels=model.dims.n_mels)
        mel = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
        _, probs = detect_language(model, mel)
        return max(self.languages, key=lambda lang: probs.get(lang, 0.0))

    def transcribe(self, audio: np.ndarray | bytes, language: str | None = None) -> Transcript:
        if isinstance(audio, bytes):
            audio = wav_to_float32(audio)
        t0 = time.perf_counter()
        if len(audio) < SAMPLE_RATE * 0.3 or float(np.abs(audio).max(initial=0)) < 0.01:
            return Transcript("", language or "en", len(audio) / SAMPLE_RATE, 0.0)  # too short or silent
        with self._lock:
            import mlx_whisper

            self._ensure()
            lang = language if language in self.languages else self.detect_language(audio)
            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=self.model_id,
                language=lang,
                initial_prompt=INITIAL_PROMPT,
                condition_on_previous_text=False,
                temperature=(0.0, 0.2, 0.4),
                verbose=None,
            )
        text = result["text"].strip()
        if text and text.lower().strip(" .") in INITIAL_PROMPT.lower():
            text = ""  # Whisper echoing its hint on near-silence
        if text and is_noise_transcript(text, len(audio) / SAMPLE_RATE):
            text = ""  # music or silence written down as subtitle credits / a bare "thank you"
        return Transcript(
            text=text,
            language=lang,
            seconds_audio=len(audio) / SAMPLE_RATE,
            ms=(time.perf_counter() - t0) * 1000,
        )
