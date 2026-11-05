// Sylvie dashboard: everything Sylvie believes about Lea, editable; learning, versions, actions, privacy, speed.
(function () {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const esc = window.escapeHtml, md = window.renderMarkdown;
  const main = $("#dmain");
  try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch (_) {}

  async function api(path, opts = {}) {
    const o = { headers: {}, ...opts };
    if (o.body) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(o.body); }
    const r = await fetch(path, o);
    if (r.status === 401) { location.href = "/"; throw new Error("locked"); }
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }
  const btn = (label, fn, cls = "mini") => {
    const b = document.createElement("button");
    b.className = cls; b.textContent = label;
    b.onclick = async () => { b.disabled = true; try { await fn(); } finally { show(current); } };
    return b;
  };
  const card = (html, ...buttons) => {
    const c = document.createElement("div");
    c.className = "card";
    c.innerHTML = html;
    if (buttons.length) { const a = document.createElement("div"); a.className = "acts"; buttons.forEach((b) => a.appendChild(b)); c.appendChild(a); }
    return c;
  };
  const badge = (status) => `<span class="badge ${status}">${status === "guess" ? "guess" : status}</span>`;

  function sparkline(points, w = 140, h = 30) {
    if (!points.length) return "";
    const max = Math.max(1, ...points.map((p) => p.score));
    const xs = (i) => (i / Math.max(1, points.length - 1)) * w;
    const ys = (v) => h - 2 - (v / max) * (h - 4);
    const line = points.map((p, i) => `${i ? "L" : "M"}${xs(i).toFixed(1)},${ys(p.score).toFixed(1)}`).join("");
    return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="strength over time">
      <path class="area" d="${line}L${w},${h}L0,${h}Z"></path><path d="${line}"></path></svg>`;
  }

  // ---------- sections ----------
  const sections = {
    async about() {
      const p = await api("/api/profile");
      main.innerHTML = `<h2>About Lea</h2><p class="lead">How Sylvie sees you. Guesses stay guesses until you confirm them; anything wrong, delete it.</p><h3>How you like to talk</h3>`;
      if (!p.traits.length) main.insertAdjacentHTML("beforeend", `<p class="empty-note">Nothing noticed yet — this fills in as you talk with Sylvie.</p>`);
      for (const t of p.traits) {
        main.appendChild(card(
          `<div class="top"><span class="t">${esc(t.description)} ${badge(t.status)}</span><div class="bar" title="confidence"><i style="width:${Math.round(t.confidence * 100)}%"></i></div></div>
           ${t.evidence.map((e) => `<p class="quote">“${esc(e)}”</p>`).join("")}`,
          ...(t.status !== "confirmed" ? [btn("That's me", () => api(`/api/profile/traits/${t.id}`, { method: "PATCH", body: { status: "confirmed" } }))] : []),
          ...(t.status !== "rejected" ? [btn("Not me", () => api(`/api/profile/traits/${t.id}`, { method: "PATCH", body: { status: "rejected" } }))] : []),
          btn("Edit", async () => { const d = prompt("Describe it your way", t.description); if (d) await api(`/api/profile/traits/${t.id}`, { method: "PATCH", body: { description: d } }); }),
          btn("Delete", () => api(`/api/profile/traits/${t.id}`, { method: "DELETE" }))));
      }
      main.insertAdjacentHTML("beforeend", `<h3>Interests</h3>`);
      if (!p.interests.length) main.insertAdjacentHTML("beforeend", `<p class="empty-note">No interests yet.</p>`);
      for (const i of p.interests) {
        const hist = await api(`/api/profile/interests/${i.id}/history`).catch(() => []);
        main.appendChild(card(
          `<div class="top"><span class="t">${esc(i.topic)} ${badge(i.status)}</span>${sparkline(hist)}<span class="pill">${i.score}</span></div>
           <p class="quote">mentioned ${i.mentions}× · last ${i.last_seen.slice(0, 10)}</p>`,
          ...(i.status !== "confirmed" ? [btn("Yes, I care about this", () => api(`/api/profile/interests/${i.id}`, { method: "PATCH", body: { status: "confirmed" } }))] : []),
          ...(i.status !== "rejected" ? [btn("Not interested", () => api(`/api/profile/interests/${i.id}`, { method: "PATCH", body: { status: "rejected" } }))] : []),
          btn("Delete", () => api(`/api/profile/interests/${i.id}`, { method: "DELETE" }))));
      }
      main.insertAdjacentHTML("beforeend", `<h3>Your rules for Sylvie</h3>`);
      for (const pr of p.preferences) {
        main.appendChild(card(`<div class="top"><span class="t" style="${pr.active ? "" : "opacity:.5;text-decoration:line-through"}">${esc(pr.text)}</span></div>${pr.source_quote ? `<p class="quote">“${esc(pr.source_quote)}”</p>` : ""}`,
          btn(pr.active ? "Turn off" : "Turn on", () => api(`/api/preferences/${pr.id}`, { method: "PATCH", body: { active: !pr.active } })),
          btn("Edit", async () => { const t = prompt("Edit rule", pr.text); if (t) await api(`/api/preferences/${pr.id}`, { method: "PATCH", body: { text: t } }); })));
      }
      main.appendChild(btn("+ Add a rule", async () => { const t = prompt("A rule for Sylvie, e.g. “Don't use emojis”"); if (t) await api("/api/preferences", { method: "POST", body: { text: t } }); }, "btn-primary"));
    },

    async memory() {
      const facts = await api("/api/memory?all=true");
      main.innerHTML = `<h2>Memory</h2><p class="lead">Every fact Sylvie keeps about you. Edit or forget any of them; replaced facts are kept as history.</p>`;
      if (!facts.length) main.insertAdjacentHTML("beforeend", `<p class="empty-note">Nothing yet.</p>`);
      for (const f of facts) {
        const state = f.superseded_by ? "replaced" : f.forgotten_at ? "forgotten" : "";
        main.appendChild(card(
          `<div class="top"><span class="t" style="${state ? "opacity:.55" : ""}">${esc(f.text)}</span>${state ? `<span class="pill">${state}</span>` : ""}${f.sensitive ? `<span class="pill">sensitive</span>` : ""}</div>
           <p class="quote">${esc(f.category)} · ${f.updated_at.slice(0, 10)}${f.key ? " · " + esc(f.key) : ""}</p>`,
          ...(f.active ? [btn("Edit", async () => { const t = prompt("Edit fact", f.text); if (t) await api(`/api/memory/${f.id}`, { method: "PATCH", body: { text: t } }); }),
                          btn("Forget", () => api(`/api/memory/${f.id}`, { method: "DELETE" }))] : []),
          ...(f.forgotten_at ? [btn("Restore", () => api(`/api/memory/${f.id}/restore`, { method: "POST" }))] : [])));
      }
    },

    async learning() {
      const [corr, nights, stats] = await Promise.all([api("/api/corrections"), api("/api/overnight"), api("/api/training/stats")]);
      main.innerHTML = `<h2>Corrections &amp; nights</h2><p class="lead">What Sylvie's teacher proposed, what she practised overnight, and what's queued for training (${stats.total} examples ready).</p><h3>Morning spot-check</h3>`;
      if (!nights.length) main.insertAdjacentHTML("beforeend", `<p class="empty-note">No overnight batches waiting.</p>`);
      for (const n of nights) {
        const c = card(`<div class="top"><span class="t">${esc(n.batch)}</span><span class="pill">${n.n} examples</span></div>
          <p class="quote">${n.stats ? `${n.stats.questions} questions · ${n.stats.rewritten} rewritten · ${n.stats.kept} kept` : ""} — untick anything that isn't better.</p>`);
        for (const ex of n.spot_check) {
          c.insertAdjacentHTML("beforeend", `<label class="toggle" style="margin-top:10px"><input type="checkbox" checked data-id="${ex.id}"> <b>${esc(ex.question.slice(0, 160))}</b></label>
            <div class="ex"><div><span class="pill">before</span>${md(ex.before)}</div><div><span class="pill on">after</span>${md(ex.after)}</div></div>`);
        }
        const acts = document.createElement("div");
        acts.className = "acts";
        acts.style.marginTop = "10px";
        acts.appendChild(btn("Reject whole night", () => api(`/api/overnight/${n.batch}`, { method: "POST", body: { decision: "reject" } })));
        acts.appendChild(btn("Approve (minus unticked)", () => {
          const excl = [...c.querySelectorAll("input[type=checkbox]:not(:checked)")].map((x) => +x.dataset.id);
          return api(`/api/overnight/${n.batch}`, { method: "POST", body: { decision: "approve", exclude_ids: excl } });
        }, "btn-primary"));
        c.appendChild(acts);
        main.appendChild(c);
      }
      main.insertAdjacentHTML("beforeend", `<h3>Corrections</h3>`);
      if (!corr.length) main.insertAdjacentHTML("beforeend", `<p class="empty-note">None yet.</p>`);
      for (const c of corr) {
        const x = card(`<div class="top"><span class="t">“${esc(c.question.slice(0, 140))}”</span><span class="pill">${c.status}</span></div>
          ${c.diagnosis && c.diagnosis.problem ? `<p class="quote">${esc(c.diagnosis.problem)}</p>` : ""}
          <div class="ex"><div><span class="pill">before</span>${md(c.original)}</div><div><span class="pill on">${c.status === "accepted" ? "now" : "proposed"}</span>${md(c.final_answer || c.proposal || "…")}</div></div>`,
          ...(c.status === "proposed" ? [
            btn("Reject", () => api(`/api/corrections/${c.id}/reject`, { method: "POST" })),
            btn("Edit & accept", async () => { const t = prompt("Edit the answer", c.proposal); if (t) await api(`/api/corrections/${c.id}/accept`, { method: "POST", body: { text: t } }); }),
            btn("Accept", () => api(`/api/corrections/${c.id}/accept`, { method: "POST", body: {} }), "btn-primary")] : []));
        main.appendChild(x);
      }
    },

    async versions() {
      const v = await api("/api/versions");
      main.innerHTML = `<h2>Versions</h2><p class="lead">Base model <b>${esc(v.model)}</b>. Active adapter: <b>${esc(v.active || "none (plain base model)")}</b>. A version is promoted only if it has no regressions and wins your blind comparison.</p>`;
      const ab = v.ab;
      main.appendChild(card(`<div class="top"><span class="t">Blind comparison</span><span class="pill">${ab.decided} rated · ${ab.pending} waiting</span></div>
        <p class="quote">new won ${ab.new_wins} · old won ${ab.old_wins} · ties ${ab.ties}</p>`,
        ...(ab.pending ? [btn("Compare answers →", async () => { location.href = "/ab"; }, "btn-primary")] : [])));
      main.insertAdjacentHTML("beforeend", `<h3>Adapters</h3>`);
      if (!v.versions.length) main.insertAdjacentHTML("beforeend", `<p class="empty-note">No fine-tuned versions of this model yet.</p>`);
      for (const a of v.versions) {
        const l = a.losses || {};
        main.appendChild(card(`<div class="top"><span class="t">${esc(a.version)}</span>${a.version === v.active ? `<span class="pill on">active</span>` : ""}</div>
          <p class="quote">${a.created || ""} · train loss ${l.train_first || "?"} → ${l.train_last || "?"} · ${a.dataset ? JSON.stringify(a.dataset.by_source) : ""}</p>`,
          ...(a.version !== v.active ? [btn("Promote", () => api(`/api/versions/${a.version}/promote`, { method: "POST" }))] : [])));
      }
      if (v.active) main.appendChild(btn("↩ Roll back to the previous version", () => api("/api/versions/rollback", { method: "POST" })));
      main.insertAdjacentHTML("beforeend", `<h3>Evaluation runs</h3>`);
      if (!v.evals.length) { main.insertAdjacentHTML("beforeend", `<p class="empty-note">No evaluation runs yet (make eval).</p>`); return; }
      const sets = [...new Set(v.evals.flatMap((e) => Object.keys(e.summary)))];
      main.insertAdjacentHTML("beforeend", `<table class="t"><thead><tr><th>Run</th><th>Date</th>${sets.map((s) => `<th>${esc(s)}</th>`).join("")}</tr></thead><tbody>
        ${v.evals.map((e) => `<tr><td>${esc(e.label)}</td><td>${esc(e.date.slice(0, 16).replace("T", " "))}</td>${sets.map((s) => {
          const x = e.summary[s]; return `<td>${x && x.accuracy != null ? Math.round(x.accuracy * 100) + "%" : "–"}</td>`; }).join("")}</tr>`).join("")}</tbody></table>`);
    },

    async actions() {
      const acts = await api("/api/actions");
      main.innerHTML = `<h2>Actions</h2><p class="lead">Everything Sylvie did in your apps. Undo where possible.</p>`;
      if (!acts.length) { main.insertAdjacentHTML("beforeend", `<p class="empty-note">No actions yet.</p>`); return; }
      const t = document.createElement("table");
      t.className = "t";
      t.innerHTML = `<thead><tr><th>When</th><th>Action</th><th>Details</th><th>Status</th><th></th></tr></thead><tbody></tbody>`;
      for (const a of acts) {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${esc(a.created_at.slice(0, 16).replace("T", " "))}</td><td>${esc(a.name)}</td><td>${esc((a.summary || JSON.stringify(a.args)).slice(0, 140))}${a.result ? `<div class="quote">${esc(a.result.slice(0, 140))}</div>` : ""}</td><td><span class="pill">${a.status}</span></td><td></td>`;
        if (a.status === "done" && a.undo) tr.lastChild.appendChild(btn("Undo", () => api(`/api/actions/${a.id}/undo`, { method: "POST" })));
        t.tBodies[0].appendChild(tr);
      }
      main.appendChild(t);
    },

    async privacy() {
      const [d, devices] = await Promise.all([api("/api/dashboard"), api("/api/devices")]);
      const p = d.privacy;
      main.innerHTML = `<h2>Privacy</h2><p class="lead">Sylvie runs on this Mac. Conversations, memory and models never leave it.</p>
        <div class="grid">
          <div class="stat"><span>Server</span><b style="font-size:14px">${esc(p.server)}</b></div>
          <div class="stat"><span>Audio recordings kept</span><b>${p.audio_kept ? "yes" : "none"}</b></div>
          <div class="stat"><span>Sylvie's data</span><b>${d.storage_gb.sylvie_data} GB</b></div>
          <div class="stat"><span>Models (cache)</span><b>${d.storage_gb.models} GB</b></div>
        </div><h3>Switches</h3>`;
      main.appendChild(card(`<div class="top"><span class="t">Web search (the only thing that sends anything out: generic queries, no personal details)</span>
        <span class="pill ${p.web_search ? "on" : "off"}">${p.web_search ? "on" : "off"}</span></div>`,
        btn(p.web_search ? "Turn off" : "Turn on", () => api("/api/settings/search", { method: "POST", body: { enabled: !p.web_search } }))));
      main.appendChild(card(`<div class="top"><span class="t">Microphone</span><span class="pill">${p.voice ? "controlled by the menu bar app (mute there)" : "voice off"}</span></div>`));
      main.insertAdjacentHTML("beforeend", `<h3>Paired devices</h3>`);
      if (!devices.length) main.insertAdjacentHTML("beforeend", `<p class="empty-note">No devices paired (see docs/iphone.md).</p>`);
      for (const dv of devices) {
        main.appendChild(card(`<div class="top"><span class="t">${esc(dv.name)}</span><span class="pill ${dv.revoked_at ? "off" : "on"}">${dv.revoked_at ? "revoked" : "paired"}</span></div>
          <p class="quote">paired ${dv.created_at.slice(0, 10)} · last seen ${dv.last_seen ? dv.last_seen.slice(0, 16).replace("T", " ") : "never"}</p>`,
          ...(dv.revoked_at ? [] : [btn("Revoke", () => api(`/api/devices/${dv.id}`, { method: "DELETE" }))])));
      }
    },

    async speed() {
      const rows = await api("/api/traces");
      main.innerHTML = `<h2>Speed</h2><p class="lead">Time to the first word of each answer. Targets: ≤ 2 s on the Mac, ≤ 3 s from the phone, ≤ 15 s with web search.</p>`;
      if (!rows.length) { main.insertAdjacentHTML("beforeend", `<p class="empty-note">No answers yet.</p>`); return; }
      const target = (r) => (r.trace.stages_ms && Object.keys(r.trace.stages_ms).some((k) => k === "tool:web_search")) ? 15000 : r.channel === "phone" ? 3000 : 2000;
      main.insertAdjacentHTML("beforeend", `<table class="t"><thead><tr><th>When</th><th>Answer</th><th>Mode</th><th>First word</th><th>Total</th><th>Prompt (cached)</th></tr></thead><tbody>
        ${rows.map((r) => {
          const ft = r.trace.marks_ms && r.trace.marks_ms.first_token;
          const llm = (r.trace.llm || [])[0] || {};
          const late = ft && ft > target(r);
          return `<tr><td>${esc(r.created_at.slice(5, 16).replace("T", " "))}</td><td>${esc(r.preview)}</td><td>${esc(r.mode || "")} · ${esc(r.channel || "")}</td>
            <td style="${late ? "color:var(--danger);font-weight:600" : ""}">${ft ? (ft / 1000).toFixed(2) + " s" : "–"}</td><td>${(r.trace.total_ms / 1000).toFixed(1)} s</td>
            <td>${llm.prompt ? `${llm.prompt} (${llm.cached})` : "–"}</td></tr>`; }).join("")}</tbody></table>`);
    },
  };

  let current = location.hash.slice(1) || "about";
  async function show(name) {
    current = name in sections ? name : "about";
    document.querySelectorAll(".dnav button").forEach((b) => b.classList.toggle("active", b.dataset.s === current));
    history.replaceState(null, "", "#" + current);
    try { await sections[current](); } catch (e) { main.innerHTML = `<p class="error">${esc(String(e.message || e))}</p>`; }
  }
  document.querySelectorAll(".dnav button").forEach((b) => (b.onclick = () => show(b.dataset.s)));
  addEventListener("hashchange", () => { const h = location.hash.slice(1); if (h && h !== current) show(h); });
  show(current);
})();
