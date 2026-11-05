// Sylvie chat UI. Plain JS, no dependencies, no external requests.
(function () {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const esc = window.escapeHtml, md = window.renderMarkdown;
  const params = new URLSearchParams(location.search);

  const S = { cid: null, streaming: false, controller: null, attachments: [], convs: [] };

  // ---------- API ----------
  async function api(path, opts = {}) {
    const o = { headers: {}, ...opts };
    if (o.body && !(o.body instanceof FormData)) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(o.body); }
    const r = await fetch(path, o);
    if (r.status === 401) { location.reload(); throw new Error("locked"); }
    if (!r.ok) throw new Error((await r.text()) || r.statusText);
    return r.headers.get("content-type")?.includes("json") ? r.json() : r;
  }

  // ---------- theme ----------
  function applyTheme(t) { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }
  try { applyTheme(localStorage.getItem("theme")); } catch (_) {}
  $("#theme-toggle").onclick = () => {
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    const cur = document.documentElement.dataset.theme || (dark ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("theme", next); } catch (_) {}
  };

  // ---------- health ----------
  async function pollHealth() {
    const dot = $("#status-dot");
    try {
      const h = await api("/api/health");
      if (h.ready) { dot.className = "dot ready"; dot.title = "Ready"; return; }
      if (h.error) { dot.className = "dot err"; dot.title = h.error; return; }
      dot.title = "Loading the model…";
    } catch (_) { dot.className = "dot err"; dot.title = "Server not reachable"; }
    setTimeout(pollHealth, 1500);
  }

  // ---------- sidebar ----------
  function group(c) {
    const d = new Date(c.updated_at), now = new Date();
    const days = (now - d) / 86400000;
    if (d.toDateString() === now.toDateString()) return "Today";
    if (days < 7) return "Previous 7 days";
    if (days < 30) return "Previous 30 days";
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
      row.innerHTML = `<span class="t">${esc(c.title)}</span><span class="acts"><button title="Rename">✎</button><button title="Delete">🗑</button></span>`;
      row.onclick = (e) => { if (!e.target.closest("button,input")) openConv(c.id); };
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
  $("#new-chat").onclick = () => newChat();
  $("#toggle-sidebar").onclick = () => { $("#app").classList.toggle(innerWidth < 720 ? "side-open" : "no-side"); };

  function newChat() {
    if (S.streaming) stop();
    S.cid = null;
    $("#messages").innerHTML = "";
    $("#messages").appendChild(emptyState());
    $("#conv-title").textContent = "Sylvie";
    document.querySelectorAll(".conv.active").forEach((c) => c.classList.remove("active"));
    $("#input").focus();
  }

  function emptyState() {
    const e = el("div", "empty");
    e.id = "empty";
    e.innerHTML = `<div class="logo">S</div><h2>Hello Lea</h2><p>Ask me anything — in English, español or français.</p>`;
    return e;
  }

  async function openConv(cid) {
    if (S.streaming) stop();
    S.cid = cid;
    const msgs = await api(`/api/conversations/${cid}/messages`);
    const box = $("#messages");
    box.innerHTML = "";
    for (const m of msgs) box.appendChild(m.role === "user" ? userMsg(m.content) : assistantMsg(m).root);
    const c = S.convs.find((x) => x.id === cid);
    $("#conv-title").textContent = c ? c.title : "Sylvie";
    document.querySelectorAll(".conv").forEach((r, i) => r.classList.toggle("active", false));
    loadConvs();
    box.scrollTop = box.scrollHeight;
  }

  // ---------- messages ----------
  function userMsg(text) {
    const m = el("div", "msg user");
    const b = el("div", "bubble");
    b.textContent = text;
    m.appendChild(b);
    return m;
  }

  function sourcesHtml(sources) {
    if (!sources || !sources.length) return "";
    return sources.map((s) => s.url
      ? `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer"><b>${s.n}</b>${esc(s.title || s.url)}</a>`
      : `<span><b>${s.n}</b>${esc(s.title || s.path || "")}</span>`).join("");
  }

  function assistantMsg(m = {}) {
    const root = el("div", "msg assistant");
    root.innerHTML = `<div class="who"><span class="avatar">S</span>Sylvie</div>
      <div class="notices"></div><details class="thinking" hidden><summary>Thinking</summary><div></div></details>
      <div class="status" hidden></div><div class="tools"></div><div class="bubble"></div>
      <div class="sources"></div><div class="actions" hidden></div>`;
    const parts = {
      root, bubble: $(".bubble", root), status: $(".status", root), tools: $(".tools", root),
      sources: $(".sources", root), actions: $(".actions", root), notices: $(".notices", root),
      thinking: $("details.thinking", root), text: m.content || "", think: "", id: m.id || null,
    };
    parts.bubble.innerHTML = md(parts.text);
    if (m.sources) parts.sources.innerHTML = sourcesHtml(m.sources);
    if (m.id) renderActions(parts, m);
    return parts;
  }

  function renderActions(p, m) {
    const a = p.actions;
    a.hidden = false;
    const fb = m.feedback || 0;
    const ms = m.trace && m.trace.marks_ms && m.trace.marks_ms.first_token;
    a.innerHTML = `<button data-a="copy" title="Copy">⧉</button>
      <button data-a="up" class="${fb === 1 ? "on" : ""}" title="Good answer">👍</button>
      <button data-a="down" class="${fb === -1 ? "on" : ""}" title="Bad answer">👎</button>
      <button data-a="edit" title="Edit to what I wanted">✎ Edit to what I wanted</button>
      ${ms ? `<span class="timing" title="Time to first word">${(ms / 1000).toFixed(1)} s · ${m.mode || ""}</span>` : ""}`;
    if (m.edited_content) a.insertAdjacentHTML("afterend", `<div class="edited">✓ Your edit was saved — Sylvie will learn from it.</div>`);
    a.onclick = async (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.a === "copy") { navigator.clipboard.writeText(p.text); b.textContent = "✓"; setTimeout(() => (b.textContent = "⧉"), 1200); }
      if (b.dataset.a === "up" || b.dataset.a === "down") {
        const v = b.dataset.a === "up" ? 1 : -1;
        const cur = b.classList.contains("on") ? null : v;
        await api(`/api/messages/${p.id}/feedback`, { method: "POST", body: { value: cur } });
        a.querySelectorAll("[data-a=up],[data-a=down]").forEach((x) => x.classList.remove("on"));
        if (cur) b.classList.add("on");
      }
      if (b.dataset.a === "edit") editAnswer(p, m);
    };
  }

  function editAnswer(p, m) {
    const box = el("div", "edit-box");
    box.innerHTML = `<textarea></textarea><div class="row"><button class="btn-ghost">Cancel</button><button class="btn-primary">Save</button></div>`;
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
      if (!$(".edited", p.root)) p.actions.insertAdjacentHTML("afterend", `<div class="edited">✓ Your edit was saved — Sylvie will learn from it.</div>`);
    };
  }

  // Copy buttons in code blocks + citation clicks
  $("#messages").addEventListener("click", (e) => {
    const c = e.target.closest(".copy-code");
    if (c) { navigator.clipboard.writeText($("code", c.closest(".code")).textContent); c.textContent = "Copied"; setTimeout(() => (c.textContent = "Copy"), 1200); }
    const cite = e.target.closest(".cite");
    if (cite) {
      const link = [...cite.closest(".msg").querySelectorAll(".sources b")].find((b) => b.textContent === cite.dataset.n);
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
    b.textContent = on ? "■" : "↑";
    b.title = on ? "Stop" : "Send (Enter)";
  }

  function stop() {
    if (S.controller) S.controller.abort();
    if (S.cid) api(`/api/chat/${S.cid}/stop`, { method: "POST" }).catch(() => {});
    setStreaming(false);
  }

  async function send(textOverride, channel = "chat") {
    const text = (textOverride ?? input.value).trim();
    if (!text || S.streaming) return;
    input.value = ""; autoGrow();
    const empty = $("#empty");
    if (empty) empty.remove();
    const box = $("#messages");
    box.appendChild(userMsg(text + (S.attachments.length ? `\n📎 ${S.attachments.map((a) => a.name).join(", ")}` : "")));
    const p = assistantMsg();
    p.bubble.classList.add("cursor");
    box.appendChild(p.root);
    box.scrollTop = box.scrollHeight;

    const mode = $("#mode").value;
    const body = { text, conversation_id: S.cid, channel, attachments: S.attachments, thinking: mode === "auto" ? null : mode === "think" };
    S.attachments = []; renderAttachments();
    setStreaming(true);
    S.controller = new AbortController();
    let meta = {}, done = null;
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
          if (ev.type === "meta") { meta = ev; if (!S.cid) { S.cid = ev.conversation_id; $("#conv-title").textContent = ev.title; loadConvs(); } }
          if (ev.type === "done") done = ev;
        }
        if (scrollPinned) box.scrollTop = box.scrollHeight;
      }
    } catch (err) {
      if (err.name !== "AbortError") p.bubble.insertAdjacentHTML("beforeend", `<p class="error">${esc(String(err.message || err))}</p>`);
    } finally {
      p.bubble.classList.remove("cursor");
      p.status.hidden = true;
      setStreaming(false);
      S.controller = null;
      if (done && done.message_id) { p.id = done.message_id; renderActions(p, { id: done.message_id, trace: done.trace, mode: p.mode }); }
      notifyNative({ type: "answer", text: p.text, conversation_id: meta.conversation_id });
    }
  }

  function handle(ev, p) {
    switch (ev.type) {
      case "text":
        p.text += ev.text;
        p.bubble.innerHTML = md(p.text);
        p.status.hidden = true;
        notifyNative({ type: "delta", text: ev.text });
        break;
      case "thinking":
        p.think += ev.text;
        p.thinking.hidden = false;
        $("div", p.thinking).textContent = p.think;
        break;
      case "status":
        if (ev.mode) p.mode = ev.mode;
        if (ev.text) { p.status.hidden = false; p.status.textContent = ev.text; notifyNative({ type: "status", text: ev.text }); }
        break;
      case "tool": {
        const label = {
          web_search: "🔎 Searched the web", open_url: "🌐 Opened a page", calculator: "🧮 Calculated",
          search_documents: "📄 Searched your documents", music: "🎵 Spotify", play_music: "🎵 Spotify",
          calendar_events: "📅 Checked your calendar", create_event: "📅 Added to calendar", create_reminder: "☑️ Reminder",
          set_timer: "⏰ Timer", open_app: "🖥 Opened app", open_in_browser: "🌐 Opened in browser",
          run_shortcut: "⚡️ Shortcut", read_emails: "✉️ Read emails", draft_email: "✉️ Draft", send_email: "✉️ Email",
        }[ev.name] || ev.name;
        p.tools.appendChild(el("span", "chip", esc(label + (ev.args && ev.args.query ? `: ${ev.args.query}` : ""))));
        if (p.text && !p.text.endsWith("\n")) p.text += "\n\n";
        break;
      }
      case "sources":
        p.sources.innerHTML = sourcesHtml(ev.sources);
        break;
      case "notice":
        if (ev.kind === "preference") p.notices.appendChild(el("div", "notice", "✓ Saved preference: " + esc(ev.item.preference)));
        if (ev.kind === "forgot") p.notices.appendChild(el("div", "notice", "🗑 Forgot: " + esc(ev.items.map((f) => f.text).join("; "))));
        break;
      case "action": {
        if (ev.status === "pending") {
          const card = el("div", "confirm-card", `<div>⚠️ ${esc(ev.summary)}</div>
            <div class="row"><button class="btn-ghost" data-v="no">No</button><button class="btn-primary" data-v="yes">Yes, do it</button></div>`);
          card.onclick = (e) => {
            const b = e.target.closest("button");
            if (!b || S.streaming) return;
            card.querySelectorAll("button").forEach((x) => (x.disabled = true));
            send(b.dataset.v);
          };
          p.notices.appendChild(card);
        } else {
          const label = { done: "✓ Done", failed: "✗ Not done", cancelled: "Cancelled", undone: "↩ Undone" }[ev.status] || ev.status;
          p.notices.appendChild(el("div", "notice", esc(label + (ev.result ? ": " + ev.result : ""))));
        }
        break;
      }
      case "error":
        p.bubble.insertAdjacentHTML("beforeend", `<p class="error">${esc(ev.text)}</p>`);
        break;
    }
  }

  // ---------- attachments ----------
  function renderAttachments() {
    const box = $("#attachments");
    box.innerHTML = "";
    S.attachments.forEach((a, i) => {
      const chip = el("span", "att", `📄 ${esc(a.name)} <small>${Math.round(a.chars / 1000)}k chars</small><button title="Remove">✕</button>`);
      $("button", chip).onclick = () => { S.attachments.splice(i, 1); renderAttachments(); };
      box.appendChild(chip);
    });
  }
  async function addFiles(files) {
    for (const f of files) {
      const fd = new FormData();
      fd.append("file", f);
      try {
        const r = await api("/api/upload", { method: "POST", body: fd });
        S.attachments.push(r);
      } catch (e) { alert(`Couldn't add ${f.name}: ${e.message}`); }
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

  // ---------- memory dialog ----------
  let memTab = "preferences";
  $("#open-memory").onclick = () => { $("#memory-dialog").showModal(); renderMemory(); };
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
    const prof = await api("/api/profile");
    if (memTab === "preferences") {
      const add = el("div", "add-row", `<input placeholder="Add a rule, e.g. “Don't use emojis”"><button class="btn-primary">Add</button>`);
      $("button", add).onclick = async () => { const v = $("input", add).value.trim(); if (v) { await api("/api/preferences", { method: "POST", body: { text: v } }); renderMemory(); } };
      body.appendChild(add);
      if (!prof.preferences.length) body.appendChild(el("p", "meta", "Tell Sylvie things like “shorter please” or “no emojis” and they'll show up here."));
      for (const p of prof.preferences) {
        body.appendChild(item(`<span class="${p.active ? "" : "inactive"}">${esc(p.text)}</span>${p.source_quote ? `<div class="meta">“${esc(p.source_quote)}”</div>` : ""}`,
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
  });

  // ---------- native bridge (Mac app, Phase 2) ----------
  function notifyNative(msg) {
    try { window.webkit?.messageHandlers?.sylvie?.postMessage(msg); } catch (_) {}
  }
  // Voice turns run in the Mac app, which forwards each server event here so the panel shows
  // the live transcript and the written answer.
  let voiceMsg = null;
  function voiceEvent(ev) {
    const box = $("#messages");
    const empty = $("#empty");
    if (ev.type === "transcript") {
      if (!ev.text) return;
      if (empty) empty.remove();
      box.appendChild(userMsg("🎙 " + ev.text));
      voiceMsg = assistantMsg();
      voiceMsg.bubble.classList.add("cursor");
      box.appendChild(voiceMsg.root);
    } else if (ev.type === "meta") {
      if (S.cid !== ev.conversation_id) { S.cid = ev.conversation_id; $("#conv-title").textContent = ev.title; loadConvs(); }
    } else if (voiceMsg && ev.type === "done") {
      voiceMsg.bubble.classList.remove("cursor");
      voiceMsg.status.hidden = true;
      if (ev.message_id) { voiceMsg.id = ev.message_id; renderActions(voiceMsg, { id: ev.message_id, trace: ev.trace, mode: voiceMsg.mode }); }
      voiceMsg = null;
    } else if (voiceMsg && ev.type !== "speak") {
      handle(ev, voiceMsg);
    }
    box.scrollTop = box.scrollHeight;
  }

  window.sylvie = { send: (t, channel) => send(t, channel || "voice"), newChat, openConv, stop, voiceEvent };

  // ---------- start ----------
  if (params.get("embed") === "panel") {
    document.body.classList.add("panel");
    const bar = el("div", "panel-bar", `<button id="open-full">Open full chat ↗</button><button id="panel-new">New</button>`);
    $("#main").prepend(bar);
    $("#open-full").onclick = () => notifyNative({ type: "openChat", conversation_id: S.cid });
    $("#panel-new").onclick = () => newChat();
    addEventListener("keydown", (e) => { if (e.key === "Escape" && !S.streaming) notifyNative({ type: "closePanel" }); });
  }
  pollHealth();
  loadConvs().then(() => { if (params.get("c")) openConv(params.get("c")); });
})();
