// Blind A/B: shows two answers without saying which version wrote them.
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = window.escapeHtml, md = window.renderMarkdown;
  try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch (_) {}

  async function next() {
    const r = await fetch("/api/ab/next").then((x) => x.json());
    const s = r.summary;
    $("#progress").textContent = `${s.decided} done · ${s.pending} to go`;
    const box = $("#pair");
    if (!r.pair) {
      box.innerHTML = `<div class="done"><h2>All done — thank you!</h2><p>New version won ${s.new_wins}, old version won ${s.old_wins}, ties ${s.ties}.</p></div>`;
      return;
    }
    const p = r.pair;
    box.innerHTML = `<div class="prompt">${esc(p.prompt)}</div>
      <div class="cols"><div class="col"><h3>Answer A</h3><div class="bubble">${md(p.left)}</div></div>
      <div class="col"><h3>Answer B</h3><div class="bubble">${md(p.right)}</div></div></div>
      <div class="choices"><button class="btn-primary" data-c="left">A is better</button>
      <button class="btn-ghost" data-c="tie">About the same</button>
      <button class="btn-primary" data-c="right">B is better</button></div>`;
    box.querySelectorAll("button").forEach((b) => (b.onclick = async () => {
      await fetch(`/api/ab/${p.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ choice: b.dataset.c }) });
      next();
    }));
  }
  addEventListener("keydown", (e) => {
    const map = { ArrowLeft: "left", ArrowRight: "right", ArrowDown: "tie" };
    const b = map[e.key] && document.querySelector(`[data-c=${map[e.key]}]`);
    if (b) b.click();
  });
  next();
})();
