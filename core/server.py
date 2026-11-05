"""Sylvie's local API + chat UI. Listens on 127.0.0.1 only.

Security (rule 2):
- Host header must be 127.0.0.1/localhost on our port → blocks DNS-rebinding attacks from websites.
- Browser requests from any origin other than Sylvie's own UI are rejected.
- Every /api call needs the token (Authorization: Bearer … or the HttpOnly session cookie that
  /auth sets). The token lives in the Keychain.
"""

from __future__ import annotations

import io
import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core.auth import get_or_create_token, tokens_equal
from core.config import ROOT, load_config
from core.orchestrator import Attachment, Sylvie

log = logging.getLogger("sylvie.server")
UI_DIR = ROOT / "ui"
COOKIE = "sylvie_session"
MAX_UPLOAD_BYTES = 30_000_000

LOCKED_PAGE = """<!doctype html><meta charset=utf-8><title>Sylvie</title>
<body style="font-family:-apple-system,sans-serif;max-width:520px;margin:15vh auto;color:#444">
<h2>Sylvie is locked</h2><p>Open Sylvie from the menu bar app, or run <code>make open</code> in the project folder.</p>"""


def create_app(sylvie: Sylvie | None = None, token: str | None = None, warm: bool = True) -> FastAPI:
    cfg = load_config() if sylvie is None else sylvie.cfg
    host = cfg.get_path("server.host", "127.0.0.1")
    port = cfg.get_path("server.port", 8765)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = set(cfg.get_path("server.allowed_origins", [])) | {
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
    }
    token = token or get_or_create_token()
    state = {"sylvie": sylvie, "ready": False, "error": None}

    @asynccontextmanager
    async def lifespan(_app):
        def _boot():
            try:
                if state["sylvie"] is None:
                    state["sylvie"] = Sylvie.build(cfg)
                if warm:
                    state["sylvie"].warm()
                state["ready"] = True
                log.info("Sylvie ready")
            except Exception as e:
                log.exception("startup failed")
                state["error"] = str(e)

        threading.Thread(target=_boot, daemon=True).start()
        yield

    app = FastAPI(title="Sylvie", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def S() -> Sylvie:
        if state["sylvie"] is None:
            raise HTTPException(503, "Sylvie is starting")
        return state["sylvie"]

    phone_hosts = set(cfg.get_path("phone.allowed_hosts", []) or [])

    @app.middleware("http")
    async def security(request: Request, call_next):
        path = request.url.path
        host = request.headers.get("host", "")
        if path.startswith("/phone/"):
            # The phone gateway: reached through the private tunnel (tailscale serve → 127.0.0.1).
            # Only paired-device tokens work here; browsers are refused.
            if host not in allowed_hosts | phone_hosts:
                return JSONResponse({"error": "bad host"}, status_code=403)
            if request.headers.get("origin"):
                return JSONResponse({"error": "browsers not allowed"}, status_code=403)
            auth = request.headers.get("authorization", "")
            device = S().devices.authenticate(auth[7:] if auth.lower().startswith("bearer ") else None)
            if device is None:
                return JSONResponse({"error": "unknown device"}, status_code=401)
            request.state.device = device
            return await call_next(request)
        if host not in allowed_hosts:
            return JSONResponse({"error": "bad host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in allowed_origins:
            return JSONResponse({"error": "origin not allowed"}, status_code=403)
        if path.startswith("/api/"):
            auth = request.headers.get("authorization", "")
            bearer = auth[7:] if auth.lower().startswith("bearer ") else None
            if not (tokens_equal(bearer, token) or tokens_equal(request.cookies.get(COOKIE), token)):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    # ---------- UI ----------
    @app.get("/auth")
    def auth(t: str):
        if not tokens_equal(t, token):
            raise HTTPException(401, "bad token")
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/", max_age=60 * 60 * 24 * 365)
        return resp

    @app.get("/")
    def index(request: Request):
        if not tokens_equal(request.cookies.get(COOKIE), token):
            return HTMLResponse(LOCKED_PAGE, status_code=401)
        return FileResponse(UI_DIR / "index.html")

    if UI_DIR.exists():
        app.mount("/static", StaticFiles(directory=UI_DIR), name="static")

    # ---------- API ----------
    @app.get("/api/health")
    def health():
        return {"ready": state["ready"], "error": state["error"]}

    @app.get("/api/conversations")
    def conversations(search: str | None = None):
        return S().convs.list(search=search)

    @app.post("/api/conversations")
    def new_conversation():
        return S().convs.create()

    @app.patch("/api/conversations/{cid}")
    def rename(cid: str, body: dict = Body(...)):
        S().convs.rename(cid, str(body.get("title", "")))
        return {"ok": True}

    @app.delete("/api/conversations/{cid}")
    def delete(cid: str):
        S().convs.delete(cid)
        return {"ok": True}

    @app.get("/api/conversations/{cid}/messages")
    def messages(cid: str):
        s = S()
        if s.convs.get(cid) is None:
            raise HTTPException(404)
        keep = (
            "id",
            "role",
            "content",
            "sources",
            "feedback",
            "edited_content",
            "mode",
            "created_at",
            "trace",
        )
        return [{k: m[k] for k in keep} for m in s.convs.visible_messages(cid)]

    @app.post("/api/chat")
    def chat(body: dict = Body(...)):
        s = S()
        text = str(body.get("text", "")).strip()
        if not text:
            raise HTTPException(400, "empty message")
        atts = [
            Attachment(str(a.get("name", "file"))[:200], str(a.get("text", "")))
            for a in body.get("attachments") or []
        ]
        thinking = body.get("thinking")
        gen = s.turn(
            text,
            conversation_id=body.get("conversation_id"),
            channel=str(body.get("channel") or "chat"),
            attachments=atts,
            thinking=thinking if isinstance(thinking, bool) else None,
        )

        def sse():
            cid = None
            try:
                for ev in gen:
                    if ev["type"] == "meta":
                        cid = ev["conversation_id"]
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            finally:
                if cid:  # client went away (stop button / closed window): stop generating
                    s.stop(cid)
                gen.close()

        return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    @app.post("/api/voice/turn")
    async def voice(request: Request, conversation_id: str | None = None, channel: str = "voice"):
        """Body: WAV audio. Streams transcript, the answer and `speak` events (one per sentence).
        The audio is only held in memory and dropped right after transcription."""
        from voice.loop import voice_turn

        s = S()
        wav = await request.body()
        if len(wav) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "audio too long")
        if channel not in ("voice", "phone"):
            raise HTTPException(400)
        gen = voice_turn(s, wav, conversation_id=conversation_id, channel=channel)
        del wav

        def sse():
            cid = conversation_id
            try:
                for ev in gen:
                    if ev["type"] == "meta":
                        cid = ev["conversation_id"]
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            finally:
                if cid:
                    s.stop(cid)
                gen.close()

        return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    @app.post("/api/chat/{cid}/stop")
    def stop(cid: str):
        S().stop(cid)
        return {"ok": True}

    @app.post("/api/messages/{mid}/feedback")
    def feedback(mid: int, body: dict = Body(...)):
        s = S()
        v = body.get("value")
        if v not in (1, -1, None):
            raise HTTPException(400)
        s.convs.set_feedback(mid, v)
        s.convs.log_event("feedback", {"message_id": mid, "value": v})
        correction = s.teacher.report(mid, "thumbs_down") if v == -1 else None
        return {"ok": True, "correction_id": correction}

    @app.post("/api/messages/{mid}/teach")
    def teach(mid: int, body: dict = Body(...)):
        """Optional: Lea says what was wrong after a thumbs down."""
        cid = S().teacher.report(mid, "thumbs_down", comment=str(body.get("comment", ""))[:1000] or None)
        return {"ok": True, "correction_id": cid}

    @app.get("/api/corrections")
    def corrections(status: str | None = None):
        return [c.to_dict() for c in S().learning.corrections(status=status)]

    @app.post("/api/corrections/{cid}/accept")
    def correction_accept(cid: int, body: dict = Body(default={})):
        try:
            return S().teacher.accept(cid, edited_text=(body or {}).get("text"))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/corrections/{cid}/reject")
    def correction_reject(cid: int):
        S().teacher.reject(cid)
        return {"ok": True}

    @app.get("/api/training/stats")
    def training_stats():
        return S().learning.stats()

    @app.post("/api/messages/{mid}/edit")
    def edit(mid: int, body: dict = Body(...)):
        s = S()
        text = str(body.get("text", "")).strip()
        m = s.convs.message(mid)
        if m is None or m["role"] != "assistant" or not text:
            raise HTTPException(400)
        s.convs.set_edit(mid, text)
        s.convs.log_event("edit", {"message_id": mid, "original": m["content"], "edited": text})
        s.teacher.report(mid, "edit", edited=text)
        return {"ok": True}

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)):
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "file too large")
        name = Path(file.filename or "file").name
        if name.lower().endswith(".pdf"):
            from pypdf import PdfReader

            try:
                reader = PdfReader(io.BytesIO(data))
                text = "\n\n".join(
                    f"[page {i + 1}]\n{p.extract_text() or ''}" for i, p in enumerate(reader.pages)
                )
            except Exception as e:
                raise HTTPException(400, f"could not read PDF: {e}") from e
        else:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as e:
                raise HTTPException(415, "only text and PDF files for now") from e
        return {"name": name, "text": text, "chars": len(text)}

    # Memory & profile (simple lists now; full dashboard in Phase 6)
    @app.get("/api/memory")
    def memory(all: bool = False):
        return [f.to_dict() for f in S().memory.list(include_inactive=all)]

    @app.patch("/api/memory/{fid}")
    def memory_edit(fid: int, body: dict = Body(...)):
        return S().memory.update_text(fid, str(body["text"])).to_dict()

    @app.delete("/api/memory/{fid}")
    def memory_forget(fid: int):
        S().memory.forget(fid)
        return {"ok": True}

    @app.post("/api/memory/{fid}/restore")
    def memory_restore(fid: int):
        S().memory.restore(fid)
        return {"ok": True}

    @app.get("/api/profile")
    def profile():
        s = S()
        return {
            "traits": [t.to_dict() for t in s.style.list(include_rejected=True)],
            "interests": [i.to_dict() for i in s.interests.list(include_rejected=True)],
            "preferences": [p.__dict__ for p in s.prefs.list(include_inactive=True)],
        }

    @app.patch("/api/profile/traits/{tid}")
    def trait_edit(tid: int, body: dict = Body(...)):
        s = S()
        if "status" in body:
            s.style.set_status(tid, body["status"])
        if "description" in body:
            s.style.update(tid, str(body["description"]))
        return {"ok": True}

    @app.delete("/api/profile/traits/{tid}")
    def trait_delete(tid: int):
        S().style.delete(tid)
        return {"ok": True}

    @app.patch("/api/profile/interests/{iid}")
    def interest_edit(iid: int, body: dict = Body(...)):
        S().interests.set_status(iid, body["status"])
        return {"ok": True}

    @app.delete("/api/profile/interests/{iid}")
    def interest_delete(iid: int):
        S().interests.delete(iid)
        return {"ok": True}

    @app.post("/api/preferences")
    def pref_add(body: dict = Body(...)):
        return {"id": S().prefs.add(str(body["text"]), source_quote="added by Lea")}

    @app.patch("/api/preferences/{pid}")
    def pref_edit(pid: int, body: dict = Body(...)):
        S().prefs.update(pid, text=body.get("text"), active=body.get("active"))
        return {"ok": True}

    @app.get("/api/actions")
    def actions_log(limit: int = 100):
        return [a.to_dict() for a in S().actions.log.recent(limit)]

    @app.post("/api/actions/{aid}/undo")
    def action_undo(aid: int):
        out = S().actions.undo(aid)
        return {"ok": out.ok, "result": out.content}

    # ---------- devices (pairing is done from the Mac, with the local token) ----------
    @app.get("/api/devices")
    def devices_list():
        return [d.to_dict() for d in S().devices.list()]

    @app.post("/api/devices")
    def devices_pair(body: dict = Body(...)):
        """Returns the new device's token ONCE (it isn't stored, only its hash)."""
        name = str(body.get("name", "")).strip() or "iPhone"
        return {"name": name, "token": S().devices.pair(name)}

    @app.delete("/api/devices/{did}")
    def devices_revoke(did: int):
        S().devices.revoke(did)
        return {"ok": True}

    # ---------- phone gateway ----------
    def _phone_conversation(s, given: str | None) -> str | None:
        """One Sylvie everywhere: without an id, continue the most recent conversation if it was
        active in the last 5 minutes (from any device)."""
        if given:
            return given
        recent = s.convs.list(limit=1)
        if recent:
            from datetime import UTC, datetime

            age = (datetime.now(UTC) - datetime.fromisoformat(recent[0]["updated_at"])).total_seconds()
            if age < 300:
                return recent[0]["id"]
        return None

    @app.get("/phone/ping")
    def phone_ping():
        return {"ok": True, "ready": state["ready"]}

    @app.post("/phone/ask")
    def phone_ask(body: dict = Body(...)):
        """For the "Hey Siri, Sylvie" Shortcut: text in, short spoken answer + full answer out."""
        from voice.speech import SentenceStreamer

        s = S()
        text = str(body.get("text", "")).strip()
        if not text:
            raise HTTPException(400, "empty message")
        cid = _phone_conversation(s, body.get("conversation_id"))
        answer, sources, conv = "", [], cid
        for ev in s.turn(text, conversation_id=cid, channel="phone"):
            if ev["type"] == "meta":
                conv = ev["conversation_id"]
            elif ev["type"] == "text":
                answer += ev["text"]
            elif ev["type"] == "sources":
                sources = ev["sources"]
        speaker = SentenceStreamer()
        spoken = speaker.feed(answer) + speaker.flush()
        return {
            "conversation_id": conv,
            "spoken": " ".join(spoken) or answer[:300],
            "answer": answer,
            "sources": sources,
        }

    @app.post("/phone/voice")
    async def phone_voice(request: Request, conversation_id: str | None = None):
        """For the iPhone app: WAV in, streamed transcript/answer/speak events out."""
        return await voice(
            request, conversation_id=_phone_conversation(S(), conversation_id), channel="phone"
        )

    # ---------- blind A/B ----------
    @app.get("/ab")
    def ab_page(request: Request):
        if not tokens_equal(request.cookies.get(COOKIE), token):
            return HTMLResponse(LOCKED_PAGE, status_code=401)
        return FileResponse(UI_DIR / "ab.html")

    @app.get("/api/ab/next")
    def ab_next():
        s = S()
        return {"pair": s.ab.next_pair(), "summary": s.ab.summary()}

    @app.post("/api/ab/{pid}")
    def ab_choose(pid: int, body: dict = Body(...)):
        try:
            S().ab.choose(pid, str(body.get("choice")))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.get("/api/knowledge")
    def knowledge():
        s = S()
        return {"folders": cfg.get_path("knowledge.folders", []), **s.docs.stats()}

    @app.post("/api/knowledge/reindex")
    def reindex():
        s = S()
        folders = cfg.get_path("knowledge.folders", []) or []
        threading.Thread(target=lambda: s.docs.index_folders(folders), daemon=True).start()
        return {"ok": True, "folders": folders}

    app.state.token = token
    app.state.host = host
    app.state.port = port
    return app


def main() -> None:
    import uvicorn

    cfg = load_config()
    host = cfg.get_path("server.host", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("Refusing to listen on a non-local address (rule 2).")
    log_dir = cfg.data_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_dir / "server.log"), logging.StreamHandler()],
    )
    uvicorn.run(
        create_app(), host=host, port=cfg.get_path("server.port", 8765), log_level="warning", access_log=False
    )


if __name__ == "__main__":
    main()
