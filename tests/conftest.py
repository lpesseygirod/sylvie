import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_zotero(monkeypatch, tmp_path_factory):
    """Tests never read Lea's real Zotero library (~/Zotero): it points at an empty folder."""
    import knowledge.zotero as z

    empty = tmp_path_factory.mktemp("no-zotero")
    monkeypatch.setattr(z, "zotero_dir", lambda cfg: empty)
    z._open.clear()
