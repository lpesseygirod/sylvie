"""Turns a streamed text answer into speakable sentences, as soon as each sentence is complete.

The spoken answer is short: only the first paragraph (at most a few sentences) is spoken;
the full answer is on screen. Markdown, code and citation markers are removed for speech.
"""

from __future__ import annotations

import re

MAX_SPOKEN_SENTENCES = 4
_SENTENCE_END = re.compile(r"[.!?…]+(?=\s|$)")
# Abbreviations that end with a dot but don't end a sentence.
_ABBREV = re.compile(r"\b(e\.g|i\.e|etc|vs|dr|mr|mrs|ms|sr|sra|mme|p\.ej|approx|aprox|nº)\.$", re.I)


def clean_for_speech(text: str) -> str:
    t = re.sub(r"```.*?(```|$)", " ", text, flags=re.S)  # code isn't spoken
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\[(\d{1,2})\]", "", t)  # citation markers
    t = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", t)  # links → their text
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_~#>|]+", "", t)
    t = re.sub(r"^\s*[-+]\s+", "", t, flags=re.M)
    t = re.sub(r"\s+", " ", t)
    return re.sub(r"\s+([.,!?;:…])", r"\1", t).strip()


def split_sentences(text: str, final: bool) -> tuple[list[str], str]:
    """Returns (complete sentences, leftover). Without `final`, a sentence end at the very end
    of the text isn't trusted yet (the next chunk might be "5" after "3.")."""
    out, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        if not final and m.end() == len(text):
            break
        piece = text[start : m.end()]
        if _ABBREV.search(piece.strip()):
            continue
        if re.search(r"\d\.$", piece) and m.end() < len(text) and text[m.end() : m.end() + 1].isdigit():
            continue
        if clean_for_speech(piece):
            out.append(clean_for_speech(piece))
        start = m.end()
    rest = text[start:]
    if final and clean_for_speech(rest):
        out.append(clean_for_speech(rest))
        rest = ""
    return out, rest


class SentenceStreamer:
    """feed(chunk) → list of complete sentences ready to speak; flush() at the end."""

    def __init__(self, max_sentences: int = MAX_SPOKEN_SENTENCES):
        self.buf = ""
        self.spoken = 0
        self.max = max_sentences
        self.done = False

    def _take(self, sentences: list[str]) -> list[str]:
        room = self.max - self.spoken
        sentences = sentences[:room]
        self.spoken += len(sentences)
        if self.spoken >= self.max:
            self.done = True
        return sentences

    def feed(self, chunk: str) -> list[str]:
        if self.done:
            return []
        self.buf += chunk
        if "\n\n" in self.buf:
            head, _, rest = self.buf.partition("\n\n")
            sentences, _ = split_sentences(head, final=True)
            out = self._take(sentences)
            if self.spoken:  # the first paragraph with words in it is the spoken answer
                self.done = True
                self.buf = ""
                return out
            self.buf = rest
            return out + self.feed("")
        if self.buf.lstrip().startswith(("```", "|")):
            self.done = self.spoken > 0
            return []
        sentences, self.buf = split_sentences(self.buf, final=False)
        return self._take(sentences)

    def flush(self) -> list[str]:
        if self.done:
            return []
        sentences, self.buf = split_sentences(self.buf, final=True)
        self.done = True
        return self._take(sentences)
