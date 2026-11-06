// Sylvie's orb as a reusable element: SylvieOrb.create(size, {mini}) → element with .setState(s) / .setLevel(x).
(function () {
  const STATES = ["idle", "listening", "thinking", "speaking", "muted"];

  function create(size, opts = {}) {
    const o = document.createElement("div");
    o.className = "orb idle" + (opts.mini ? " mini" : "");
    o.style.setProperty("--orb-size", size + "px");
    o.setAttribute("role", "img");
    o.setAttribute("aria-label", "Sylvie");
    o.innerHTML = '<div class="glow"></div><div class="ring"></div><div class="blob b1"></div>' +
      '<div class="blob b2"></div><div class="blob b3"></div><div class="core-level"><div class="core"></div></div>';
    o.setState = (s) => {
      if (!STATES.includes(s)) s = "idle";
      STATES.forEach((x) => o.classList.toggle(x, x === s));
      o.setAttribute("aria-label", "Sylvie, " + s);
    };
    o.setLevel = (x) => o.style.setProperty("--level", Math.max(0, Math.min(1, x || 0)).toFixed(3));
    return o;
  }

  // Replace every <div data-orb="44" data-orb-mini> placeholder with an orb.
  function mountAll(root = document) {
    root.querySelectorAll("[data-orb]").forEach((ph) => {
      const orb = create(+ph.dataset.orb || 44, { mini: ph.hasAttribute("data-orb-mini") });
      if (ph.id) orb.id = ph.id;
      if (ph.dataset.state) orb.setState(ph.dataset.state);
      ph.replaceWith(orb);
    });
  }

  window.SylvieOrb = { create, mountAll, STATES };
})();
