"""Tiny authenticated API client for checks from the terminal (token read from Keychain, never printed).
Usage: python scripts/api.py GET /api/health  |  python scripts/api.py CHAT "message" [conversation_id]
       python scripts/api.py WAIT   (waits until Sylvie is ready)"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from core.auth import get_or_create_token  # noqa: E402
from core.config import load_config  # noqa: E402

cfg = load_config()
BASE = f"http://127.0.0.1:{cfg.get_path('server.port', 8765)}"
H = {"Authorization": f"Bearer {get_or_create_token()}"}

cmd = sys.argv[1]
if cmd == "WAIT":
    for _ in range(240):
        try:
            h = httpx.get(BASE + "/api/health", headers=H, timeout=2).json()
            if h["ready"] or h["error"]:
                print(h)
                break
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(1)
elif cmd == "GET":
    print(
        json.dumps(httpx.get(BASE + sys.argv[2], headers=H, timeout=30).json(), indent=1, ensure_ascii=False)
    )
elif cmd == "CHAT":
    body = {"text": sys.argv[2], "conversation_id": sys.argv[3] if len(sys.argv) > 3 else None}
    t0 = time.time()
    first = None
    text = ""
    with httpx.stream("POST", BASE + "/api/chat", json=body, headers=H, timeout=300) as r:
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev["type"] == "text":
                first = first or time.time() - t0
                text += ev["text"]
            elif ev["type"] == "done":
                print(
                    f"[conversation {ev['conversation_id']}] first word {first or 0:.2f}s, total {time.time() - t0:.2f}s"
                )
                print("trace:", ev["trace"])
            elif ev["type"] != "thinking":
                print(ev)
    print("SYLVIE:", text)
elif cmd == "VOICE":
    wav = Path(sys.argv[2]).read_bytes()
    t0 = time.time()
    first_speak = None
    with httpx.stream(
        "POST", BASE + "/api/voice/turn", content=wav, headers={**H, "Content-Type": "audio/wav"}, timeout=300
    ) as r:
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev["type"] == "transcript":
                print(f"heard ({ev['language']}, {ev['ms']} ms): {ev['text']}")
            elif ev["type"] == "speak":
                first_speak = first_speak or time.time() - t0
                print(f"  speak +{time.time() - t0:.2f}s [{ev['language']}]: {ev['text']}")
            elif ev["type"] == "done":
                print(f"first sentence ready after {first_speak or 0:.2f}s (audio upload → speakable text)")
                print("trace:", ev.get("trace"))
elif cmd == "POST":
    body = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    print(
        json.dumps(
            httpx.post(BASE + sys.argv[2], json=body, headers=H, timeout=60).json(),
            indent=1,
            ensure_ascii=False,
        )
    )
