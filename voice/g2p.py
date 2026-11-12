"""Text → Kokoro phonemes with espeak-ng, for English, Spanish and French.

Adapted from misaki/espeak.py (hexgrad/misaki, Apache-2.0): the espeak → Kokoro phoneme mappings
below are theirs. Vendored instead of installed because misaki's package metadata excludes
Python 3.13, and its full English pipeline needs spaCy + PyTorch; espeak-ng covers all three of
Lea's languages (docs/decisions.md, T27). espeak-ng (via espeakng-loader) and phonemizer-fork are
GPL-3.0; I accepted that for private use (docs/LICENSES.md).
"""

from __future__ import annotations

import re
import threading

ESPEAK_LANG = {"en": "en-us", "es": "es", "fr": "fr-fr"}

# English: espeak → misaki's American-English phoneme set (misaki EspeakFallback)
_EN = sorted(
    {
        "ʔˌn̩": "ʔn", "ʔn̩": "ʔn", "a^ɪ": "I", "a^ʊ": "W", "d^ʒ": "ʤ", "e^ɪ": "A", "e": "A",
        "t^ʃ": "ʧ", "ɔ^ɪ": "Y", "ə^l": "ᵊl", "ʲo": "jo", "ʲə": "jə", "ʲ": "", "ɚ": "əɹ", "r": "ɹ",
        "x": "k", "ç": "k", "ɐ": "ə", "ɬ": "l", "̃": "",
    }.items(),
    key=lambda kv: -len(kv[0]),
)  # fmt: skip

# Other languages (misaki EspeakG2P)
_OTHER = sorted(
    {
        "a^ɪ": "I", "a^ʊ": "W", "d^z": "ʣ", "d^ʒ": "ʤ", "e^ɪ": "A", "o^ʊ": "O", "ə^ʊ": "Q",
        "s^s": "S", "t^s": "ʦ", "t^ʃ": "ʧ", "ɔ^ɪ": "Y",
    }.items()
)  # fmt: skip


def map_english(ps: str) -> str:
    for old, new in _EN:
        ps = ps.replace(old, new)
    ps = re.sub(r"(\S)̩", r"ᵊ\1", ps).replace(chr(809), "")
    ps = ps.replace("o^ʊ", "O").replace("ɜːɹ", "ɜɹ").replace("ɜː", "ɜɹ").replace("ɪə", "iə").replace("ː", "")
    ps = ps.replace("o", "ɔ").replace("ɾ", "T").replace("ʔ", "t")
    return ps.replace("^", "")


def map_other(ps: str) -> str:
    for old, new in _OTHER:
        ps = ps.replace(old, new)
    return ps.replace("^", "").replace("-", "")


class G2P:
    """One espeak backend per language, created on first use. Thread-safe (espeak is not)."""

    def __init__(self):
        self._backends: dict[str, object] = {}
        self._lock = threading.Lock()

    def _backend(self, lang: str):
        if lang not in self._backends:
            import espeakng_loader
            from phonemizer.backend import EspeakBackend
            from phonemizer.backend.espeak.wrapper import EspeakWrapper

            EspeakWrapper.set_library(espeakng_loader.get_library_path())
            EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
            self._backends[lang] = EspeakBackend(
                language=ESPEAK_LANG[lang], preserve_punctuation=True, with_stress=True,
                tie="^", language_switch="remove-flags",
            )  # fmt: skip
        return self._backends[lang]

    def __call__(self, text: str, lang: str) -> str:
        lang = lang if lang in ESPEAK_LANG else "en"
        text = text.replace("«", "“").replace("»", "”").replace("(", "«").replace(")", "»")
        with self._lock:
            out = self._backend(lang).phonemize([text])
        if not out:
            return ""
        ps = out[0].strip()
        ps = map_english(ps) if lang == "en" else map_other(ps)
        return ps.replace("«", "(").replace("»", ")")
