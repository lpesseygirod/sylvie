// Voice mode: the orb screen. Two drivers:
//  - in the Mac app (WKWebView): the app owns the microphone, wake word, echo cancellation and
//    Kokoro playback; this page just shows its state and events and sends button presses back;
//  - in a browser: this page records with getUserMedia, finds the end of speech, sends the audio to
//    /api/voice/turn and plays each sentence from /api/voice/speak (system voice as a fallback).
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const icon = window.icon;
  const params = new URLSearchParams(location.search);
  const native = window.webkit?.messageHandlers?.sylvie;
  let cid = params.get("c") || null;

  $("#v-mute").innerHTML = icon("mic", "i-20");
  $("#v-stop").innerHTML = icon("stop", "i-20");
  $("#v-chat").innerHTML = icon("chat", "i-20");
  window.SylvieOrb.mountAll();
  const orb = $("#v-orb");
  for (let i = 0; i < 24; i++) {
    const b = document.createElement("span");
    b.style.animationDelay = ((i * 0.13) % 0.9).toFixed(2) + "s";
    b.style.animationDuration = (0.6 + (i % 5) * 0.12).toFixed(2) + "s";
    $("#v-wave").appendChild(b);
  }

  // ---------- view ----------
  const LABELS = { idle: "Tap to talk", listening: "Listening", thinking: "Thinking", speaking: "Speaking", muted: "Microphone muted", offline: "Can't reach Sylvie" };
  let state = "idle";
  function setState(s, label) {
    state = s;
    orb.setState(s === "offline" ? "muted" : s);
    $("#v-label").textContent = label || LABELS[s] || s;
    $("#v-wave").hidden = s !== "speaking";
    $("#v-said").classList.toggle("live", s === "listening");
    $("#v-stop").disabled = !(s === "thinking" || s === "speaking" || s === "listening");
    $("#v-mute").setAttribute("aria-pressed", s === "muted" ? "true" : "false");
    $("#v-mute").innerHTML = icon(s === "muted" ? "micOff" : "mic", "i-20");
    $("#v-mute").setAttribute("aria-label", s === "muted" ? "Unmute microphone" : "Mute microphone");
    if (s !== "speaking") orb.setLevel(0);
    // Whisper writes down what Lea said once she stops, so while listening there's a caret, not live words.
    if (s === "listening" && !$("#v-answer").textContent) $("#v-answer").textContent = "Go ahead, I’m listening.";
  }
  function setConversation(id) {
    if (!id || id === cid) return;
    cid = id;
    history.replaceState(null, "", "/voice?c=" + encodeURIComponent(cid));
    $("#v-chat").href = "/?c=" + encodeURIComponent(cid);
  }
  $("#v-chat").href = cid ? "/?c=" + encodeURIComponent(cid) : "/";

  // The answer as plain text: documents are for the screen in chat, markdown marks are dropped.
  let answerRaw = "";
  function showAnswer(text) {
    answerRaw = text;
    let t = text.replace(/<doc\b(?:\s+title\s*=\s*"([^"]*)")?[^>]*>[\s\S]*?(<\/doc>|$)/gi, (_, title) => ` [${title || "Document"}: open chat to see it] `);
    t = t.replace(/```[\s\S]*?(```|$)/g, " ").replace(/[*_`#>|]/g, "").replace(/\[(\d{1,2})\]/g, "").replace(/\s+/g, " ").trim();
    $("#v-answer").textContent = t.length > 600 ? t.slice(0, 600).replace(/\s\S*$/, "") + "… (more in the chat)" : t;
  }
  function showSaid(text) {
    $("#v-said").textContent = text ? `“${text}”` : "";
    if (text && $("#v-answer").textContent === "Go ahead, I’m listening.") $("#v-answer").textContent = "";
  }

  async function health() {
    try {
      const r = await fetch("/api/health");
      if (r.status === 401) { location.href = "/"; return; }
      const h = await r.json();
      $("#v-dot").className = "status-dot" + (h.ready ? " ok" : h.error ? " err" : "");
      $("#v-where").textContent = h.ready ? "On your Mac · private" : h.error ? "Sylvie couldn't start" : "Starting on your Mac…";
      if (!h.ready && !h.error) setTimeout(health, 1500);
    } catch (_) {
      $("#v-dot").className = "status-dot err";
      $("#v-where").textContent = "Can't reach your Mac";
      setState("offline");
    }
  }
  health();

  // ---------- Mac app driver ----------
  if (native) {
    window.sylvieVoice = {
      state(s, detail) {
        if (s === "listening") showSaid("");  // the last answer stays readable until Lea says something new
        setState(s === "starting" ? "thinking" : s, s === "offline" ? detail : undefined);
      },
      level(x) { orb.setLevel(x); },
      event(ev) {
        if (ev.type === "transcript") { showSaid(ev.text); showAnswer(""); }
        else if (ev.type === "meta") setConversation(ev.conversation_id);
        else if (ev.type === "text") showAnswer(answerRaw + ev.text);
        else if (ev.type === "status" && ev.text && !answerRaw) $("#v-answer").textContent = ev.text;
        else if (ev.type === "error") $("#v-answer").textContent = ev.text;
      },
    };
    const post = (msg) => native.postMessage(msg);
    post({ type: "voiceMode", active: true, conversation_id: cid });
    addEventListener("pagehide", () => post({ type: "voiceMode", active: false }));
    $("#v-orb-btn").onclick = () => post({ type: "voiceListen" });
    $("#v-stop").onclick = () => post({ type: "voiceStop" });
    $("#v-mute").onclick = () => post({ type: "voiceMute" });
    addEventListener("keydown", (e) => {
      if (e.key === " " && e.target === document.body) { e.preventDefault(); post({ type: "voiceListen" }); }
      if (e.key === "Escape") post({ type: "voiceStop" });
    });
    setState("idle");
    return;
  }

  // ---------- browser driver ----------
  $("#v-hint").textContent = "Tap the orb to talk · Stop interrupts · Esc to stop";
  let ctx = null, stream = null, proc = null, analyser = null;
  let muted = false, collecting = false, chunks = [], heard = false, silentMs = 0, totalMs = 0, speechChunks = 0;
  let floor = 0.01, turnAbort = null, autoListen = true;
  const queue = [];
  let playing = null, levelTimer = null;

  async function ensureAudio() {
    if (ctx && stream) { if (ctx.state === "suspended") await ctx.resume(); return true; }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch (e) {
      setState("muted", "Allow the microphone to talk");
      $("#v-answer").textContent = "The browser didn't give me the microphone. Allow it in the address bar, then tap the orb.";
      return false;
    }
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === "suspended") await ctx.resume().catch(() => {});
    const src = ctx.createMediaStreamSource(stream);
    proc = ctx.createScriptProcessor(4096, 1, 1);
    const sink = ctx.createGain();
    sink.gain.value = 0;
    src.connect(proc); proc.connect(sink); sink.connect(ctx.destination);
    proc.onaudioprocess = (e) => onAudio(e.inputBuffer.getChannelData(0));
    analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.connect(ctx.destination);
    return true;
  }

  function onAudio(data) {
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    const rms = Math.sqrt(sum / data.length);
    const ms = (data.length / ctx.sampleRate) * 1000;
    if (!collecting) { floor = floor * 0.95 + Math.min(rms, 0.05) * 0.05; return; }
    chunks.push(new Float32Array(data));
    orb.setLevel(Math.min(1, rms * 12));
    totalMs += ms;
    const speech = rms > Math.max(0.015, floor * 3.5);
    if (speech) { speechChunks++; silentMs = 0; if (speechChunks >= 2) heard = true; }
    else { speechChunks = 0; if (!heard) floor = floor * 0.9 + rms * 0.1; silentMs += ms; }
    if (heard && silentMs > 900) endUtterance();
    else if (!heard && totalMs > 8000) { collecting = false; setState("idle", "I didn't hear anything"); }
    else if (totalMs > 30000) endUtterance();
  }

  async function listen() {
    if (muted) return;
    if (!(await ensureAudio())) return;
    stopSpeaking();
    chunks = []; heard = false; silentMs = 0; totalMs = 0; speechChunks = 0;
    collecting = true;
    showSaid("");
    setState("listening");
  }

  function endUtterance() {
    collecting = false;
    const wav = encodeWav(chunks, ctx.sampleRate);
    chunks = [];
    runTurn(wav);
  }

  function encodeWav(parts, rate) {
    const len = parts.reduce((n, p) => n + p.length, 0);
    const all = new Float32Array(len);
    let o = 0;
    for (const p of parts) { all.set(p, o); o += p.length; }
    const ratio = rate / 16000, n = Math.floor(len / ratio);
    const buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    const str = (off, s) => { for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i)); };
    str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE"); str(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 16000, true);
    v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) {
      const x = i * ratio, j = Math.floor(x), f = x - j;
      const s = (all[j] || 0) * (1 - f) + (all[j + 1] || 0) * f;
      v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, s)) * 32767, true);
    }
    return new Blob([buf], { type: "audio/wav" });
  }

  async function runTurn(wav) {
    setState("thinking");
    autoListen = true;
    turnAbort = new AbortController();
    let lang = "en";
    try {
      const q = cid ? `?conversation_id=${encodeURIComponent(cid)}` : "";
      const r = await fetch("/api/voice/turn" + q, { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav, signal: turnAbort.signal });
      if (!r.ok) throw new Error(await r.text());
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const line = buf.slice(0, i); buf = buf.slice(i + 2);
          if (!line.startsWith("data: ")) continue;
          const ev = JSON.parse(line.slice(6));
          if (ev.type === "transcript") {
            showSaid(ev.text); showAnswer(""); lang = ev.language || lang;
            if (!ev.text) { setState("idle", "I didn't catch that"); return; }
          } else if (ev.type === "meta") setConversation(ev.conversation_id);
          else if (ev.type === "text") showAnswer(answerRaw + ev.text);
          else if (ev.type === "status" && ev.text && !answerRaw) $("#v-answer").textContent = ev.text;
          else if (ev.type === "speak") enqueue(ev.text, ev.language || lang);
          else if (ev.type === "error") $("#v-answer").textContent = ev.text;
        }
      }
    } catch (e) {
      if (e.name !== "AbortError") { $("#v-answer").textContent = "Something went wrong: " + e.message; setState("idle"); }
      return;
    } finally { turnAbort = null; }
    if (!playing && !queue.length) afterAnswer();
  }

  // Sentences are fetched as soon as they arrive, so the next one is ready while one plays.
  function enqueue(text, language) {
    const item = { text, language, audio: fetchSpeech(text, language) };
    queue.push(item);
    if (!playing) playNext();
  }
  async function fetchSpeech(text, language) {
    try {
      const r = await fetch("/api/voice/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, language }) });
      if (!r.ok) return null;
      return await ctx.decodeAudioData(await r.arrayBuffer());
    } catch (_) { return null; }
  }
  async function playNext() {
    const item = queue.shift();
    if (!item) { playing = null; clearInterval(levelTimer); if (!turnAbort) afterAnswer(); return; }
    playing = item;
    setState("speaking");
    const audio = await item.audio;
    if (playing !== item) return;  // stopped meanwhile
    if (audio) {
      const src = ctx.createBufferSource();
      src.buffer = audio;
      src.connect(analyser);
      item.src = src;
      src.onended = () => { if (playing === item) playNext(); };
      src.start();
      const bins = new Uint8Array(analyser.fftSize);
      clearInterval(levelTimer);
      levelTimer = setInterval(() => {
        analyser.getByteTimeDomainData(bins);
        let s = 0;
        for (const b of bins) s += ((b - 128) / 128) ** 2;
        orb.setLevel(Math.min(1, Math.sqrt(s / bins.length) * 5));
      }, 50);
    } else if ("speechSynthesis" in window) {
      const u = new SpeechSynthesisUtterance(item.text);
      u.lang = { en: "en-US", es: "es-ES", fr: "fr-FR" }[item.language] || "en-US";
      u.onend = u.onerror = () => { if (playing === item) playNext(); };
      speechSynthesis.speak(u);
    } else playNext();
  }
  function stopSpeaking() {
    queue.length = 0;
    if (playing && playing.src) { try { playing.src.stop(); } catch (_) {} }
    playing = null;
    clearInterval(levelTimer);
    if ("speechSynthesis" in window) speechSynthesis.cancel();
  }
  function afterAnswer() {
    if (autoListen && !muted && state !== "idle") listen();  // keep the conversation going
    else if (!muted) setState("idle");
  }

  function stopAll() {
    autoListen = false;
    collecting = false;
    if (turnAbort) turnAbort.abort();
    if (cid) fetch(`/api/chat/${encodeURIComponent(cid)}/stop`, { method: "POST" }).catch(() => {});
    stopSpeaking();
    setState(muted ? "muted" : "idle");
  }

  function orbPressed() {
    if (muted) return;
    if (state === "listening") { if (heard) endUtterance(); else { collecting = false; setState("idle"); } return; }
    if (state === "thinking" || state === "speaking") stopAll();
    listen();
  }
  $("#v-orb-btn").onclick = orbPressed;
  $("#v-stop").onclick = stopAll;
  $("#v-mute").onclick = () => {
    muted = !muted;
    if (stream) stream.getAudioTracks().forEach((t) => (t.enabled = !muted));
    if (muted) { collecting = false; setState("muted"); } else setState("idle");
  };
  addEventListener("keydown", (e) => {
    if (e.key === " " && e.target === document.body) { e.preventDefault(); orbPressed(); }
    if (e.key === "Escape") stopAll();
  });

  setState("idle");
  listen();  // opening voice mode means "I want to talk"; the browser may ask for the microphone first
})();
