"""The tools Sylvie can call, their schemas (native tool calling), argument validation and dispatch.

Only tools listed here can be called (allowlist). Every result that contains outside content
is wrapped in <untrusted> so the model treats it as data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tools.calc import calculate
from tools.fetch import BlockedURL, fetch_page
from tools.research import research
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
WIKIPEDIA = _fn(
    "wikipedia",
    "Look something up in Sylvie's offline Wikipedia (English, Spanish, French; snapshot Nov 2023): instant and "
    "private, nothing leaves the Mac. Use FIRST for facts about the world: people, history, places, science, "
    "definitions, how things work. Use web_search instead for anything recent or changing (news, prices, "
    "schedules, weather, after 2023). Each result has the article's URL: open_url it if you need the full article.",
    {
        "query": {"type": "string", "description": "the topic or question, a few key words"},
        "language": {"type": "string", "description": "en, es or fr (the language of Lea's question)"},
    },
    ["query"],
)
_STYLE = {"type": "string", "description": "citation style: apa (default), mla, chicago or harvard"}
ZOTERO = _fn(
    "zotero_library",
    "Search Lea's own Zotero library (key words in the language of her sources, usually English): the "
    "papers, reports and books she saved, their full text (PDFs), "
    "and her notes and highlights. Private, on the Mac. Use FIRST for essays, reports and any academic "
    "writing, and when she asks about her sources or readings. Each result gives the in-text citation and "
    "the bibliography entry to use. Query 'everything' lists the whole library.",
    {"query": {"type": "string", "description": "the topic or claim to find support for, a few key words"},
     "style": _STYLE},
    ["query"],
)  # fmt: skip
FIND_PAPERS = _fn(
    "find_papers",
    "Find published research (journal articles, reports, books) on a topic in OpenAlex, 250M+ scholarly "
    "works: title, authors, year, abstract, citation count, and the exact citation. Use for essays and "
    "academic questions when Lea's Zotero library isn't enough. Only the search words leave the Mac: never "
    "include Lea's name or personal details.",
    {"query": {"type": "string", "description": "key words of the topic, in English for best results"},
     "since_year": {"type": "integer", "description": "only works published from this year on (optional)"},
     "style": _STYLE},
    ["query"],
)  # fmt: skip
CHECK_REFERENCE = _fn(
    "check_reference",
    "Check that a reference really exists (Crossref, the DOI registry) and get its exact details, before "
    "citing a source you remember rather than one a tool just gave you. Give the title (or a DOI).",
    {"title": {"type": "string", "description": "the work's title, or its DOI"},
     "authors": {"type": "string", "description": "author family names (optional)"},
     "year": {"type": "string", "description": "year of publication (optional)"},
     "style": _STYLE},
    ["title"],
)  # fmt: skip
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
RESEARCH = _fn(
    "research",
    "Research a broad or multi-part question (comparisons, pros and cons, planning a trip, 'what are my options'): "
    "it splits the question into parts, searches them in parallel and returns notes with sources. Slower than "
    "web_search (~20-40 s), so use web_search for a single fact. Never include Lea's name or personal details.",
    {"question": {"type": "string", "description": "the whole question to research, generic wording"}},
    ["question"],
)


def _settings_tools() -> list[dict]:
    from core.settings import SETTINGS, VOICES

    names = ", ".join(f"{n} ({s.help})" for n, s in SETTINGS.items())
    voices = "; ".join(f"{lang}: {', '.join(v)}" for lang, v in VOICES.items())
    change = _fn(
        "change_setting",
        "Change one of Sylvie's own settings when Lea asks (e.g. 'speak faster', 'use the Bella voice in "
        f"English', 'turn off web search'). Settings: {names}. Voices by language: {voices}. Values: on/off, "
        "a voice name, a speed between 0.6 and 1.6, or for model 'fast'/'smart'. Then confirm in one sentence.",
        {"name": {"type": "string", "enum": list(SETTINGS)}, "value": {"type": "string"}},
        ["name", "value"],
    )
    show = _fn("show_settings", "List Sylvie's current settings, when Lea asks what they are.", {}, [])
    past = _fn(
        "search_past_conversations",
        "Search everything Lea and Sylvie said in earlier conversations (any date), by topic or words. Use it "
        "when Lea refers to something she told you before ('remember when…', 'what did I say about…', "
        "'te lo dije hace dos meses'). Returns exchanges with their dates.",
        {"query": {"type": "string", "description": "what to look for, in a few words"}},
        ["query"],
    )
    tools = [change, show, past]
    from tools.claude import claude_cli

    if claude_cli() is not None:
        tools.append(
            _fn(
                "ask_claude",
                "Hand a task to Claude (Anthropic's assistant, in the cloud) and get its answer. ONLY when Lea's "
                "message explicitly asks for Claude ('ask Claude…', 'pregúntale a Claude', 'delegate this to "
                "Claude'). Pass her request as a complete, self-contained task. Then give her Claude's answer "
                "(say it's from Claude), short when spoken.",
                {"task": {"type": "string", "description": "the full request for Claude"}},
                ["task"],
            )
        )
    return tools


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
    llm: object | None = None  # for tools that think (research); the turn's model, idle while tools run
    settings: object | None = None  # the Sylvie whose settings Lea can change by asking (core/settings.py)
    refs: list = field(
        default_factory=list
    )  # works found this turn (knowledge/cite.Ref), for the reference check


@dataclass
class ToolResult:
    content: str
    sources: list[dict] = field(default_factory=list)
    ok: bool = True
    pending: dict | None = None  # an action waiting for Lea's "yes"


def _wiki(ctx: ToolContext):
    from knowledge.wiki import get

    return get(ctx.cfg)


def _zotero(ctx: ToolContext):
    from knowledge.zotero import get

    return get(ctx.cfg)


def _scholar_on(ctx: ToolContext) -> bool:
    return bool(ctx.cfg.get_path("search.enabled", True) and ctx.cfg.get_path("scholar.enabled", True))


def available_tools(ctx: ToolContext) -> list[dict]:
    tools = [CALCULATOR]
    if ctx.cfg.get_path("search.enabled", True):
        tools = [WEB_SEARCH, OPEN_URL] + tools
        if ctx.llm is not None:
            tools.append(RESEARCH)
    if _scholar_on(ctx):
        tools += [FIND_PAPERS, CHECK_REFERENCE]
    if _wiki(ctx) is not None:
        tools.insert(0, WIKIPEDIA)
    if _zotero(ctx) is not None:
        tools.append(ZOTERO)
    if ctx.docs is not None and ctx.docs.stats()["chunks"] > 0:
        tools.append(SEARCH_DOCS)
    if ctx.actions is not None:
        tools += ctx.actions.tool_defs()
    if ctx.settings is not None:
        tools += _settings_tools()
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


UNTRUSTED_TOOLS = {
    "web_search",
    "wikipedia",
    "open_url",
    "search_documents",
    "read_emails",
    "calendar_events",
    "research",
    "ask_claude",
    "zotero_library",
    "find_papers",
    "check_reference",
}


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
            pending = {
                "id": out.pending_id,
                "name": call.name,
                "args": args,
                "summary": rec.summary if rec else "",
            }
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
    if call.name == "ask_claude":
        from tools.claude import ask, asked_for_claude

        if not asked_for_claude(ctx.user_text):
            return ToolResult(
                "Not sent: Claude is only asked when Lea's own message asks for Claude. Answer yourself.",
                ok=False,
            )
        ok, answer = ask(str(args["task"]), ctx.cfg.data_dir / "claude-workdir")
        return ToolResult(("Claude's answer:\n" + answer) if ok else answer, ok=ok)
    if call.name == "search_past_conversations":
        if ctx.settings is None:
            return ToolResult("Past conversations can't be searched here.", ok=False)
        found = ctx.settings.episodes.search(
            str(args["query"]), k=6, exclude_conversation=ctx.conversation_id
        )
        if not found:
            return ToolResult(f"Nothing about '{args['query']}' in earlier conversations.")
        return ToolResult("Earlier conversations (oldest dates first):\n" + "\n\n".join(
            e.line() for e in sorted(found, key=lambda e: e.created_at)))  # fmt: skip
    if call.name in ("change_setting", "show_settings"):
        from core.settings import BadValue, change, describe

        if ctx.settings is None:
            return ToolResult("Settings can't be changed here.", ok=False)
        if call.name == "show_settings":
            return ToolResult(describe(ctx.cfg))
        try:
            return ToolResult(change(ctx.settings, args["name"], args["value"]))
        except BadValue as e:
            return ToolResult(f"Not changed: {e}", ok=False)
    if call.name == "research":
        if ctx.llm is None:
            return ToolResult("Research isn't available here; use web_search instead.", ok=False)

        def search(query: str):
            try:
                return web_search(
                    query,
                    base_url=ctx.cfg.get_path("search.searxng_url"),
                    max_results=ctx.cfg.get_path("search.max_results", 5),
                    pages_to_open=2,
                    private_terms=ctx.private_terms,
                )
            except SearchUnavailable:
                return "Web search is unavailable right now.", []

        text, sources, _ = research(args["question"], ctx.llm, search, start=len(ctx.sources))
        for s in sources:
            ctx.allowed_urls.add(s["url"])
            ctx.sources.append(s)
        if not sources:
            text += "\n\n(No web results came back: tell Lea you couldn't research it and answer from what you know.)"
        return ToolResult(text, sources)
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
    if call.name == "wikipedia":
        wiki = _wiki(ctx)
        if wiki is None:
            return ToolResult("The offline Wikipedia isn't installed; use web_search.", ok=False)
        from core.lang import guess_language

        lang = str(args.get("language") or guess_language(ctx.user_text or args["query"], default="en"))[:2]
        hits = wiki.search(args["query"], lang=lang, k=4)
        if not hits:
            return ToolResult("Nothing found in the offline Wikipedia. Try other key words, or web_search.")
        srcs = [{"title": f"Wikipedia: {h.title}", "url": h.url} for h in hits]
        for s in srcs:
            ctx.allowed_urls.add(s["url"])
        numbered = _number_sources(ctx, srcs)
        blocks = [f"[{s['n']}] {h.title} ({h.url})\n{h.text}" for s, h in zip(numbered, hits, strict=True)]
        return ToolResult(
            "From the offline Wikipedia (snapshot Nov 2023; cite as [n]):\n<untrusted>\n"
            + "\n\n".join(blocks)
            + "\n</untrusted>",
            numbered,
        )
    if call.name in ("zotero_library", "find_papers", "check_reference"):
        return _scholarly(call, ctx)
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


def turn_style(ctx: ToolContext, args: dict | None = None) -> str:
    """The citation style: the one Lea named in her message, else the tool's argument, else her default."""
    from knowledge.cite import style_asked, style_key

    return style_key(style_asked(ctx.user_text) or (args or {}).get("style")
                     or ctx.cfg.get_path("writing.citation_style", "apa"))  # fmt: skip


def _scholarly(call, ctx: ToolContext) -> ToolResult:
    """Lea's Zotero library, OpenAlex and Crossref: sources with ready-made citations (knowledge/cite.py)."""
    from knowledge.cite import STYLES, describe
    from tools.scholar import ScholarUnavailable, check_reference, search_papers
    from tools.search import sanitize_query

    args, style = call.arguments, turn_style(ctx, call.arguments)
    if call.name == "zotero_library":
        z = _zotero(ctx)
        if z is None:
            return ToolResult("Lea has no Zotero library on this Mac.", ok=False)
        q = str(args["query"]).strip()
        if q.lower() in ("everything", "all", "*", "todo", "tout"):
            listing = "\n".join(
                f"- {describe(r, style).splitlines()[1].split(': ', 1)[1]}" for _, r in z.items()
            )
            return ToolResult(
                f"Lea's Zotero library ({z.count()} sources):\n<untrusted>\n{listing}\n</untrusted>"
            )
        hits = z.search(q, k=6)
        if not hits:
            return ToolResult(
                "Nothing about that in Lea's Zotero library. Try other key words, or find_papers."
            )
        by_item: dict[str, list] = {}
        for h in hits:  # one numbered source per work, with its passages
            by_item.setdefault(h.key, []).append(h)
        groups = list(by_item.values())
        ctx.refs += [g[0].ref for g in groups]
        srcs = [{"title": f"Zotero: {g[0].ref.title}", "url": g[0].ref.link, "path": (g[0].files or [""])[0]}
                for g in groups]  # fmt: skip
        numbered = _number_sources(ctx, srcs)
        blocks = []
        for s, g in zip(numbered, groups, strict=True):
            ref = g[0].ref
            gaps = "" if ref.authors and ref.year else (
                "\n(Zotero has no author or year for this one: check_reference it before citing.)")  # fmt: skip
            passages = "\n".join(f"- {h.location}: {h.text}" for h in g)
            blocks.append(f"[{s['n']}] {ref.title}\n{describe(ref, style)}{gaps}\nPassages:\n{passages}")
        return ToolResult(f"From Lea's Zotero library ({STYLES[style]}):\n<untrusted>\n"
                          + "\n\n".join(blocks) + "\n</untrusted>", numbered)  # fmt: skip
    if not _scholar_on(ctx):
        return ToolResult("Online search is turned off.", ok=False)
    try:
        if call.name == "find_papers":
            q = sanitize_query(str(args["query"]), ctx.private_terms)
            since = args.get("since_year")
            try:
                since = int(since) if since else None
            except (TypeError, ValueError):
                since = None
            found = search_papers(q, n=5, since=since)
            if not found:
                return ToolResult("No papers found. Try broader key words, in English.")
            srcs = [{"title": p["ref"].title, "url": p["ref"].link or p["openalex"]} for p in found]
            ctx.refs += [p["ref"] for p in found]
            for p, s in zip(found, srcs, strict=True):
                ctx.allowed_urls.update(u for u in (s["url"], p["open_pdf"]) if u)
            numbered = _number_sources(ctx, srcs)
            blocks = []
            for s, p in zip(numbered, found, strict=True):
                ab = p["abstract"][:1200] or "(no abstract available)"
                blocks.append(f"[{s['n']}] {p['ref'].title} — cited {p['cited_by']} times"
                              + (f"; free full text: {p['open_pdf']}" if p["open_pdf"] else "")
                              + f"\n{describe(p['ref'], style)}\nAbstract: {ab}")  # fmt: skip
            return ToolResult(f"Papers from OpenAlex ({STYLES[style]}; you only know what the abstract says):\n"
                              "<untrusted>\n" + "\n\n".join(blocks) + "\n</untrusted>", numbered)  # fmt: skip
        title = sanitize_query(str(args["title"]), ctx.private_terms)
        ref, conf = check_reference(title, str(args.get("authors") or ""), str(args.get("year") or ""))
    except ScholarUnavailable as e:
        return ToolResult(
            f"The research database didn't answer ({e}). Don't cite what you couldn't check.", ok=False
        )
    if ref is None or conf < 0.5:
        near = f" The closest record is: {describe(ref, style)}" if ref is not None else ""
        return ToolResult(f"NOT FOUND: no published work matches that reference, so don't cite it.{near}")
    ctx.refs.append(ref)
    [s] = _number_sources(ctx, [{"title": ref.title, "url": ref.link}])
    if ref.link:
        ctx.allowed_urls.add(ref.link)
    return ToolResult(f"[{s['n']}] Found (confidence {conf}): {ref.title}\n<untrusted>\n{describe(ref, style)}"
                      "\n</untrusted>\nUse these exact details, not the ones you remembered.", [s])  # fmt: skip
