"""End-to-end UI test: the real web UI in headless Chrome, against the UI dev server (fake model,
throwaway database, fake app control — never Lea's data or apps).

    make ui-test             # chat, work panel, voice mode, wake panel, dashboard
    make ui-test VOICE=1     # also one real spoken round trip (loads Whisper + Kokoro, ~2.5 GB)

Needs Google Chrome. Uses its own throwaway Chrome profile.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
PORT = 8767
BASE = f"http://127.0.0.1:{PORT}"
CDP_PORT = 9334
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# Stands in for the Mac app's WKWebView bridge and records what the page sends to it.
NATIVE_STUB = "window.__posted = []; window.webkit = {messageHandlers: {sylvie: {postMessage: (m) => window.__posted.push(m)}}};"

results: list[tuple[bool, str, str]] = []


def check(name: str, ok, detail="") -> bool:
    results.append((bool(ok), name, "" if ok else str(detail)[:300]))
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f"  → {str(detail)[:300]}"), flush=True)
    return bool(ok)


class Tab:
    """Minimal Chrome DevTools Protocol client: commands, console errors, auto-answered dialogs."""

    def __init__(self, ws):
        self.ws, self.n, self.pending = ws, 0, {}
        self.errors: list[str] = []
        self.dialogs: list[str] = []
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self.pending:
                self.pending.pop(msg["id"]).set_result(msg)
                continue
            method, p = msg.get("method"), msg.get("params", {})
            if method == "Runtime.exceptionThrown":
                self.errors.append(p["exceptionDetails"].get("exception", {}).get("description", "exception"))
            elif method == "Runtime.consoleAPICalled" and p.get("type") == "error":
                self.errors.append(
                    " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))
                )
            elif (
                method == "Log.entryAdded"
                and p["entry"]["level"] == "error"
                and "favicon" not in p["entry"].get("url", "")
            ):
                self.errors.append(p["entry"]["text"])
            elif method == "Page.javascriptDialogOpening":
                self.dialogs.append(p["message"])
                asyncio.create_task(
                    self.call("Page.handleJavaScriptDialog", accept=True, promptText="Renamed by test")
                )

    async def call(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        msg = await asyncio.wait_for(fut, 60)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result", {})

    async def goto(self, url, settle=1.2):
        self.errors.clear()
        await self.call("Page.navigate", url=url)
        await asyncio.sleep(settle)

    async def js(self, expr):
        """Runs an async JS body (may use await and return)."""
        r = await self.call("Runtime.evaluate", expression=f"(async () => {{ {expr} }})()",
                            awaitPromise=True, returnByValue=True)  # fmt: skip
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", "js error"))
        return r.get("result", {}).get("value")

    async def size(self, w, h):
        await self.call(
            "Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600
        )

    async def shot(self, path: Path):
        path.write_bytes(base64.b64decode((await self.call("Page.captureScreenshot", format="png"))["data"]))


HELPERS = """
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const $ = (s) => document.querySelector(s), $$ = (s) => [...document.querySelectorAll(s)];
const idle = async () => { for (let i = 0; i < 120; i++) { await wait(100); if (!$('#send').classList.contains('stop')) return; } };
const say = async (t) => { $('#input').value = t; $('#composer').requestSubmit(); await wait(150); await idle(); };
const last = () => $$('.msg.assistant').pop();
"""


async def test_chat(t: Tab, shots: Path):
    print("Chat workspace", flush=True)
    await t.size(1440, 900)
    await t.goto(f"{BASE}/auth?t=dev", settle=2)
    r = await t.js(
        HELPERS
        + """
      for (let i = 0; i < 40 && !$('#status-line').textContent.includes('Running'); i++) await wait(100);
      await document.fonts.ready;
      return { status: $('#status-line').textContent.trim(), chip: $('#version-chip').textContent, chipShown: !$('#version-chip').hidden,
               inter: document.fonts.check('16px Inter'), grotesk: document.fonts.check('600 16px "Space Grotesk"'),
               orb: !!$('.empty .orb'), brandOrb: !!$('.brand .orb'), emoji: /[\\u{1F300}-\\u{1FAFF}]/u.test(document.body.innerText),
               search: $('#search-toggle').textContent, modes: $$('#mode option').length };"""
    )
    check("status line says Running on this Mac", r["status"] == "Running on this Mac", r["status"])
    check(
        "version chip shows the model version",
        r["chipShown"] and r["chip"].startswith("Sylvie ") and r["chip"].endswith("· local"),
        r,
    )
    check("fonts are bundled and load (Inter, Space Grotesk)", r["inter"] and r["grotesk"], r)
    check("orb in the logo and the empty state", r["orb"] and r["brandOrb"], r)
    check("no emoji in the interface", not r["emoji"])
    check(
        "composer has web search switch and answer modes",
        r["search"] == "Web search on" and r["modes"] == 3,
        r,
    )

    r = await t.js(
        HELPERS
        + """
      await say('Can you make me a quick cheat sheet for time value of money?');
      return { cards: $$('.doc-card').length, panel: !$('#work-panel').hidden, title: $('#wp-title').textContent,
               preview: $('#wp-preview').innerText, actions: !last().querySelector('.actions').hidden,
               convs: $$('.conv').length, title2: $('#conv-title').textContent, voice: $('#voice-btn').getAttribute('href') };"""
    )
    check(
        "a document becomes a card and opens the work panel",
        r["cards"] == 1 and r["panel"] and r["title"] == "TVM cheat sheet",
        r,
    )
    check(
        "panel preview renders the document",
        "Perpetuity" in r["preview"] and "<doc" not in r["preview"],
        r["preview"][:120],
    )
    check(
        "answer gets feedback actions; conversation appears in the sidebar",
        r["actions"] and r["convs"] == 1,
        r,
    )
    check("Voice mode button keeps the conversation", r["voice"].startswith("/voice?c="), r["voice"])
    await t.shot(shots / "chat-panel.png")

    await t.call("Emulation.setFocusEmulationEnabled", enabled=True)
    await t.call(
        "Browser.grantPermissions", permissions=["clipboardReadWrite", "clipboardSanitizedWrite"], origin=BASE
    )
    r = await t.js(
        HELPERS
        + """
      $('#wp-tab-edit').click(); await wait(50);
      const ta = $('#wp-edit'); ta.value += '\\n\\nAdded by the UI test.'; ta.dispatchEvent(new Event('input'));
      $('#wp-tab-preview').click(); await wait(50);
      const edited = $('#wp-preview').innerText.includes('Added by the UI test');
      $('#wp-copy').click(); await wait(300); const copied = $('#wp-note').textContent;
      const clip = await navigator.clipboard.readText().catch(() => '');
      $('#wp-save').click(); for (let i = 0; i < 30 && !$('#wp-note').textContent.startsWith('Saved'); i++) await wait(100);
      const saved = $('#wp-note').textContent;
      $('#wp-close').click(); await wait(50);
      const closed = $('#work-panel').hidden;
      last().querySelector('.doc-card').click(); await wait(50);
      const reopenedKeepsEdit = $('#wp-preview').innerText.includes('Added by the UI test');
      $('#wp-close').click();
      return { edited, copied, clip: clip.includes('Added by the UI test'), saved, closed, reopenedKeepsEdit };"""
    )
    check("Edit tab changes the document; Preview shows it", r["edited"], r)
    check("Copy puts the document on the clipboard", r["clip"] and r["copied"] == "Copied", r)
    check("Save to notes saves it", r["saved"] == "Saved to notes", r)
    check("close and reopen the panel keeps the edit", r["closed"] and r["reopenedKeepsEdit"], r)

    r = await t.js(
        HELPERS
        + """
      await say('search the web for the NPV formula');
      const m = last();
      return { note: m.querySelector('.notes').innerText, table: !!m.querySelector('table'), code: !!m.querySelector('.code .copy-code'),
               cite: !!m.querySelector('.cite') };"""
    )
    check("tool use shows as an activity note", "Searched the web: NPV formula" in r["note"], r)
    check("markdown: table, code block with Copy, citation", r["table"] and r["code"] and r["cite"], r)

    r = await t.js(
        HELPERS
        + """
      await say('send an email to my professor about Monday');
      const card = last().querySelector('.confirm-card');
      const head = card && card.querySelector('.cc-head').innerText, body = card && card.querySelector('.cc-body').innerText;
      const buttons = card ? [...card.querySelectorAll('button')].map((b) => b.textContent) : [];
      const before = $$('.msg').length;
      card.querySelector('[data-v=edit]').click(); await wait(300);
      const editKeepsQuiet = $$('.msg').length === before && document.activeElement === $('#input') && $('#input').placeholder.includes('change');
      card.querySelector('[data-v=yes]').click(); await wait(200); await idle();
      return { head, body, buttons, editKeepsQuiet, after: last().querySelector('.notes').innerText,
               orderOk: !!last().previousElementSibling };"""
    )
    check(
        "Send? card shows app + recipient and the exact content",
        r["head"] == "Mail · to prof@uni.es" and "Can we meet Monday at 10?" in r["body"],
        r,
    )
    check("card has Send / Edit / Cancel", r["buttons"] == ["Send", "Edit", "Cancel"], r["buttons"])
    check("Edit sends nothing and asks what to change", r["editKeepsQuiet"], r)
    check("Send runs the action (fake Mail) and reports it", "Done" in r["after"], r["after"])

    r = await t.js(
        HELPERS
        + """
      const m = last();
      m.querySelector('[data-a=up]').click(); await wait(300);
      const up = m.querySelector('[data-a=up]').getAttribute('aria-pressed');
      m.querySelector('[data-a=down]').click(); await wait(300);
      const why = m.querySelector('.why-box input'); why.value = 'too long'; m.querySelector('.why-box button').click(); await wait(400);
      const thanks = m.querySelector('.why-box').innerText;
      m.querySelector('[data-a=edit]').click(); await wait(50);
      m.querySelector('.edit-box textarea').value = 'Better answer.'; m.querySelector('.edit-box .btn-primary').click(); await wait(400);
      return { up, thanks, edited: !!m.querySelector('.edited'), bubble: m.querySelector('.bubble').innerText };"""
    )
    check("thumbs up is saved", r["up"] == "true", r)
    check("thumbs down asks why and sends it to the teacher", r["thanks"].startswith("Thanks"), r)
    check("Edit to what I wanted saves the edit", r["edited"], r)

    r = await t.js(
        HELPERS
        + """
      $('#open-memory').click(); await wait(500);
      const prefs = $('#memory-body').innerText;
      $('[data-tab=facts]').click(); await wait(400); const facts = $('#memory-body').innerText;
      $('[data-tab=interests]').click(); await wait(400); const interests = $('#memory-body').innerText;
      $('[data-tab=traits]').click(); await wait(400); const traits = $('#memory-body').innerText;
      $('[data-tab=corrections]').click(); await wait(400); const corr = $('#memory-body').innerText;
      $('#memory-dialog').close();
      $('#search-toggle').click(); await wait(400); const off = $('#search-toggle').textContent;
      const info = await (await fetch('/api/info')).json();
      $('#search-toggle').click(); await wait(400);
      return { prefs, facts, interests, traits, corr, off, infoOff: info.web_search === false, on: $('#search-toggle').textContent };"""
    )
    check("About Lea: preferences, facts, interests, style, corrections", "Keep answers short" in r["prefs"] and "Computer Science" in r["facts"]
          and "Formula 1" in r["interests"] and "Mixes Spanish" in r["traits"] and "You asked" in r["corr"], r)  # fmt: skip
    check(
        "web search switch turns search off and on",
        r["off"] == "Web search off" and r["infoOff"] and r["on"] == "Web search on",
        r,
    )

    cid = await t.js(
        "return new URLSearchParams($('#voice-btn').search).get('c')".replace("$(", "document.querySelector(")
    )
    tmp = Path(tempfile.mkdtemp()) / "notes.txt"
    tmp.write_text("Exam on 12 January. Bring a calculator.")
    doc = await t.call("DOM.getDocument")
    node = await t.call("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#file-input")
    await t.call("DOM.setFileInputFiles", files=[str(tmp)], nodeId=node["nodeId"])
    r = await t.js(
        HELPERS
        + """
      for (let i = 0; i < 30 && !$('.att'); i++) await wait(100);
      const chip = $('.att') && $('.att').innerText;
      await say('what does my file say?');
      const user = $$('.msg.user').pop().innerText;
      return { chip, user, chipsLeft: $$('.att').length };"""
    )
    check(
        "attach a file: chip, then shown on the message",
        r["chip"] and "notes.txt" in r["chip"] and "notes.txt" in r["user"] and r["chipsLeft"] == 0,
        r,
    )

    await t.goto(f"{BASE}/?c={cid}", settle=2)
    r = await t.js(
        HELPERS
        + """
      const msgs = $$('#messages .msg').length, title = $('#conv-title').textContent;
      $('.conv.active [aria-label=Rename]').click(); await wait(100);
      const inp = $('.conv input'); inp.value = 'Renamed in the test'; inp.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'})); await wait(500);
      const renamed = $$('.conv .t').map((e) => e.textContent);
      $('#new-chat').click(); await wait(100);
      return { msgs, title, renamed, empty: !!$('#empty'), newTitle: $('#conv-title').textContent };"""
    )
    check("reopening a conversation shows its history", r["msgs"] >= 8, r)
    check("rename a conversation", "Renamed in the test" in r["renamed"], r["renamed"])
    check("New chat clears the screen", r["empty"] and r["newTitle"] == "New chat", r)

    r = await t.js(
        HELPERS
        + """
      await say('hello');  // a throwaway conversation to delete
      const before = $$('.conv').length;
      $('.conv.active [aria-label=Delete]').click(); await wait(800);
      return { before, after: $$('.conv').length };"""
    )
    check(
        "delete a conversation (with confirm dialog)",
        r["after"] == r["before"] - 1 and t.dialogs,
        (r, t.dialogs),
    )
    check("chat page: no console errors", not t.errors, t.errors)

    await t.size(390, 844)
    await t.goto(f"{BASE}/?c={cid}&doc=1", settle=2)
    r = await t.js(
        HELPERS
        + """
      const sideHidden = getComputedStyle($('#sidebar')).display === 'none';
      const panel = $('#work-panel').getBoundingClientRect();
      $('#wp-close').click(); $('#toggle-sidebar').click(); await wait(100);
      const sideShown = getComputedStyle($('#sidebar')).display !== 'none';
      return { sideHidden, sideShown, panelFull: panel.width >= 380, noHScroll: document.documentElement.scrollWidth <= innerWidth };"""
    )
    check("phone width: sidebar becomes a drawer", r["sideHidden"] and r["sideShown"], r)
    check("phone width: work panel is full screen, no sideways scroll", r["panelFull"] and r["noHScroll"], r)
    await t.size(1440, 900)
    return cid


async def test_voice_page(t: Tab, cid: str, shots: Path):
    print("Voice mode (Mac app bridge)", flush=True)
    stub = await t.call("Page.addScriptToEvaluateOnNewDocument", source=NATIVE_STUB)
    await t.goto(f"{BASE}/voice?c={cid}", settle=1.5)
    r = await t.js(
        HELPERS
        + """
      const st = () => ({ label: $('#v-label').textContent, orb: $('#v-orb').className, wave: !$('#v-wave').hidden,
                          stop: $('#v-stop').disabled, caret: $('#v-said').classList.contains('live') });
      const out = { posted0: JSON.parse(JSON.stringify(window.__posted)) };
      sylvieVoice.state('listening'); await wait(50); out.listening = st();
      sylvieVoice.event({type: 'transcript', text: 'What do I have tomorrow?'}); sylvieVoice.state('thinking'); await wait(50);
      out.thinking = st(); out.said = $('#v-said').textContent;
      sylvieVoice.state('speaking');
      sylvieVoice.event({type: 'text', text: 'Two classes. <doc title="Plan">secret table</doc> Have fun.'}); await wait(50);
      out.speaking = st(); out.answer = $('#v-answer').textContent;
      sylvieVoice.level(0.8); out.level = $('#v-orb').style.getPropertyValue('--level');
      sylvieVoice.state('muted'); await wait(50); out.muted = st(); out.mutePressed = $('#v-mute').getAttribute('aria-pressed');
      sylvieVoice.state('idle'); window.__posted.length = 0;
      $('#v-orb-btn').click(); $('#v-mute').click(); sylvieVoice.state('speaking'); $('#v-stop').click();
      out.posted = window.__posted.map((m) => m.type);
      out.chat = $('#v-chat').getAttribute('href');
      return out;"""
    )
    check("voice mode tells the app it's open, with the conversation",
          r["posted0"][:1] == [{"type": "voiceMode", "active": True, "conversation_id": cid}], r["posted0"])  # fmt: skip
    check(
        "listening: label, orb state, caret",
        r["listening"]["label"] == "Listening"
        and "listening" in r["listening"]["orb"]
        and r["listening"]["caret"],
        r["listening"],
    )
    check(
        "thinking: label, orb state, transcript in quotes",
        r["thinking"]["label"] == "Thinking"
        and "thinking" in r["thinking"]["orb"]
        and r["said"] == "“What do I have tomorrow?”",
        r,
    )
    check(
        "speaking: wave bars, answer text, documents not read out",
        r["speaking"]["wave"] and "secret table" not in r["answer"] and "Have fun." in r["answer"],
        r,
    )
    check("speaking: the orb follows the audio level", r["level"] == "0.800", r["level"])
    check(
        "muted: grey orb and mute button pressed",
        "muted" in r["muted"]["orb"] and r["mutePressed"] == "true",
        r["muted"],
    )
    check(
        "orb / mute / stop buttons talk to the app",
        r["posted"] == ["voiceListen", "voiceMute", "voiceStop"],
        r["posted"],
    )
    check("Open chat goes back to the same conversation", r["chat"] == f"/?c={cid}", r["chat"])
    await t.js(
        "sylvieVoice.state('speaking'); sylvieVoice.event({type:'transcript', text:'What do I have tomorrow?'}); sylvieVoice.event({type:'text', text:'Two classes, Corporate Finance and Macro.'}); return 1"
    )
    await asyncio.sleep(0.5)
    await t.shot(shots / "voice-speaking.png")
    check("voice page: no console errors", not t.errors, t.errors)
    await t.call("Page.removeScriptToEvaluateOnNewDocument", identifier=stub["identifier"])

    print("Voice mode (browser, no microphone)", flush=True)
    await t.goto(f"{BASE}/voice", settle=2)
    r = await t.js(
        HELPERS
        + "return { label: $('#v-label').textContent, orb: $('#v-orb').className, hint: $('#v-hint').textContent };"
    )
    check(
        "without a microphone it says so instead of failing",
        "microphone" in r["label"].lower() and "muted" in r["orb"],
        r,
    )


async def test_panel(t: Tab, shots: Path):
    print("Wake panel", flush=True)
    stub = await t.call("Page.addScriptToEvaluateOnNewDocument", source=NATIVE_STUB)
    await t.size(620, 420)
    await t.goto(f"{BASE}/?embed=panel", settle=1.5)
    r = await t.js(
        HELPERS
        + """
      const out = { app: getComputedStyle($('#app')).display, wake: getComputedStyle($('#wake')).display };
      sylvie.voiceState('listening'); await wait(50);
      out.listening = { orb: $('#wake-orb').className, ph: $('#wake-q').placeholder };
      sylvie.voiceEvent({type: 'transcript', text: 'email my professor'}); sylvie.voiceState('thinking');
      sylvie.voiceEvent({type: 'tool', name: 'send_email', args: {}});
      sylvie.voiceEvent({type: 'text', text: 'Here is the email. Want me to send it?'});
      sylvie.voiceEvent({type: 'action', status: 'pending', name: 'send_email', summary: 'x',
                         args: {to: 'prof@uni.es', subject: 'Meeting', body: 'Monday at 10 works.'}});
      sylvie.voiceEvent({type: 'done'}); await wait(300);
      out.q = $('#wake-q').value; out.card = !!$('#wake .confirm-card'); out.hint = $('#wake-hint').textContent;
      out.order = $('#wake .bubble').compareDocumentPosition($('#wake .confirm-card')) & Node.DOCUMENT_POSITION_FOLLOWING;
      out.posted = window.__posted.map((m) => m.type);
      out.height = Math.max(0, ...window.__posted.filter((m) => m.type === 'panelSize').map((m) => m.height));
      window.__posted.length = 0;
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
      $('#wake-open').click();
      out.posted2 = window.__posted.map((m) => m.type);
      return out;"""
    )
    check("panel mode shows only the wake card", r["app"] == "none" and r["wake"] == "flex", r)
    check(
        "listening: mini orb + 'Listening…'",
        "listening" in r["listening"]["orb"] and r["listening"]["ph"] == "Listening…",
        r["listening"],
    )
    check("spoken question shown in quotes", r["q"] == "“email my professor”", r["q"])
    check(
        "Send? card after the answer, with the 'say yes' hint",
        r["card"] and r["order"] and r["hint"].startswith("Say “yes” to send"),
        r,
    )
    check(
        "panel tells the app: confirmation waiting, its height",
        "pendingConfirm" in r["posted"] and r["height"] > 80,
        r,
    )
    check("Esc and Open chat reach the app", r["posted2"] == ["closePanel", "openChat"], r["posted2"])
    await t.shot(shots / "panel.png")

    await t.goto(f"{BASE}/?embed=panel", settle=1.5)
    r = await t.js(
        HELPERS
        + """
      sylvie.focusInput(); $('#wake-q').value = 'hello';
      $('#wake-q').dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
      for (let i = 0; i < 80 && !($('#wake .actions') && !$('#wake .actions').hidden); i++) await wait(100);
      return { answer: $('#wake .bubble').innerText.slice(0, 40), actions: !!$('#wake .actions') && !$('#wake .actions').hidden };"""
    )
    check("typing in the panel gets a streamed answer", r["answer"] and r["actions"], r)
    check("panel: no console errors", not t.errors, t.errors)
    await t.call("Page.removeScriptToEvaluateOnNewDocument", identifier=stub["identifier"])
    await t.size(1440, 900)


async def test_other_pages(t: Tab):
    print("Dashboard and blind comparison", flush=True)
    await t.goto(f"{BASE}/dashboard", settle=2)
    r = await t.js(
        HELPERS
        + "return { nav: $$('.dnav button').length, theme: getComputedStyle(document.body).backgroundColor };"
    )
    check("dashboard loads in the new colours", r["nav"] >= 5 and r["theme"] == "rgb(14, 15, 19)", r)
    check("dashboard: no console errors", not t.errors, t.errors)
    await t.goto(f"{BASE}/ab", settle=1.5)
    check("blind comparison page loads", not t.errors, t.errors)


async def test_real_voice(t: Tab):
    print("Voice mode (browser, real Whisper + Kokoro round trip)", flush=True)
    await t.goto(f"{BASE}/voice", settle=1.5)
    r = await t.js(
        HELPERS
        + """
      const ac = new AudioContext(); await ac.resume();
      const res = await fetch('/api/voice/speak', {method: 'POST', headers: {'Content-Type': 'application/json'},
                              body: JSON.stringify({text: 'What is the net present value of a project?', language: 'en'})});
      if (!res.ok) return { error: 'speak ' + res.status };
      const buf = await ac.decodeAudioData(await res.arrayBuffer());
      const dest = ac.createMediaStreamDestination();
      navigator.mediaDevices.getUserMedia = async () => dest.stream;
      $('#v-mute').click(); $('#v-mute').click(); $('#v-orb-btn').click(); await wait(700);
      const s = ac.createBufferSource(); s.buffer = buf; s.connect(dest); s.start();
      const seen = [];
      for (let i = 0; i < 240; i++) { await wait(250); const l = $('#v-label').textContent; if (seen[seen.length - 1] !== l) seen.push(l);
        if (seen.includes('Speaking') && l === 'Listening') break; }
      const said = $('#v-said').textContent; $('#v-stop').click();
      const cid = new URLSearchParams(location.search).get('c');
      const msgs = cid ? await (await fetch('/api/conversations/' + cid + '/messages')).json() : [];
      return { seen, said, user: msgs.length ? msgs[0].content : '' };"""
    )
    check("spoken question → Listening → Thinking → Speaking → Listening again",
          r.get("seen", [])[:4] == ["Listening", "Thinking", "Speaking", "Listening"], r)  # fmt: skip
    check("Whisper understood the question", "net present value" in r.get("user", "").lower(), r)


async def main() -> int:
    if not Path(CHROME).exists():
        print("Google Chrome is needed for the UI test.")
        return 2
    shots = ROOT / "tests" / "ui-shots"
    shots.mkdir(exist_ok=True)
    env = {**os.environ, "SYLVIE_UI_PORT": str(PORT), "HF_HUB_OFFLINE": "1"}
    server = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "ui_dev.py")], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
    profile = Path(tempfile.mkdtemp(prefix="sylvie-ui-test-chrome-"))
    chrome = None
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(BASE + "/api/health")  # 401 = up
            except urllib.error.HTTPError:
                break
            except OSError:
                time.sleep(0.2)
        chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={profile}",
                                   "--no-first-run", "--no-default-browser-check", "--mute-audio", "--hide-scrollbars",
                                   "--autoplay-policy=no-user-gesture-required", "about:blank"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
        for _ in range(50):
            try:
                page = next(
                    x
                    for x in json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json"))
                    if x["type"] == "page"
                )
                break
            except Exception:
                time.sleep(0.2)
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=2**27) as ws:
            t = Tab(ws)
            for domain in ("Page", "Runtime", "Log", "DOM"):
                await t.call(f"{domain}.enable")
            cid = await test_chat(t, shots)
            await test_voice_page(t, cid, shots)
            await test_panel(t, shots)
            await test_other_pages(t)
            if os.environ.get("VOICE"):
                await test_real_voice(t)
    finally:
        if chrome:
            chrome.terminate()
        server.terminate()
        shutil.rmtree(profile, ignore_errors=True)
    failed = [r for r in results if not r[0]]
    print(f"\n{len(results) - len(failed)}/{len(results)} UI checks passed. Screenshots: {shots}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
