"""Offline Wikipedia: indexing, search, and the tool Sylvie calls (Lea, 2026-11-07)."""

import threading

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from core.config import Config, load_config
from core.toolcall import ToolCall
from knowledge.wiki import MAX_PASSAGES, PASSAGE_CHARS, Wiki, db_path, split_passages
from tools.registry import ToolContext, available_tools, execute

ARTICLES = {
    "es": [
        ("Fotosíntesis", "La fotosíntesis es la conversión de materia inorgánica en materia orgánica gracias a la luz.\nOcurre en los cloroplastos."),
        ("Simón Bolívar", "Simón Bolívar fue un militar y político venezolano, libertador de varios países de América."),
        ("Parque Simón Bolívar", "Un parque en Bogotá con el nombre de Simón Bolívar."),
    ],
    "en": [
        ("Marie Curie", "Marie Curie was a physicist and chemist who did pioneering research on radioactivity."),
        ("Radioactivity", "Radioactivity is the emission of radiation by unstable atomic nuclei. Marie Curie studied it."),
    ],
}  # fmt: skip


def write_parquet(path, rows, lang):
    table = pa.table(
        {
            "id": [str(i) for i in range(len(rows))],
            "url": [f"https://{lang}.wikipedia.org/wiki/{t.replace(' ', '_')}" for t, _ in rows],
            "title": [t for t, _ in rows],
            "text": [x for _, x in rows],
        }
    )
    pq.write_table(table, path)


@pytest.fixture
def wiki(tmp_path):
    w = Wiki(tmp_path / "wikipedia.db")
    for lang, rows in ARTICLES.items():
        f = tmp_path / f"{lang}.parquet"
        write_parquet(f, rows, lang)
        assert w.index_parquet(f, lang) == len(rows)
        assert w.index_parquet(f, lang) == 0  # already done: resumable, never twice
    w.optimize()
    return w


def test_exact_title_comes_first_and_language_is_respected(wiki):
    hits = wiki.search("¿Quién fue Simón Bolívar?", lang="es")
    assert hits[0].title == "Simón Bolívar" and hits[0].lang == "es"
    assert hits[0].url == "https://es.wikipedia.org/wiki/Simón_Bolívar"
    assert wiki.search("fotosintesis", lang="es")[0].title == "Fotosíntesis"  # accents don't matter
    assert wiki.search("Who was Marie Curie", lang="en")[0].title == "Marie Curie"
    assert wiki.search("Marie Curie radioactividad", lang="fr")[0].lang == "en"  # falls back to English
    assert wiki.search("the of and") == []
    assert wiki.stats() == {"es": 3, "en": 2}


def test_interrupted_file_is_redone_cleanly(tmp_path):
    w = Wiki(tmp_path / "w.db")
    f = tmp_path / "es.parquet"
    write_parquet(f, ARTICLES["es"], "es")
    stop = threading.Event()
    stop.set()
    with pytest.raises(InterruptedError):
        w.index_parquet(f, "es", stop=stop)
    assert w.stats() == {}
    assert w.index_parquet(f, "es") == 3


def test_passages_keep_the_start_of_long_articles():
    text = "\n".join(f"Paragraph {i}. " + "word " * 150 for i in range(100))
    parts = split_passages(text)
    assert len(parts) == MAX_PASSAGES and parts[0].startswith("Paragraph 0.")
    assert all(len(p) <= PASSAGE_CHARS * 1.6 for p in parts)
    one_long = split_passages("Sentence one. " * 300)
    assert len(one_long) > 1 and all(p.endswith(".") for p in one_long[:-1])


def test_tool_is_offered_only_when_installed_and_returns_cited_passages(tmp_path):
    cfg = Config(dict(load_config()))
    cfg["paths"] = {**cfg["paths"], "data_dir": str(tmp_path / "data")}
    ctx = ToolContext(cfg=cfg, user_text="¿Quién fue Simón Bolívar?")
    assert "wikipedia" not in [t["function"]["name"] for t in available_tools(ctx)]
    db_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    w = Wiki(db_path(cfg))
    f = tmp_path / "es.parquet"
    write_parquet(f, ARTICLES["es"], "es")
    w.index_parquet(f, "es")
    assert available_tools(ctx)[0]["function"]["name"] == "wikipedia"
    r = execute(ToolCall("wikipedia", {"query": "Simón Bolívar"}), ctx)
    assert r.ok and "<untrusted>" in r.content and "libertador" in r.content
    assert r.sources[0]["title"] == "Wikipedia: Simón Bolívar" and r.sources[0]["n"] == 1
    assert r.sources[0]["url"] in ctx.allowed_urls  # she may open the full article online
    assert ctx.read_untrusted


# ---------- Wikimedia's own dumps ----------
import bz2  # noqa: E402
import json as _json  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402

from knowledge import wiki_dump  # noqa: E402

DUMP = [
    {"title": "Canberra", "namespace": 0, "text": "Canberra is the capital city of Australia.", "incoming_links": 50000,
     "redirect": [{"namespace": 0, "title": "Canberra, Australia"}]},
    {"title": "Capital gains tax in Australia", "namespace": 0, "text": "Capital gains tax in Australia is a tax on the capital.", "incoming_links": 40},
    {"title": "Marie Curie", "namespace": 0, "text": "Marie Curie was a physicist and chemist.", "incoming_links": 9000,
     "redirect": [{"namespace": 0, "title": "Madame Curie"}, {"namespace": 0, "title": "Maria Skłodowska"}]},
    {"title": "Mercury (disambiguation)", "namespace": 0, "text": "Mercury may refer to: a planet, an element."},
    {"title": "Photosynthesis", "namespace": 0, "text": "Photosynthesis is how plants turn light into chemical energy.", "incoming_links": 30000},
    {"title": "Robin Hill (biochemist)", "namespace": 0, "text": "Robin Hill's work on photosynthesis: the Hill reaction.", "incoming_links": 50},
    {"title": "Talk:Canberra", "namespace": 1, "text": "Discussion."},
]  # fmt: skip


def dump_bytes(docs, streams=2):
    lines = b"".join(
        _json.dumps({"index": {}}).encode() + b"\n" + _json.dumps(d).encode() + b"\n" for d in docs
    )
    half = len(lines) // 2
    cut = lines.index(b"\n", half) + 1
    parts = [lines[:cut], lines[cut:]] if streams == 2 else [lines]
    return b"".join(bz2.compress(p) for p in parts)  # Wikimedia shards can hold several bz2 streams


def test_dump_lines_are_parsed_across_bz2_streams_and_chunks():
    raw = dump_bytes(DUMP)
    chunks = [raw[i : i + 7] for i in range(0, len(raw), 7)]
    got = list(wiki_dump.pages(wiki_dump.bz2_lines(chunks)))
    assert [p["title"] for p in got] == [
        "Canberra",
        "Capital gains tax in Australia",
        "Marie Curie",
        "Mercury (disambiguation)",
        "Photosynthesis",
        "Robin Hill (biochemist)",
    ]
    assert got[0]["redirects"] == ["Canberra, Australia"] and got[0]["popularity"] == 50000


def test_build_from_dump_swaps_in_the_new_library_and_ranks_by_popularity(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "wikipedia.db").write_text("old library")

    def fetch(url, out, keep):
        with open(out, "w") as f:
            for p in wiki_dump.pages(wiki_dump.bz2_lines([dump_bytes(DUMP)])):
                f.write(_json.dumps(p) + "\n")
        return url, 4

    stats = wiki_dump.build(data, ["en"], date="20261101", fetch=fetch, pool_cls=ThreadPoolExecutor,
                            list_shards=lambda d, lang: [f"https://x/{lang}-0.json.bz2"])  # fmt: skip
    assert stats == {"en": 5}  # the disambiguation page and the talk page are left out
    assert list((data / "_to_delete").glob("wikipedia-replaced-*.db"))  # old one kept, not deleted
    w = Wiki(data / "wikipedia.db")
    assert w.search("capital of Australia", "en")[0].title == "Canberra"  # popular beats wordy
    assert w.search("Madame Curie", "en")[0].title == "Marie Curie"  # another name of the article
    assert w.search("Madame Curie", "en")[0].url == "https://en.wikipedia.org/wiki/Marie_Curie"
    assert w.done("en/cirrus-20261101/en-0.json.bz2")
    # A title inside the question beats articles that merely share its words.
    assert w.search("how does photosynthesis work", "en")[0].title == "Photosynthesis"
    assert w.snapshot() == "2026-11-01"
