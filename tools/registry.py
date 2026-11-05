"""The tools Sylvie can call, their schemas (native tool calling), argument validation and dispatch.

Only tools listed here can be called (allowlist). Every result that contains outside content
is wrapped in <untrusted> so the model treats it as data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tools.calc import calculate
from tools.fetch import BlockedURL, fetch_page
from tools.search import SearchUnavailable, web_search


def _fn(name: str, description: str, params: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": params, "required": required},
        },
    }


WEB_SEARCH = _fn(
    "web_search",
    "Search the web and read the top pages. Use for recent events, prices, schedules, news, or facts you're unsure of. "
    "The query must be generic: never include Lea's name or personal details.",
    {"query": {"type": "string", "description": "search query, in the most useful language"}},
    ["query"],
)
SEARCH_DOCS = _fn(
    "search_documents",
    "Search Lea's own indexed files (course PDFs, slides, notes). Use for questions about her courses or documents.",
    {"query": {"type": "string", "description": "what to look for"}},
    ["query"],
)
CALCULATOR = _fn(
    "calculator",
    "Evaluate an arithmetic expression exactly, e.g. '0.17 * 2340' or 'sqrt(2) * pi'. Supports + - * / ** % mod and math functions.",
    {"expression": {"type": "string"}},
    ["expression"],
)
OPEN_URL = _fn(
    "open_url",
    "Open and read a web page. Only for URLs Lea gave you or URLs from this turn's search results.",
    {"url": {"type": "string"}},
    ["url"],
)


@dataclass
class ToolContext:
    cfg: object
    docs: object | None = None
    user_text: str = ""
    private_terms: list[str] = field(default_factory=list)
    allowed_urls: set[str] = field(default_factory=set)
    sources: list[dict] = field(default_factory=list)
    actions: object | None = None  # actions.runner.ActionRunner
    conversation_id: str | None = None
    read_untrusted: bool = False  # set once outside content (web, files, email) entered this turn


@dataclass
class ToolResult:
    content: str
    sources: list[dict] = field(default_factory=list)
    ok: bool = True
    pending: dict | None = None  # an action waiting for Lea's "yes"


def available_tools(ctx: ToolContext) -> list[dict]:
    tools = [CALCULATOR]
    if ctx.cfg.get_path("search.enabled", True):
        tools = [WEB_SEARCH, OPEN_URL] + tools
    if ctx.docs is not None and ctx.docs.stats()["chunks"] > 0:
        tools.append(SEARCH_DOCS)
    if ctx.actions is not None:
        tools += ctx.actions.tool_defs()
    return tools


def validate(call, tools: list[dict]) -> str | None:
    """Returns an error message, or None if the call is valid."""
    spec = next((t["function"] for t in tools if t["function"]["name"] == call.name), None)
    if spec is None:
        names = ", ".join(t["function"]["name"] for t in tools)
        return f"Unknown tool '{call.name}'. Available tools: {names}."
    props = spec["parameters"]["properties"]
    for req in spec["parameters"]["required"]:
        v = call.arguments.get(req)
        if v is None or (isinstance(v, str) and not v.strip()):
            return f"Missing required parameter '{req}' for {call.name}."
    for k, v in call.arguments.items():
        if k not in props:
            return f"Unknown parameter '{k}' for {call.name}."
        if props[k].get("type") == "string" and not isinstance(v, str):
            call.arguments[k] = str(v)
    return None


def _number_sources(ctx: ToolContext, sources: list[dict]) -> list[dict]:
    """Renumber so citations stay unique across several searches in one turn."""
    out = []
    for s in sources:
        s = {**s, "n": len(ctx.sources) + 1}
        ctx.sources.append(s)
        out.append(s)
    return out


UNTRUSTED_TOOLS = {"web_search", "open_url", "search_documents", "read_emails", "calendar_events"}


def execute(call, ctx: ToolContext) -> ToolResult:
    result = _execute(call, ctx)
    if call.name in UNTRUSTED_TOOLS:
        ctx.read_untrusted = True
    return result


def _execute(call, ctx: ToolContext) -> ToolResult:
    args = call.arguments
    if ctx.actions is not None and ctx.actions.handles(call.name):
        out = ctx.actions.call(call.name, args, ctx.conversation_id, after_untrusted=ctx.read_untrusted)
        pending = None
        if out.pending_id:
            rec = ctx.actions.log.get(out.pending_id)
            pending = {"id": out.pending_id, "name": call.name, "summary": rec.summary if rec else ""}
        return ToolResult(out.content, ok=out.ok, pending=pending)
    if call.name == "calculator":
        return ToolResult(calculate(args["expression"]))
    if call.name == "web_search":
        try:
            text, sources = web_search(
                args["query"],
                base_url=ctx.cfg.get_path("search.searxng_url"),
                max_results=ctx.cfg.get_path("search.max_results", 5),
                pages_to_open=ctx.cfg.get_path("search.max_pages_to_open", 3),
                private_terms=ctx.private_terms,
            )
        except SearchUnavailable:
            return ToolResult(
                "Web search is unavailable right now (the local search service isn't running). "
                "Tell Lea you couldn't search and answer from what you know, saying it may be out of date.",
                ok=False,
            )
        offset = len(ctx.sources)
        for s in sources:
            ctx.allowed_urls.add(s["url"])
        numbered = _number_sources(ctx, sources)
        if offset:  # shift [n] markers in the text to the new numbers
            for s in reversed(sources):
                text = text.replace(f"[{s['n']}] ", f"[{s['n'] + offset}] ", 1)
        return ToolResult(text, numbered)
    if call.name == "open_url":
        url = str(args["url"]).strip()
        if url not in ctx.allowed_urls and url not in ctx.user_text:
            return ToolResult(
                "Refused: you can only open URLs Lea gave you or that came from this turn's search results.",
                ok=False,
            )
        try:
            page = fetch_page(url)
        except (BlockedURL, ValueError, OSError) as e:
            return ToolResult(f"Could not open the page: {e}", ok=False)
        except Exception as e:  # httpx errors etc.
            return ToolResult(f"Could not open the page: {type(e).__name__}", ok=False)
        [src] = _number_sources(ctx, [{"title": page["title"] or url, "url": page["url"]}])
        return ToolResult(
            f"[{src['n']}] {page['title']}\nURL: {page['url']}\n<untrusted>\n{page['text']}\n</untrusted>",
            [src],
        )
    if call.name == "search_documents":
        hits = ctx.docs.search(args["query"], k=5) if ctx.docs else []
        if not hits:
            return ToolResult("No matching passages in Lea's documents.")
        srcs = []
        for h in hits:
            name = h.path.rsplit("/", 1)[-1]
            srcs.append({"title": f"{name}" + (f", {h.location}" if h.location else ""), "path": h.path})
        numbered = _number_sources(ctx, srcs)
        blocks = [f"[{s['n']}] {s['title']}\n{h.text}" for s, h in zip(numbered, hits, strict=True)]
        return ToolResult(
            "Passages from Lea's documents:\n<untrusted>\n" + "\n\n".join(blocks) + "\n</untrusted>", numbered
        )
    return ToolResult(f"Tool '{call.name}' is not available.", ok=False)
