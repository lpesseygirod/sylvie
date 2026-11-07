"""Academic writing: citations, Lea's Zotero library, research papers online, writing practice (T42)."""

import json
import sqlite3

import httpx
import pytest

from core.config import Config, load_config
from core.intent import looks_like_writing
from core.llm import FakeLLM
from core.toolcall import ToolCall
from knowledge import zotero
from knowledge.cite import Ref, in_text, initials, reference, style_key
from tools import scholar
from tools.registry import ToolContext, available_tools, execute

DELUCCHI = Ref(
    "An assessment of electric vehicles", [("Delucchi", "Mark A."), ("Yang", "C."), ("Burke", "Andrew F.")],
    "2014", container="Philosophical Transactions A", volume="372", issue="2006", pages="20120325",
    doi="10.1098/rsta.2012.0325",
)  # fmt: skip
REPORT = Ref("Electric Avenue", [("Reichmuth", "David")], "2022", kind="report",
             publisher="Union of Concerned Scientists")  # fmt: skip


# ---------- citations ----------
def test_references_in_each_style():
    assert initials("Mark A.") == "M. A." and initials("Jean-Paul") == "J.-P."
    assert reference(DELUCCHI, "apa") == (
        "Delucchi, M. A., Yang, C., & Burke, A. F. (2014). An assessment of electric vehicles. "
        "*Philosophical Transactions A*, *372*(2006), 20120325. https://doi.org/10.1098/rsta.2012.0325"
    )
    assert in_text(DELUCCHI, "apa") == "(Delucchi et al., 2014)"
    assert in_text(REPORT, "apa") == "(Reichmuth, 2022)"
    assert (
        reference(REPORT, "apa") == "Reichmuth, D. (2022). *Electric Avenue*. Union of Concerned Scientists."
    )
    assert reference(DELUCCHI, "mla").startswith("Delucchi, Mark A., et al. “An assessment")
    assert in_text(DELUCCHI, "chicago") == "(Delucchi, Yang, and Burke 2014)"
    assert reference(DELUCCHI, "chicago").startswith("Delucchi, Mark A., C. Yang, and Andrew F. Burke. 2014.")
    assert in_text(DELUCCHI, "harvard") == "(Delucchi, Yang and Burke, 2014)"
    assert "doi:10.1098/rsta.2012.0325" in reference(DELUCCHI, "harvard")
    two = Ref("T", [("Nealer", "R."), ("Hendrickson", "T. P.")], "2015")
    assert (
        in_text(two, "apa") == "(Nealer & Hendrickson, 2015)"
        and in_text(two, "mla") == "(Nealer and Hendrickson)"
    )
    assert in_text(Ref("Electric Cars in Boston"), "apa") == "(“Electric Cars in Boston”, n.d.)"
    assert style_key("APA 7") == "apa" and style_key("Chicago") == "chicago" and style_key("weird") == "apa"


# ---------- Lea's Zotero library ----------
ZSCHEMA = """
CREATE TABLE itemTypes(itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
CREATE TABLE items(itemID INTEGER PRIMARY KEY, itemTypeID INT, key TEXT, dateModified TEXT);
CREATE TABLE fields(fieldID INTEGER PRIMARY KEY, fieldName TEXT);
CREATE TABLE itemDataValues(valueID INTEGER PRIMARY KEY, value);
CREATE TABLE itemData(itemID INT, fieldID INT, valueID INT);
CREATE TABLE creators(creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT, fieldMode INT);
CREATE TABLE creatorTypes(creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT);
CREATE TABLE itemCreators(itemID INT, creatorID INT, creatorTypeID INT, orderIndex INT);
CREATE TABLE itemNotes(itemID INTEGER PRIMARY KEY, parentItemID INT, note TEXT, title TEXT);
CREATE TABLE itemAttachments(itemID INTEGER PRIMARY KEY, parentItemID INT, linkMode INT, contentType TEXT, path TEXT);
CREATE TABLE itemAnnotations(itemID INTEGER PRIMARY KEY, parentItemID INT, type INT, text TEXT, comment TEXT, pageLabel TEXT);
CREATE TABLE deletedItems(itemID INTEGER PRIMARY KEY);
"""


def fake_zotero(root):
    root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / "zotero.sqlite")
    con.executescript(ZSCHEMA)
    con.executemany("INSERT INTO itemTypes VALUES (?, ?)",
                    [(1, "journalArticle"), (2, "attachment"), (3, "note"), (4, "report")])  # fmt: skip
    fields = ["title", "date", "publicationTitle", "volume", "DOI", "abstractNote", "institution"]
    con.executemany("INSERT INTO fields VALUES (?, ?)", list(enumerate(fields, 1)))
    con.executemany("INSERT INTO creatorTypes VALUES (?, ?)", [(8, "author"), (10, "editor")])
    con.executemany("INSERT INTO items VALUES (?, ?, ?, ?)", [
        (1, 1, "AAAA1111", "2026-01-01"), (2, 2, "PDFKEY01", "2026-01-01"), (3, 3, "NOTEKEY1", "2026-01-01"),
        (4, 4, "TRASHED1", "2026-01-01"), (5, 2, "ANNOTPDF", "2026-01-01"),
    ])  # fmt: skip
    vals = {1: [("title", "An assessment of electric vehicles"), ("date", "2014-00-00 2014"),
                ("publicationTitle", "Philosophical Transactions A"), ("volume", "372"),
                ("DOI", "https://doi.org/10.1098/rsta.2012.0325"),
                ("abstractNote", "We review technology, infrastructure and greenhouse-gas emissions.")],
            4: [("title", "A deleted report about electric buses"), ("institution", "Nobody")]}  # fmt: skip
    vid = 0
    for item, pairs in vals.items():
        for name, value in pairs:
            vid += 1
            con.execute("INSERT INTO itemDataValues VALUES (?, ?)", (vid, value))
            con.execute("INSERT INTO itemData VALUES (?, ?, ?)", (item, fields.index(name) + 1, vid))
    con.executemany(
        "INSERT INTO creators VALUES (?, ?, ?, 0)", [(1, "Mark A.", "Delucchi"), (2, "C.", "Yang")]
    )
    con.executemany("INSERT INTO itemCreators VALUES (1, ?, 8, ?)", [(1, 0), (2, 1)])
    con.execute(
        "INSERT INTO itemAttachments VALUES (2, 1, 0, 'application/pdf', 'storage:Delucchi 2014.pdf')"
    )
    con.execute(
        "INSERT INTO itemNotes VALUES (3, 1, '<p>Key point: <b>lifecycle</b> emissions depend on the grid mix.</p>', '')"
    )
    con.execute("INSERT INTO deletedItems VALUES (4)")
    con.commit()
    con.close()
    folder = root / "storage" / "PDFKEY01"
    folder.mkdir(parents=True)
    (folder / "Delucchi 2014.pdf").write_bytes(b"%PDF-1.4 not really")
    (folder / ".zotero-ft-cache").write_text(
        "Page one: introduction to electric vehicles and batteries.\f"
        + "Page two: battery electric vehicles charged from coal power plants emit more than hybrids. " * 30
    )
    return root


@pytest.fixture
def zot(tmp_path, monkeypatch):
    zdir = fake_zotero(tmp_path / "Zotero")
    monkeypatch.setattr(zotero, "zotero_dir", lambda cfg: zdir)
    zotero._open.clear()
    cfg = Config(dict(load_config()))
    cfg["paths"] = {**cfg["paths"], "data_dir": str(tmp_path / "data")}
    return cfg, zdir


def test_zotero_library_is_read_from_a_copy_and_searched(zot):
    cfg, zdir = zot
    before = (zdir / "zotero.sqlite").read_bytes()
    idx = zotero.get(cfg)
    assert [r.title for _, r in idx.items()] == ["An assessment of electric vehicles"]  # trashed one left out
    ref = idx.items()[0][1]
    assert ref.authors == [("Delucchi", "Mark A."), ("Yang", "C.")] and ref.year == "2014"
    assert ref.doi == "10.1098/rsta.2012.0325"
    hits = idx.search("coal power plants emissions")
    assert any(h.location == "PDF page 2" and "coal" in h.text for h in hits)
    assert any(h.kind == "note" and "grid mix" in h.text for h in idx.search("lifecycle grid"))
    assert (zdir / "zotero.sqlite").read_bytes() == before  # Zotero's own file is never changed
    assert idx.refresh(force=True)["changed"] == 0  # nothing changed: nothing read again


def test_zotero_tool_gives_citations_and_lists_the_library(zot):
    cfg, _ = zot
    ctx = ToolContext(cfg=cfg, user_text="essay on EV emissions")
    assert "zotero_library" in [t["function"]["name"] for t in available_tools(ctx)]
    r = execute(ToolCall("zotero_library", {"query": "coal emissions", "style": "apa"}), ctx)
    assert r.ok and "<untrusted>" in r.content and ctx.read_untrusted
    assert "Cite in text as: (Delucchi & Yang, 2014)" in r.content and "PDF page 2" in r.content
    assert r.sources[0]["title"].startswith("Zotero: ")
    everything = execute(ToolCall("zotero_library", {"query": "everything"}), ctx)
    assert "1 sources" in everything.content and "Delucchi, M. A., & Yang, C. (2014)" in everything.content


def test_no_zotero_means_no_tool(tmp_path):
    cfg = Config(dict(load_config()))
    cfg["paths"] = {**cfg["paths"], "data_dir": str(tmp_path / "data")}
    assert "zotero_library" not in [t["function"]["name"] for t in available_tools(ToolContext(cfg=cfg))]


# ---------- papers online ----------
class FakeHTTP:
    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def __call__(self, url, params=None, **kw):
        self.seen.append((url, params or {}))
        for prefix, body in self.routes:
            if url.startswith(prefix):
                status, payload = body if isinstance(body, tuple) else (200, body)
                content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                return httpx.Response(status, content=content, request=httpx.Request("GET", url))
        return httpx.Response(404, request=httpx.Request("GET", url))


OPENALEX_WORK = {
    "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/abc", "title": "Lifecycle emissions of EVs",
    "publication_year": 2016, "type": "article", "cited_by_count": 406,
    "authorships": [{"author": {"display_name": "Linda Ager-Wick Ellingsen"}}, {"author": {"display_name": "Anders Hammer Strømman"}}],
    "primary_location": {"source": {"display_name": "Environmental Research Letters"}, "landing_page_url": "https://iop.org/x"},
    "biblio": {"volume": "11", "issue": "5", "first_page": "054010", "last_page": "054010"},
    "abstract_inverted_index": {"Battery": [0], "size": [1], "matters.": [2]},
    "best_oa_location": {"pdf_url": "https://iop.org/x.pdf"},
}  # fmt: skip


def test_find_papers_strips_lea_and_returns_citations(tmp_path, monkeypatch):
    http = FakeHTTP([(scholar.OPENALEX, {"results": [OPENALEX_WORK]})])
    monkeypatch.setattr(scholar.httpx, "get", http)
    monkeypatch.setattr(scholar, "_api_key", lambda: None)
    cfg = Config(dict(load_config()))
    ctx = ToolContext(cfg=cfg, private_terms=["Lea"])
    r = execute(
        ToolCall("find_papers", {"query": "Lea essay EV lifecycle emissions", "since_year": 2010}), ctx
    )
    assert (
        "Lea" not in http.seen[0][1]["search"]
        and http.seen[0][1]["filter"] == "from_publication_date:2010-01-01"
    )
    assert "(Ellingsen & Strømman, 2016)" in r.content and "Abstract: Battery size matters." in r.content
    assert "https://iop.org/x.pdf" in ctx.allowed_urls and "cited 406 times" in r.content


def test_check_reference_catches_invented_ones(monkeypatch):
    real = {"message": {"items": [{"title": ["An assessment of electric vehicles: technology and policy"],
                                   "author": [{"family": "Delucchi", "given": "M. A."}], "issued": {"date-parts": [[2014]]},
                                   "DOI": "10.1098/rsta.2012.0325", "type": "journal-article"}]}}  # fmt: skip
    monkeypatch.setattr(scholar.httpx, "get", FakeHTTP([(scholar.CROSSREF, real)]))
    ref, conf = scholar.check_reference("An assessment of electric vehicles", "Delucchi", "2014")
    assert conf == 1.0 and ref.year == "2014"
    _, conf = scholar.check_reference("The quantum economics of electric unicorns", "Smith", "2019")
    assert conf < 0.5
    ctx = ToolContext(cfg=Config(dict(load_config())))
    r = execute(ToolCall("check_reference", {"title": "The quantum economics of electric unicorns"}), ctx)
    assert r.content.startswith("NOT FOUND")
    monkeypatch.setattr(scholar.httpx, "get", FakeHTTP([(scholar.CROSSREF, (429, {}))]))
    monkeypatch.setattr("time.sleep", lambda s: None)
    r = execute(ToolCall("check_reference", {"title": "Anything"}), ctx)
    assert not r.ok and "Don't cite" in r.content


# ---------- writing mode ----------
def test_writing_requests_get_sources_and_room():
    assert looks_like_writing("Write me an essay on EV policy with APA references")
    assert looks_like_writing("Hazme un ensayo sobre coches eléctricos")
    assert looks_like_writing("fais-moi une revue de littérature")
    assert not looks_like_writing("Rédige un mail à Paul") and not looks_like_writing("what time is it")


def test_essay_turn_gets_the_writing_note_and_longer_answers(tmp_path):
    from tests.test_orchestrator import make, run

    s = make(
        tmp_path,
        [
            '<doc title="Essay">Text</doc> Here it is.',
            json.dumps({"facts": [], "interests": [], "style": []}),
        ],
    )
    run(s, "Write an essay on electric cars")
    call = s.llm.calls[0]
    assert call["max_tokens"] == s.cfg.get_path("llm.max_tokens_writing", 3072)
    assert "academic writing" in call["messages"][-1]["content"]


# ---------- writing practice ----------
ARTICLE = """<article><front><article-meta><title-group><article-title>Cars and Cities</article-title></title-group>
<permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/" xmlns:xlink="http://www.w3.org/1999/xlink">
<license-p>Open Access under CC BY.</license-p></license></permissions>
<abstract><p>{abstract}</p></abstract></article-meta></front>
<body><sec><title>Introduction</title><p>{intro} Cities grow <xref>1</xref>, <xref>2</xref>.</p></sec>
<sec><title>Methods</title><p>We counted cars.</p></sec>
<sec><title>Conclusions</title><p>Fewer cars help.</p></sec></body></article>"""


def article(license_url="https://creativecommons.org/licenses/by/4.0/"):
    xml = ARTICLE.format(abstract="word " * 180, intro="Context sentence. " * 120)
    return xml.replace("https://creativecommons.org/licenses/by/4.0/", license_url).encode()


def test_only_cc_by_papers_are_used():
    from learning.writing import parse_article

    p = parse_article(article(), "PMC1")
    assert p.title == "Cars and Cities" and p.license == "CC BY 4.0"
    assert p.section("introduction").endswith("Cities grow.")  # citation markers removed
    assert parse_article(article("https://creativecommons.org/licenses/by-nc/4.0/"), "PMC2") is None


def test_writing_practice_keeps_her_improved_rewrite(tmp_path):
    from core.db import Database
    from learning.store import LearningStore
    from learning.writing import writing_practice
    from tests.test_overnight import Pool

    http = FakeHTTP([
        ("https://www.ebi.ac.uk/europepmc/webservices/rest/search", {"resultList": {"result": [{"pmcid": "PMC7"}]}}),
        ("https://www.ebi.ac.uk/europepmc/webservices/rest/PMC7/fullTextXML", (200, article())),
    ])  # fmt: skip
    judge = FakeLLM(
        [json.dumps({"score": 5, "feedback": "vague aim"}), json.dumps({"score": 8.5, "feedback": "good"})]
    )
    pool = Pool(FakeLLM(["A first draft.", "A clearer, better draft."]), judge)
    store = LearningStore(Database(tmp_path / "w.db", dim=8))
    cfg = Config({"learning": {"auto_approve_judged": True, "auto_approve_min_score": 8}})
    stats = writing_practice(pool, store, cfg, n=1, batch="b1", model_name="Qwen-test", seed=3, get=http)
    assert stats == {"papers": 1, "exercises": 1, "kept": 1}
    [ex] = store.examples(status="ready")
    assert ex["chosen"] == "A clearer, better draft." and ex["rejected"] == "A first draft."
    assert ex["tag"] == "general" and "CC BY 4.0 excerpt: Europe PMC PMC7" in ex["license"]
    rewrite_prompt = pool.models["sylvie"].calls[-1]["messages"][-1]["content"]
    assert "vague aim" in rewrite_prompt  # she rewrites from the feedback, not from the published text


def test_style_named_in_the_message_wins_and_missing_references_are_found(monkeypatch):
    from knowledge.cite import missing_references, style_asked

    assert style_asked("Hazme un ensayo, formato Chicago") == "chicago" and style_asked("an essay") is None
    http = FakeHTTP([(scholar.OPENALEX, {"results": [OPENALEX_WORK]})])
    monkeypatch.setattr(scholar.httpx, "get", http)
    monkeypatch.setattr(scholar, "_api_key", lambda: None)
    ctx = ToolContext(cfg=Config(dict(load_config())), user_text="Un ensayo en formato Chicago, por favor")
    r = execute(ToolCall("find_papers", {"query": "EV emissions"}), ctx)  # no style argument given
    assert "(Ellingsen and Strømman 2016)" in r.content and ctx.refs[0].year == "2016"
    essay = "EVs pollute less (Ellingsen and Strømman 2016; Delucchi et al. 2014).\n\nReferences\nDelucchi… An assessment of electric vehicles."
    assert [x.title for x in missing_references(essay, ctx.refs + [DELUCCHI])] == [
        "Lifecycle emissions of EVs"
    ]


def test_essay_looks_in_zotero_first_and_flags_references_no_tool_found(zot, tmp_path):
    from tests.test_orchestrator import make

    cfg, _ = zot
    invented = (
        '<doc title="Essay">EVs help (Delucchi et al., 2014; Smith, 2019).\n\nReferences\n'
        "Delucchi, M. A., & Yang, C. (2014). An assessment of electric vehicles. *Phil. Trans. A*.\n"
        "Smith, J. (2019). The quantum economics of electric unicorns. *Journal of Nothing*, 1, 1-2.\n</doc>"
    )
    s = make(tmp_path, [invented, json.dumps({"facts": [], "interests": [], "style": []})])
    s.cfg["paths"] = cfg["paths"]
    ev = list(s.turn("Write a short essay on coal and electric vehicle emissions", conversation_id=None))
    tools_run = [e["name"] for e in ev if e["type"] == "tool"]
    assert tools_run[0] == "zotero_library"  # before the model wrote a word
    first = s.llm.calls[0]["messages"]
    assert any(m["role"] == "tool" and "From Lea's Zotero library" in m["content"] for m in first)
    text = "".join(e["text"] for e in ev if e["type"] == "text")
    assert "**Check before using:**" in text and "quantum economics" in text.split("Check before using")[1]
    assert "An assessment of electric vehicles. *Phil" not in text.split("Check before using")[1]
