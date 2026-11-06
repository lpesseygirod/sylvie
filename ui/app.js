// Sylvie chat UI. Plain JS, no dependencies, no external requests.
(function () {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const esc = window.escapeHtml, md = window.renderMarkdown, icon = window.icon;
  const params = new URLSearchParams(location.search);
  const PANEL = params.get("embed") === "panel";

  const S = { cid: null, streaming: false, controller: null, attachments: [], convs: [], doc: null, info: null };

  // ---------- static icons ----------
  const setIcon = (sel, name, label, cls) => { const b = $(sel); if (b) b.innerHTML = icon(name, cls) + (label ? " " + label : ""); };
  setIcon("#new-chat", "plus", "New chat");
  setIcon("#open-memory", "person", "About Lea");
  setIcon("#open-dashboard", "chart", "Learning dashboard");
  setIcon("#attach", "clip", "", "i-18");
  setIcon("#mic-btn", "mic", "", "i-18");
  setIcon("#send", "send", "", "i-18");
  setIcon("#wp-close", "close");
  setIcon("#toggle-sidebar", "menu", "", "i-18");
  setIcon("#close-sidebar", "close");
  $("#memory-dialog .dialog-head button").innerHTML = icon("close");
  $(".search").insertAdjacentHTML("afterbegin", icon("search", "i-14"));
  $("#wake-open").insertAdjacentHTML("beforeend", icon("arrowOut", "i-14"));
  window.SylvieOrb.mountAll();

  // ---------- API ----------
  async function api(path, opts = {}) {
    const o = { headers: {}, ...opts };
    if (o.body && !(o.body instanceof FormData)) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(o.body); }
    const r = await fetch(path, o);
    if (r.status === 401) { location.reload(); throw new Error("locked"); }
    if (!r.ok) throw new Error((await r.text()) || r.statusText);
    return r.headers.get("content-type")?.includes("json") ? r.json() : r;
  }

  // Clipboard: the modern API, or the old way when the browser refuses it (no focus, no permission).
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (_) {}
    const ta = el("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) {}
    ta.remove();
    return ok;
  }

  // ---------- status line, version chip, web search switch ----------
  function setStatus(kind, text) {
    $("#status-line .status-dot").className = "status-dot" + (kind ? " " + kind : "");
    $("#status-line .txt").textContent = text;
  }
  async function pollHealth() {
    try {
      const h = await api("/api/health");
      if (h.ready) { setStatus("ok", "Running on this Mac"); loadInfo(); return; }
      if (h.error) { setStatus("err", "Couldn't start: " + h.error); return; }
      setStatus("", "Loading the model…");
    } catch (_) { setStatus("err", "Sylvie's server isn't running"); }
    setTimeout(pollHealth, 1500);
  }
  async function loadInfo() {
    try {
      S.info = await api("/api/info");
      const chip = $("#version-chip");
      chip.textContent = `Sylvie ${S.info.version} · local`;
      chip.title = S.info.model;
      chip.hidden = false;
      setSearchChip(S.info.web_search);
    } catch (_) {}
  }
  function setSearchChip(on) {
    const b = $("#search-toggle");
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.textContent = on ? "Web search on" : "Web search off";
  }
  $("#search-toggle").onclick = async () => {
    const on = $("#search-toggle").getAttribute("aria-pressed") !== "true";
    try { setSearchChip((await api("/api/settings/search", { method: "POST", body: { enabled: on } })).enabled); } catch (_) {}
  };

  // ---------- sidebar ----------
  function group(c) {
    const d = new Date(c.updated_at), now = new Date();
    if (d.toDateString() === now.toDateString()) return "Today";
    if ((now - d) / 86400000 < 7) return "This week";
    return "Older";
  }

  async function loadConvs() {
    const q = $("#search").value.trim();
    S.convs = await api("/api/conversations" + (q ? `?search=${encodeURIComponent(q)}` : ""));
    const list = $("#conv-list");
    list.innerHTML = "";
    let last = null;
    for (const c of S.convs) {
      const g = group(c);
      if (g !== last) { list.appendChild(el("div", "group-label", g)); last = g; }
      const row = el("div", "conv" + (c.id === S.cid ? " active" : ""));
      row.tabIndex = 0;
      if (c.id === S.cid) row.setAttribute("aria-current", "page");
      row.innerHTML = `<span class="t">${esc(c.title)}</span><span class="acts"><button aria-label="Rename">${icon("pencil", "i-14")}</button><button aria-label="Delete">${icon("trash", "i-14")}</button></span>`;
      row.onclick = (e) => { if (!e.target.closest("button,input")) openConv(c.id); };
      row.onkeydown = (e) => { if (e.key === "Enter" && e.target === row) openConv(c.id); };
      const [ren, del] = row.querySelectorAll("button");
      ren.onclick = () => renameConv(row, c);
      del.onclick = async () => {
        if (!confirm(`Delete "${c.title}"?`)) return;
        await api(`/api/conversations/${c.id}`, { method: "DELETE" });
        if (S.cid === c.id) newChat();
        loadConvs();
      };
      list.appendChild(row);
    }
    if (!S.convs.length && q) list.appendChild(el("div", "group-label", "No matches"));
  }

  function renameConv(row, c) {
    const t = $(".t", row);
    const input = el("input");
    input.value = c.title;
    t.replaceWith(input);
    input.focus(); input.select();
    const done = async (save) => {
      if (save && input.value.trim() && input.value.trim() !== c.title) {
        await api(`/api/conversations/${c.id}`, { method: "PATCH", body: { title: input.value.trim() } });
        if (S.cid === c.id) $("#conv-title").textContent = input.value.trim();
      }
      loadConvs();
    };
    input.onkeydown = (e) => { if (e.key === "Enter") done(true); if (e.key === "Escape") done(false); };
    input.onblur = () => done(true);
  }

  let searchTimer;
  $("#search").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadConvs, 200); };
  $("#new-chat").onclick = () => { newChat(); $("#app").classList.remove("side-open"); };
  $("#toggle-sidebar").onclick = () => $("#app").classList.add("side-open");
  $("#close-sidebar").onclick = () => $("#app").classList.remove("side-open");

  function voiceHref() { return "/voice" + (S.cid ? `?c=${encodeURIComponent(S.cid)}` : ""); }
  function syncVoiceLinks() { $("#voice-btn").href = voiceHref(); $("#mic-btn").href = voiceHref(); }

  function newChat() {
    if (S.streaming) stop();
    S.cid = null;
    $("#messages").innerHTML = "";
    $("#messages").appendChild(emptyState());
    $("#conv-title").textContent = "New chat";
    document.querySelectorAll(".conv.active").forEach((c) => c.classList.remove("active"));
    closePanel();
    syncVoiceLinks();
    $("#input").focus();
  }

  function emptyState() {
    const e = el("div", "empty");
    e.id = "empty";
    e.innerHTML = `<div data-orb="120" data-state="idle"></div><h2>Hello Lea</h2><p>Ask me anything, in English, español or français.</p>`;
    window.SylvieOrb.mountAll(e);
    return e;
  }

  async function openConv(cid) {
    if (S.streaming) stop();
    S.cid = cid;
    const msgs = await api(`/api/conversations/${cid}/messages`);
    const box = $("#messages");
    box.innerHTML = "";
    closePanel();
    let tools = [];  // tool rounds are stored as separate, empty assistant rows: show them as notes on the answer
    const withDocs = [];
    for (const m of msgs) {
      if (m.role === "user") { box.appendChild(userMsg(m.content)); tools = []; continue; }
      if (m.role !== "assistant") continue;
      const calls = (m.tool_calls || []).map((t) => t.function || t);
      if (!m.content && calls.length) { tools.push(...calls); continue; }
      const parts = assistantMsg({ ...m, tool_calls: [...tools, ...calls] });
      box.appendChild(parts.root);
      if (parts.docs.length) withDocs.push(parts);
      tools = [];
    }
    const c = S.convs.find((x) => x.id === cid);
    $("#conv-title").textContent = c ? c.title : "Sylvie";
    $("#app").classList.remove("side-open");
    syncVoiceLinks();
    loadConvs();
    box.scrollTop = box.scrollHeight;
    const n = +params.get("doc");  // ?c=…&doc=1 opens the conversation's first document in the panel
    if (n && withDocs[n - 1] && cid === params.get("c")) showDoc(withDocs[n - 1], 0);
  }

  // ---------- documents (<doc title="…">…</doc>) → card + work panel ----------
  const DOC_RE = /<doc\b(?:\s+title\s*=\s*"([^"]*)")?[^>]*>([\s\S]*?)(<\/doc>|$)/gi;

  function splitDocs(text) {
    const parts = [];
    let last = 0, m;
    DOC_RE.lastIndex = 0;
    while ((m = DOC_RE.exec(text))) {
      if (m.index > last) parts.push({ kind: "text", text: text.slice(last, m.index) });
      const body = m[2].replace(/^\n+|\s+$/g, "");
      // A finished "document" of a line or two is just part of the answer (the 9B model wraps
      // single results in <doc>): show it inline instead of opening the work panel.
      if (m[3] && body.length <= 200 && body.split("\n").length <= 3) parts.push({ kind: "text", text: body });
      else parts.push({ kind: "doc", title: (m[1] || "Document").trim(), body, done: !!m[3] });
      last = DOC_RE.lastIndex;
      if (!m[0]) break;
    }
    // a "<do" at the very end may be the start of a tag that's still streaming: hide it
    const tail = text.slice(last).replace(/<d?o?c?$/i, "");
    if (tail) parts.push({ kind: "text", text: tail });
    return parts;
  }

  // Renders an answer; documents become cards. p.docs keeps them (edits made in the panel stick).
  function renderAnswer(p) {
    const parts = splitDocs(p.text);
    p.docs = p.docs || [];
    let html = "", n = 0;
    for (const part of parts) {
      if (part.kind === "text") { html += md(part.text); continue; }
      const d = p.docs[n] || (p.docs[n] = { title: part.title, body: part.body, edited: false });
      d.title = part.title;
      if (!d.edited) d.body = part.body;
      d.done = part.done;
      html += `<button type="button" class="doc-card" data-doc="${n}">${icon("doc")}<span class="dc-t"><span class="dc-name">${esc(d.title)}</span><span class="dc-sub">${part.done ? "Document · open in panel" : "Writing…"}</span></span></button>`;
      n++;
    }
    p.bubble.innerHTML = html;
    if (S.doc && S.doc.owner === p) showDoc(p, S.doc.index, true);
    return n;
  }

  function showDoc(p, index, keepTab) {
    const d = p.docs[index];
    if (!d) return;
    if (PANEL) { notifyNative({ type: "openChat", conversation_id: S.cid }); return; }
    S.doc = { owner: p, index };
    $("#work-panel").hidden = false;
    $("#wp-title").textContent = d.title;
    $("#wp-preview").innerHTML = md(d.body);
    if (!keepTab || $("#wp-edit").hidden) $("#wp-edit").value = d.body;
    if (!keepTab) setTab("preview");
    $("#wp-note").textContent = "";
  }
  function closePanel() { S.doc = null; $("#work-panel").hidden = true; }
  function setTab(t) {
    const edit = t === "edit";
    $("#wp-tab-preview").setAttribute("aria-selected", String(!edit));
    $("#wp-tab-edit").setAttribute("aria-selected", String(edit));
    $("#wp-preview").hidden = edit;
    $("#wp-edit").hidden = !edit;
    if (edit) $("#wp-edit").focus();
  }
  function currentDoc() { return S.doc ? S.doc.owner.docs[S.doc.index] : null; }
  $("#wp-tab-preview").onclick = () => {
    const d = currentDoc();
    if (d) $("#wp-preview").innerHTML = md(d.body);
    setTab("preview");
  };
  $("#wp-tab-edit").onclick = () => setTab("edit");
  $("#wp-edit").oninput = () => { const d = currentDoc(); if (d) { d.body = $("#wp-edit").value; d.edited = true; } };
  $("#wp-close").onclick = closePanel;
  const flash = (t) => { $("#wp-note").textContent = t; setTimeout(() => ($("#wp-note").textContent = ""), 2500); };
  $("#wp-copy").onclick = async () => { const d = currentDoc(); if (d) flash((await copyText(d.body)) ? "Copied" : "Couldn't copy"); };
  $("#wp-pdf").onclick = () => {
    $("#wp-tab-preview").click();
    if (window.webkit?.messageHandlers?.sylvie) notifyNative({ type: "print" }); else window.print();
  };
  $("#wp-save").onclick = async () => {
    const d = currentDoc();
    if (!d) return;
    try { const r = await api("/api/notes", { method: "POST", body: { title: d.title, content: d.body } }); flash("Saved to notes"); $("#wp-save").title = r.file; }
    catch (e) { flash("Couldn't save: " + e.message); }
  };

  // ---------- messages ----------
  function userMsg(text) {
    const m = el("div", "msg user");
    const b = el("div", "bubble");
    const [main, ...att] = text.split("\n\u0001");
    b.textContent = main;
    if (att.length) b.insertAdjacentHTML("beforeend", `<div class="att-note">${icon("clip", "i-14")}${esc(att.join(", "))}</div>`);
    m.appendChild(b);
    return m;
  }

  function sourcesHtml(sources) {
    if (!sources || !sources.length) return "";
    return sources.map((s) => s.url
      ? `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" title="${esc(s.url)}"><b>${s.n}</b>${esc(s.title || s.url)}</a>`
      : `<span title="${esc(s.path || "")}"><b>${s.n}</b>${esc(s.title || s.path || "")}</span>`).join("");
  }

  function partsFor(root, m = {}) {
    root.innerHTML = `<div class="notes"></div><details class="thinking" hidden><summary>Thinking</summary><div></div></details>
      <div class="status" hidden></div><div class="bubble"></div><div class="confirm-slot"></div><div class="sources"></div><div class="actions" hidden></div>`;
    const p = {
      root, bubble: $(".bubble", root), status: $(".status", root), notes: $(".notes", root), confirm: $(".confirm-slot", root),
      sources: $(".sources", root), actions: $(".actions", root), thinking: $("details.thinking", root),
      text: m.content || "", think: "", id: m.id || null, docs: [],
    };
    p.bubble.addEventListener("click", (e) => { const c = e.target.closest(".doc-card"); if (c) showDoc(p, +c.dataset.doc); });
    renderAnswer(p);
    if (m.sources) p.sources.innerHTML = sourcesHtml(m.sources);
    if (m.tool_calls) for (const t of m.tool_calls) addToolNote(p, t.name, t.arguments || t.args);
    if (m.id) renderActions(p, m);
    return p;
  }
  const assistantMsg = (m) => partsFor(el("div", "msg assistant"), m);

  function renderActions(p, m) {
    const a = p.actions;
    a.hidden = false;
    const fb = m.feedback || 0;
    const ms = m.trace && m.trace.marks_ms && m.trace.marks_ms.first_token;
    a.innerHTML = `<button data-a="up" class="${fb === 1 ? "on" : ""}" aria-label="Good answer" aria-pressed="${fb === 1}">${icon("up")}</button>
      <button data-a="down" class="${fb === -1 ? "on" : ""}" aria-label="Bad answer" aria-pressed="${fb === -1}">${icon("down")}</button>
      <button data-a="edit">Edit to what I wanted</button>
      <button data-a="copy" aria-label="Copy answer">${icon("copy")}</button>
      ${ms ? `<span class="timing" title="Time to first word">${(ms / 1000).toFixed(1)} s · ${esc(m.mode || "")}</span>` : ""}`;
    if (m.edited_content && !$(".edited", p.root)) a.insertAdjacentHTML("afterend", `<div class="edited">Your edit was saved. Sylvie will learn from it.</div>`);
    a.onclick = async (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.a === "copy" && (await copyText(p.text))) { b.innerHTML = icon("check"); setTimeout(() => (b.innerHTML = icon("copy")), 1200); }
      if (b.dataset.a === "up" || b.dataset.a === "down") {
        const v = b.dataset.a === "up" ? 1 : -1;
        const cur = b.classList.contains("on") ? null : v;
        await api(`/api/messages/${p.id}/feedback`, { method: "POST", body: { value: cur } });
        a.querySelectorAll("[data-a=up],[data-a=down]").forEach((x) => { x.classList.remove("on"); x.setAttribute("aria-pressed", "false"); });
        if (cur) { b.classList.add("on"); b.setAttribute("aria-pressed", "true"); }
        if (cur === -1 && !$(".why-box", p.root)) askWhy(p);
      }
      if (b.dataset.a === "edit") editAnswer(p, m);
    };
  }

  function askWhy(p) {
    const box = el("div", "why-box", `<input placeholder="What was wrong? (optional, helps Sylvie learn)" aria-label="What was wrong"><button class="btn">Send</button>
      <span class="meta">Sylvie's teacher will review it. See About Lea → Corrections.</span>`);
    const inp = $("input", box);
    const go = async () => {
      if (inp.value.trim()) await api(`/api/messages/${p.id}/teach`, { method: "POST", body: { comment: inp.value.trim() } });
      box.innerHTML = `<span class="meta">Thanks. The teacher will look at it.</span>`;
      refreshBadge();
    };
    $("button", box).onclick = go;
    inp.onkeydown = (e) => { if (e.key === "Enter") go(); };
    p.actions.insertAdjacentElement("afterend", box);
    inp.focus();
  }

  async function refreshBadge() {
    try {
      const pending = await api("/api/corrections?status=proposed");
      const b = $("#corr-badge");
      b.hidden = !pending.length;
      b.textContent = pending.length;
      $("#open-memory").classList.toggle("has-badge", pending.length > 0);
    } catch (_) {}
  }

  function editAnswer(p, m) {
    const box = el("div", "edit-box");
    box.innerHTML = `<textarea aria-label="What you wanted"></textarea><div class="row"><button class="btn btn-quiet">Cancel</button><button class="btn btn-primary">Save</button></div>`;
    const ta = $("textarea", box);
    ta.value = m.edited_content || p.text;
    p.bubble.replaceWith(box);
    ta.focus();
    const [cancel, save] = box.querySelectorAll("button");
    cancel.onclick = () => box.replaceWith(p.bubble);
    save.onclick = async () => {
      await api(`/api/messages/${p.id}/edit`, { method: "POST", body: { text: ta.value } });
      m.edited_content = ta.value;
      box.replaceWith(p.bubble);
      if (!$(".edited", p.root)) p.actions.insertAdjacentHTML("afterend", `<div class="edited">Your edit was saved. Sylvie will learn from it.</div>`);
    };
  }

  // Copy buttons in code blocks + citation clicks
  document.addEventListener("click", (e) => {
    const c = e.target.closest(".copy-code");
    if (c) copyText($("code", c.closest(".code")).textContent).then((ok) => { c.textContent = ok ? "Copied" : "Couldn't copy"; setTimeout(() => (c.textContent = "Copy"), 1200); });
    const cite = e.target.closest(".cite");
    if (cite) {
      const link = [...(cite.closest(".msg, #wake") || document).querySelectorAll(".sources b")].find((b) => b.textContent === cite.dataset.n);
      if (link && link.parentElement.href) window.open(link.parentElement.href, "_blank", "noopener");
    }
  });

  // ---------- sending ----------
  const input = $("#input");
  function autoGrow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 240) + "px";
    input.style.overflowY = input.scrollHeight > 240 ? "auto" : "hidden";
  }
  input.addEventListener("input", autoGrow);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); }
  });
  $("#composer").onsubmit = (e) => { e.preventDefault(); S.streaming ? stop() : send(); };

  function setStreaming(on) {
    S.streaming = on;
    const b = $("#send");
    b.classList.toggle("stop", on);
    b.innerHTML = icon(on ? "stop" : "send", "i-18");
    b.setAttribute("aria-label", on ? "Stop" : "Send");
  }

  function stop() {
    if (S.controller) S.controller.abort();
    if (S.cid) api(`/api/chat/${S.cid}/stop`, { method: "POST" }).catch(() => {});
    setStreaming(false);
  }

  async function send(textOverride, channel = "chat") {
    const text = (textOverride ?? (PANEL ? $("#wake-q").value : input.value)).trim();
    if (!text || S.streaming) return;
    input.value = ""; autoGrow();
    let p;
    if (PANEL) {
      p = wakeTurn(text);
    } else {
      $("#empty")?.remove();
      const box = $("#messages");
      const names = S.attachments.map((a) => a.name);
      box.appendChild(userMsg(text + (names.length ? "\n\u0001" + names.join("\n\u0001") : "")));
      p = assistantMsg();
      box.appendChild(p.root);
      box.scrollTop = box.scrollHeight;
    }
    p.bubble.classList.add("cursor");

    const mode = $("#mode").value;
    const body = { text, conversation_id: S.cid, channel, attachments: S.attachments, thinking: mode === "auto" ? null : mode === "think" };
    S.attachments = []; renderAttachments();
    setStreaming(true);
    setOrb("thinking");
    S.controller = new AbortController();
    let meta = {}, done = null;
    const box = PANEL ? $("#wake-body") : $("#messages");
    try {
      const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: S.controller.signal });
      if (!r.ok) throw new Error(await r.text());
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      let scrollPinned = true;
      box.onscroll = () => { scrollPinned = box.scrollHeight - box.scrollTop - box.clientHeight < 60; };
      for (;;) {
        const { value, done: end } = await reader.read();
        if (end) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const line = buf.slice(0, i); buf = buf.slice(i + 2);
          if (!line.startsWith("data: ")) continue;
          const ev = JSON.parse(line.slice(6));
          handle(ev, p);
          if (ev.type === "meta") { meta = ev; if (!S.cid) { S.cid = ev.conversation_id; $("#conv-title").textContent = ev.title; syncVoiceLinks(); loadConvs(); } }
          if (ev.type === "done") done = ev;
        }
        if (scrollPinned) box.scrollTop = box.scrollHeight;
        if (PANEL) reportPanelSize();
      }
    } catch (err) {
      if (err.name !== "AbortError") p.bubble.insertAdjacentHTML("beforeend", `<p class="error">${esc(String(err.message || err))}</p>`);
    } finally {
      p.bubble.classList.remove("cursor");
      p.status.hidden = true;
      setStreaming(false);
      setOrb("idle");
      S.controller = null;
      if (done && done.message_id) { p.id = done.message_id; renderActions(p, { id: done.message_id, trace: done.trace, mode: p.mode }); }
      notifyNative({ type: "answer", text: p.text, conversation_id: meta.conversation_id });
      if (PANEL) reportPanelSize();
    }
  }

  const TOOLS = {
    web_search: ["globe", "Searched the web"], open_url: ["globe", "Read a web page"], calculator: ["calc", "Calculated"],
    search_documents: ["folder", "Searched your files"], music: ["music", "Spotify"], play_music: ["music", "Played on Spotify"],
    calendar_events: ["calendar", "Checked your calendar"], create_event: ["calendar", "Added to your calendar"],
    create_reminder: ["bell", "Added a reminder"], set_timer: ["timer", "Set a timer"], open_app: ["app", "Opened an app"],
    open_in_browser: ["globe", "Opened in your browser"], run_shortcut: ["bolt", "Ran a shortcut"],
    read_emails: ["mail", "Read your emails"], draft_email: ["mail", "Drafted an email"], send_email: ["mail", "Email"],
  };
  function addToolNote(p, name, args) {
    const [ic, label] = TOOLS[name] || ["sparkle", name];
    const q = args && args.query ? `: ${args.query}` : "";
    p.notes.appendChild(el("div", "note", icon(ic, "i-14") + esc(label + q)));
  }

  function handle(ev, p) {
    switch (ev.type) {
      case "text": {
        p.text += ev.text;
        const n = renderAnswer(p);
        // A document opens in the panel once it's clearly a real one (more than a line or two);
        // a tiny one ends up inline when it finishes (see splitDocs).
        const d = n ? p.docs[n - 1] : null;
        if (d && !PANEL && p.autoOpened !== n - 1 && (d.body.length > 200 || d.body.split("\n").length > 3)) {
          p.autoOpened = n - 1;
          showDoc(p, n - 1);
        }
        p.status.hidden = true;
        notifyNative({ type: "delta", text: ev.text });
        break;
      }
      case "thinking":
        p.think += ev.text;
        p.thinking.hidden = false;
        $("div", p.thinking).textContent = p.think;
        break;
      case "status":
        if (ev.mode) p.mode = ev.mode;
        if (ev.text) { p.status.hidden = false; p.status.textContent = ev.text; notifyNative({ type: "status", text: ev.text }); }
        break;
      case "tool":
        addToolNote(p, ev.name, ev.args);
        if (p.text && !p.text.endsWith("\n")) p.text += "\n\n";
        break;
      case "sources":
        p.sources.innerHTML = sourcesHtml(ev.sources);
        break;
      case "notice":
        if (ev.kind === "preference") p.notes.appendChild(el("div", "note", icon("star", "i-14") + "Remembered: " + esc(ev.item.preference)));
        if (ev.kind === "forgot") p.notes.appendChild(el("div", "note", icon("trash", "i-14") + "Forgot: " + esc(ev.items.map((f) => f.text).join("; "))));
        break;
      case "action": {
        if (ev.status === "pending") {
          p.confirm.appendChild(confirmCard(ev));
          notifyNative({ type: "pendingConfirm", value: true });
          if (PANEL) $("#wake-hint").textContent = "Say “yes” to send · nothing is sent without your OK";
        } else {
          const label = { done: "Done", failed: "Not done", cancelled: "Cancelled", undone: "Undone" }[ev.status] || ev.status;
          const ic = { done: "check", undone: "undo" }[ev.status] || "alert";
          p.notes.appendChild(el("div", "note" + (ev.status === "done" ? " ok" : ""), icon(ic, "i-14") + esc(label + (ev.result ? ": " + ev.result : ""))));
          notifyNative({ type: "pendingConfirm", value: false });
        }
        break;
      }
      case "error":
        p.bubble.insertAdjacentHTML("beforeend", `<p class="error">${esc(ev.text)}</p>`);
        break;
    }
  }

  // A "Send?" card (design §2.3): app + recipient, the exact content, Send / Edit / Cancel.
  function confirmCard(ev) {
    const a = ev.args || {};
    const kinds = {
      send_email: ["mail", `Mail · to ${a.to || "?"}`, "Send"],
      run_shortcut: ["bolt", `Shortcuts · ${a.name || "?"}`, "Run"],
    };
    const [ic, head, yes] = kinds[ev.name] || ["alert", "Needs your OK", "Yes, do it"];
    const body = ev.name === "send_email" && a.body
      ? `${a.subject ? `<div class="meta">Subject: ${esc(a.subject)}</div>` : ""}${esc(a.body)}`
      : esc(ev.summary);
    const card = el("div", "confirm-card", `<div class="cc-head">${icon(ic, "i-14")}${esc(head)}</div>
      <div class="cc-body">${body}</div>
      <div class="row"><button class="btn btn-primary" data-v="yes">${yes}</button><button class="btn" data-v="edit">Edit</button><button class="btn btn-quiet" data-v="no">Cancel</button></div>`);
    card.onclick = (e) => {
      const b = e.target.closest("button");
      if (!b || S.streaming) return;
      if (b.dataset.v === "edit") {  // Lea says what to change; nothing is sent meanwhile
        const field = PANEL ? $("#wake-q") : input;
        if (PANEL) { field.readOnly = false; field.value = ""; }
        field.placeholder = "Tell Sylvie what to change…";
        field.focus();
        return;
      }
      card.querySelectorAll("button").forEach((x) => (x.disabled = true));
      notifyNative({ type: "pendingConfirm", value: false });
      if (PANEL) $("#wake-hint").textContent = "Say “Sylvie” to talk · Esc to close";
      send(b.dataset.v);
    };
    return card;
  }

  // ---------- attachments ----------
  function renderAttachments() {
    const box = $("#attachments");
    box.innerHTML = "";
    S.attachments.forEach((a, i) => {
      const chip = el("span", "att", `${icon("doc", "i-14")}${esc(a.name)} <small>${Math.round(a.chars / 1000)}k chars</small><button aria-label="Remove ${esc(a.name)}">${icon("close", "i-14")}</button>`);
      $("button", chip).onclick = () => { S.attachments.splice(i, 1); renderAttachments(); };
      box.appendChild(chip);
    });
  }
  async function addFiles(files) {
    for (const f of files) {
      const fd = new FormData();
      fd.append("file", f);
      try { S.attachments.push(await api("/api/upload", { method: "POST", body: fd })); }
      catch (e) { alert(`Couldn't add ${f.name}: ${e.message}`); }
    }
    renderAttachments();
  }
  $("#attach").onclick = () => $("#file-input").click();
  $("#file-input").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };
  let dragDepth = 0;
  addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; document.body.classList.add("dragging"); });
  addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove("dragging"); } });
  addEventListener("dragover", (e) => e.preventDefault());
  addEventListener("drop", (e) => { e.preventDefault(); dragDepth = 0; document.body.classList.remove("dragging"); if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files); });

  // ---------- About Lea dialog ----------
  let memTab = "preferences";
  $("#open-memory").onclick = () => {
    if (!$("#corr-badge").hidden) memTab = "corrections";  // proposals are waiting: show them first
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x.dataset.tab === memTab));
    $("#memory-dialog").showModal();
    renderMemory();
  };
  document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => {
    memTab = b.dataset.tab;
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
    renderMemory();
  }));

  async function renderMemory() {
    const body = $("#memory-body");
    body.innerHTML = "Loading…";
    const btn = (label, fn) => { const b = el("button", null, label); b.onclick = async () => { await fn(); renderMemory(); }; return b; };
    const item = (html, ...buttons) => { const r = el("div", "item", `<div class="txt">${html}</div>`); buttons.forEach((b) => r.appendChild(b)); return r; };
    body.innerHTML = "";
    if (memTab === "facts") {
      const facts = await api("/api/memory?all=true");
      if (!facts.length) body.textContent = "Nothing yet. Sylvie remembers things you tell her about yourself.";
      for (const f of facts.filter((x) => !x.superseded_by)) {
        const r = item(`<span class="${f.active ? "" : "inactive"}">${esc(f.text)}</span><div class="meta">${esc(f.category)} · ${f.updated_at.slice(0, 10)}${f.sensitive ? " · sensitive" : ""}</div>`,
          f.active ? btn("Edit", async () => { const t = prompt("Edit fact", f.text); if (t) await api(`/api/memory/${f.id}`, { method: "PATCH", body: { text: t } }); })
                   : btn("Restore", () => api(`/api/memory/${f.id}/restore`, { method: "POST" })),
          ...(f.active ? [btn("Forget", () => api(`/api/memory/${f.id}`, { method: "DELETE" }))] : []));
        body.appendChild(r);
      }
      return;
    }
    if (memTab === "corrections") {
      const list = await api("/api/corrections");
      if (!list.length) body.textContent = "No corrections yet. Thumbs-down an answer or say “Sylvie, that's wrong” and her teacher proposes a better one here.";
      for (const c of list) {
        const card = el("div", "corr " + c.status);
        const status = { queued: "The teacher will review this soon", proposed: "", accepted: "Accepted", rejected: "Rejected", failed: "The review failed" }[c.status];
        card.innerHTML = `<div class="meta">You asked: “${esc(c.question.slice(0, 200))}”</div>
          <div class="old bubble"><b>Before</b>${md(c.original)}</div>
          ${c.proposal && c.status !== "accepted" ? `<div class="new bubble"><b>Proposed</b>${md(c.proposal)}</div>` : ""}
          ${c.final_answer && c.status === "accepted" ? `<div class="new bubble"><b>Now</b>${md(c.final_answer)}</div>` : ""}
          ${c.diagnosis && c.diagnosis.problem ? `<div class="meta">Problem: ${esc(c.diagnosis.problem)}</div>` : ""}
          ${c.rule && c.rule.type !== "none" && c.rule.text ? `<div class="meta">Rule it will remember: ${esc(c.rule.text)}</div>` : ""}
          ${c.sources && c.sources.length ? `<div class="sources">${sourcesHtml(c.sources)}</div>` : ""}
          <div class="meta">${status}</div>`;
        if (c.status === "proposed") {
          const row = el("div", "row");
          row.appendChild(btn("Reject", () => api(`/api/corrections/${c.id}/reject`, { method: "POST" })));
          row.appendChild(btn("Edit & accept", async () => {
            const t = prompt("Edit the answer", c.proposal);
            if (t) await api(`/api/corrections/${c.id}/accept`, { method: "POST", body: { text: t } });
          }));
          const ok = btn("Accept", () => api(`/api/corrections/${c.id}/accept`, { method: "POST", body: {} }));
          ok.className = "btn btn-primary";
          row.appendChild(ok);
          card.appendChild(row);
        }
        body.appendChild(card);
      }
      refreshBadge();
      return;
    }
    const prof = await api("/api/profile");
    if (memTab === "preferences") {
      const add = el("div", "add-row", `<input placeholder="Add a rule, e.g. “Don't use emojis”" aria-label="New preference"><button class="btn btn-primary">Add</button>`);
      $("button", add).onclick = async () => { const v = $("input", add).value.trim(); if (v) { await api("/api/preferences", { method: "POST", body: { text: v } }); renderMemory(); } };
      body.appendChild(add);
      if (!prof.preferences.length) body.appendChild(el("p", "meta", "Tell Sylvie things like “shorter please” or “no emojis” and they'll show up here."));
      for (const p of prof.preferences) {
        const from = p.scope === "claude" ? `<div class="meta">${p.active ? "From your Claude chats" : "Suggested from your Claude chats · off until you turn it on"}</div>` : "";
        body.appendChild(item(`<span class="${p.active ? "" : "inactive"}">${esc(p.text)}</span>${from}${p.source_quote ? `<div class="meta">“${esc(p.source_quote)}”</div>` : ""}`,
          btn(p.active ? "Turn off" : "Turn on", () => api(`/api/preferences/${p.id}`, { method: "PATCH", body: { active: !p.active } }))));
      }
    }
    if (memTab === "interests") {
      if (!prof.interests.length) body.textContent = "No interests noticed yet.";
      for (const i of prof.interests) {
        body.appendChild(item(`${esc(i.topic)}<span class="badge ${i.status}">${i.status}</span><div class="meta">strength ${i.score} · mentioned ${i.mentions}×</div>`,
          ...(i.status !== "confirmed" ? [btn("Confirm", () => api(`/api/profile/interests/${i.id}`, { method: "PATCH", body: { status: "confirmed" } }))] : []),
          ...(i.status !== "rejected" ? [btn("Not me", () => api(`/api/profile/interests/${i.id}`, { method: "PATCH", body: { status: "rejected" } }))] : []),
          btn("Delete", () => api(`/api/profile/interests/${i.id}`, { method: "DELETE" }))));
      }
    }
    if (memTab === "traits") {
      if (!prof.traits.length) body.textContent = "No style traits noticed yet.";
      for (const t of prof.traits) {
        body.appendChild(item(`${esc(t.description)}<span class="badge ${t.status}">${t.status === "guess" ? "guess" : t.status}</span>
          <div class="meta">confidence ${Math.round(t.confidence * 100)}%${t.evidence.length ? " · e.g. “" + esc(t.evidence[t.evidence.length - 1]) + "”" : ""}</div>`,
          ...(t.status !== "confirmed" ? [btn("Confirm", () => api(`/api/profile/traits/${t.id}`, { method: "PATCH", body: { status: "confirmed" } }))] : []),
          ...(t.status !== "rejected" ? [btn("Not me", () => api(`/api/profile/traits/${t.id}`, { method: "PATCH", body: { status: "rejected" } }))] : []),
          btn("Delete", () => api(`/api/profile/traits/${t.id}`, { method: "DELETE" }))));
      }
    }
  }

  // ---------- keyboard ----------
  addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); newChat(); }
    if (e.key === "Escape" && S.streaming) stop();
    else if (e.key === "Escape" && !PANEL && S.doc && !$("#memory-dialog").open) closePanel();
  });

  // ---------- native bridge (Mac app) ----------
  function notifyNative(msg) {
    try { window.webkit?.messageHandlers?.sylvie?.postMessage(msg); } catch (_) {}
  }

  // ---------- wake panel (Mac app, ?embed=panel): one exchange at a time ----------
  const wakeOrb = () => $("#wake-orb");
  function setOrb(state) { if (PANEL && wakeOrb()) wakeOrb().setState(state); }

  function wakeTurn(question, spoken) {
    const q = $("#wake-q");
    q.value = spoken ? `“${question}”` : question;
    q.readOnly = true;
    growWake();
    const root = $("#wake-body");
    return partsFor(root);
  }
  function growWake() { const q = $("#wake-q"); q.style.height = "auto"; q.style.height = q.scrollHeight + "px"; }
  function reportPanelSize() {
    requestAnimationFrame(() => notifyNative({ type: "panelSize", height: Math.ceil($("#wake").getBoundingClientRect().height) }));
  }

  // Voice turns run in the Mac app, which forwards each server event here.
  let voiceP = null;
  function voiceEvent(ev) {
    if (PANEL) {
      if (ev.type === "transcript") {
        if (!ev.text) return;
        voiceP = wakeTurn(ev.text, true);
        voiceP.bubble.classList.add("cursor");
      } else if (ev.type === "meta") {
        S.cid = ev.conversation_id;
      } else if (voiceP && ev.type === "done") {
        voiceP.bubble.classList.remove("cursor");
        voiceP.status.hidden = true;
        if (ev.message_id) { voiceP.id = ev.message_id; renderActions(voiceP, { id: ev.message_id, trace: ev.trace, mode: voiceP.mode }); }
        voiceP = null;
      } else if (voiceP && ev.type !== "speak") {
        handle(ev, voiceP);
      }
      reportPanelSize();
      return;
    }
    const box = $("#messages");
    if (ev.type === "transcript") {
      if (!ev.text) return;
      $("#empty")?.remove();
      box.appendChild(userMsg(ev.text));
      voiceP = assistantMsg();
      voiceP.bubble.classList.add("cursor");
      box.appendChild(voiceP.root);
    } else if (ev.type === "meta") {
      if (S.cid !== ev.conversation_id) { S.cid = ev.conversation_id; $("#conv-title").textContent = ev.title; syncVoiceLinks(); loadConvs(); }
    } else if (voiceP && ev.type === "done") {
      voiceP.bubble.classList.remove("cursor");
      voiceP.status.hidden = true;
      if (ev.message_id) { voiceP.id = ev.message_id; renderActions(voiceP, { id: ev.message_id, trace: ev.trace, mode: voiceP.mode }); }
      voiceP = null;
    } else if (voiceP && ev.type !== "speak") {
      handle(ev, voiceP);
    }
    box.scrollTop = box.scrollHeight;
  }

  // The Mac app tells the panel what the orb should show (listening, thinking, speaking, muted…)
  function voiceState(state, detail) {
    setOrb(state);
    const q = $("#wake-q");
    if (state === "listening") {
      $("#wake-body").innerHTML = "";
      q.value = ""; q.readOnly = true; q.placeholder = "Listening…"; growWake();
      $("#wake-hint").textContent = "Say “Sylvie, stop” to interrupt · Esc to close";
    } else if (state === "idle" && !S.streaming) {
      q.readOnly = false; q.placeholder = "Ask Sylvie…";
    }
    if (detail) $("#wake-hint").textContent = detail;
    reportPanelSize();
  }
  function voiceLevel(x) { if (wakeOrb()) wakeOrb().setLevel(x); }
  function focusInput() {
    if (PANEL) { const q = $("#wake-q"); q.readOnly = false; q.focus(); q.select(); } else input.focus();
  }

  window.sylvie = { send: (t, channel) => send(t, channel || "voice"), newChat, openConv, stop, voiceEvent, voiceState, voiceLevel, focusInput };

  // ---------- start ----------
  if (PANEL) {
    document.body.classList.add("panel");
    const q = $("#wake-q");
    q.addEventListener("input", growWake);
    q.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!q.readOnly) send(); }
      if (e.key === "Escape" && !S.streaming) notifyNative({ type: "closePanel" });
    });
    q.addEventListener("click", () => { if (q.readOnly && !S.streaming) { q.readOnly = false; q.select(); } });
    $("#wake-open").onclick = () => notifyNative({ type: "openChat", conversation_id: S.cid });
    addEventListener("keydown", (e) => { if (e.key === "Escape" && !S.streaming) notifyNative({ type: "closePanel" }); });
    new ResizeObserver(reportPanelSize).observe($("#wake"));
  }
  syncVoiceLinks();
  pollHealth();
  refreshBadge();
  setInterval(refreshBadge, 60000);
  loadConvs().then(() => { if (params.get("c")) openConv(params.get("c")); });
})();
