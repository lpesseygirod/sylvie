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
