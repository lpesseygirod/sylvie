"""Sets up phone access once Tailscale is installed and signed in on this Mac (Lea runs it):

    make phone NAME="Lea's iPhone"

1. publishes Sylvie's gateway on Lea's tailnet only (`tailscale serve`, never `funnel`),
2. writes the Mac's tailnet name into config.yaml → phone.allowed_hosts,
3. pairs the phone and prints its token once, plus the URL for the Shortcut (docs/iphone.md).
Then restart Sylvie. Nothing here makes Sylvie reachable from the public internet.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def tailscale_cli() -> str | None:
    return shutil.which("tailscale") or (APP_CLI if Path(APP_CLI).exists() else None)


def tailnet_name(status: dict) -> str | None:
    """The Mac's MagicDNS name from `tailscale status --json`, without the trailing dot."""
    if status.get("BackendState") != "Running":
        return None
    name = (status.get("Self") or {}).get("DNSName", "").rstrip(".")
    return name or None


def set_allowed_host(config_text: str, host: str) -> str:
    """Adds `host` to phone.allowed_hosts, keeping the file's comments and layout."""
    m = re.search(r"^(phone:\n(?:[ \t]*#.*\n)*[ \t]+allowed_hosts:[ \t]*)\[(.*?)\]", config_text, re.M)
    if not m:
        raise ValueError("phone.allowed_hosts not found in config.yaml")
    hosts = [h.strip().strip("'\"") for h in m.group(2).split(",") if h.strip()]
    if host not in hosts:
        hosts.append(host)
    listed = ", ".join(f'"{h}"' for h in hosts)
    return config_text[: m.start()] + f"{m.group(1)}[{listed}]" + config_text[m.end() :]


def main() -> None:
    name = " ".join(sys.argv[1:]).strip() or "iPhone"
    cli = tailscale_cli()
    if not cli:
        sys.exit(
            "Tailscale isn't installed. Install it from the Mac App Store, sign in, then run this again."
        )
    status = json.loads(
        subprocess.run([cli, "status", "--json"], capture_output=True, text=True).stdout or "{}"
    )
    host = tailnet_name(status)
    if not host:
        sys.exit("Tailscale isn't connected. Open Tailscale, sign in, then run this again.")

    port = 8765
    subprocess.run([cli, "serve", "--bg", "--https=443", f"http://127.0.0.1:{port}"], check=True)
    print(f"Sylvie's phone gateway is on your tailnet only: https://{host}")

    cfg_path = ROOT / "config.yaml"
    cfg_path.write_text(set_allowed_host(cfg_path.read_text(encoding="utf-8"), host), encoding="utf-8")
    print(f"Added {host} to config.yaml → phone.allowed_hosts")

    from core.config import load_config
    from core.db import Database
    from core.devices import Devices

    cfg = load_config()
    token = Devices(Database(cfg.db_path, dim=cfg.get_path("embeddings.dim", 1024))).pair(name)
    print(f"\nPaired '{name}'. Its token (shown only now):\n\n  {token}\n")
    print(f"Shortcut URL: https://{host}/phone/ask   (steps in docs/iphone.md)")
    print("Now restart Sylvie (stop `make run` and start it again, or it restarts by itself as a service).")


if __name__ == "__main__":
    main()
