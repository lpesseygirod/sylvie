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


_DOC_BLOCK = re.compile(r"<doc\b[^>]*>.*?(?:</doc>|$)", re.S | re.I)
_DOC_OPEN = re.compile(r"<doc[\s>]", re.I)


def strip_docs(text: str) -> str:
    """Removes <doc …>…</doc> blocks: things Sylvie made to read and edit on screen, not to say."""
    return _DOC_BLOCK.sub(" ", text)


class DocFilter:
    """Streaming version of strip_docs: feed(chunk) returns only the text outside doc blocks,
    holding back a tag that may be split across chunks."""

    def __init__(self):
        self.buf = ""
        self.inside = False

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        out = ""
        while True:
            if self.inside:
                i = self.buf.lower().find("</doc>")
                if i < 0:
                    self.buf = self.buf[-5:]  # might be the start of "</doc>"
                    return out
                self.buf, self.inside = self.buf[i + 6 :], False
                continue
            m = _DOC_OPEN.search(self.buf)
            if m:
                end = self.buf.find(">", m.start())
                if end < 0:
                    out, self.buf = out + self.buf[: m.start()], self.buf[m.start() :]
                    return out
                out, self.buf, self.inside = out + self.buf[: m.start()], self.buf[end + 1 :], True
                continue
            j = self.buf.rfind("<")
            if j >= 0 and len(self.buf) - j <= 4 and "<doc".startswith(self.buf[j:].lower()):
                out, self.buf = out + self.buf[:j], self.buf[j:]
                return out
            out, self.buf = out + self.buf, ""
            return out

    def flush(self) -> str:
        out = "" if self.inside else self.buf
        self.buf, self.inside = "", False
        return out


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

    def __init__(self, max_sentences: int = MAX_SPOKEN_SENTENCES, full: bool = False):
        self.buf = ""
        self.spoken = 0
        self.max = max_sentences
        self.done = False
        # full: speak the whole answer (Lea's rule, 2026-11-06: "don't cut yourself off unless I say
        # stop"). Code blocks and tables are still skipped: they can't be read aloud sensibly.
        self.full = full
        self.in_code = False

    def _take(self, sentences: list[str]) -> list[str]:
        room = self.max - self.spoken
        sentences = sentences[:room]
        self.spoken += len(sentences)
        if self.spoken >= self.max:
            self.done = True
        return sentences

    def _speakable(self, paragraph: str) -> bool:
        fences = paragraph.count("```")
        was_code = self.in_code
        if fences % 2:
            self.in_code = not self.in_code
        return not (was_code or fences or paragraph.lstrip().startswith("|"))

    def _feed_full(self, final: bool = False) -> list[str]:
        out = []
        while "\n\n" in self.buf:
            head, _, self.buf = self.buf.partition("\n\n")
            if self._speakable(head):
                out += split_sentences(head, final=True)[0]
        pending = self.buf.lstrip()
        if self.in_code or pending.startswith(("```", "|")):
            if final:
                self.buf = ""
            return out
        sentences, self.buf = split_sentences(self.buf, final=final)
        return out + sentences

    def feed(self, chunk: str) -> list[str]:
        if self.done:
            return []
        self.buf += chunk
        if self.full:
            return self._feed_full()
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
        if self.full:
            self.done = True
            return self._feed_full(final=True)
        sentences, self.buf = split_sentences(self.buf, final=True)
        self.done = True
        return self._take(sentences)
