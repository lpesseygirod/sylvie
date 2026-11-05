"""Pairs a new device (Lea runs this): prints its token ONCE, to paste into the Shortcut or app.
Usage: make pair NAME="Lea's iPhone"     Revoke from the dashboard or: DELETE /api/devices/<id>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import load_config  # noqa: E402
from core.db import Database  # noqa: E402
from core.devices import Devices  # noqa: E402

cfg = load_config()
name = " ".join(sys.argv[1:]).strip() or "iPhone"
token = Devices(Database(cfg.db_path, dim=cfg.get_path("embeddings.dim", 1024))).pair(name)
print(f"Paired '{name}'. Its token (shown only now — paste it into the Shortcut / app):\n\n  {token}\n")
