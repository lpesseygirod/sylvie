"""Opens the chat UI in the default browser, unlocked with the Keychain token (never printed)."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.auth import get_or_create_token  # noqa: E402
from core.config import load_config  # noqa: E402

cfg = load_config()
port = cfg.get_path("server.port", 8765)
subprocess.run(["open", f"http://127.0.0.1:{port}/auth?t={get_or_create_token()}"], check=False)
