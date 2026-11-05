#!/bin/zsh
# Installs SearXNG (local web search) natively, without Docker, into Sylvie's data folder.
# Lea runs this herself once:  zsh scripts/install_searxng.sh
# No sudo. SearXNG is AGPL-3.0: fine for private use as a separate local service (see docs/LICENSES.md).
set -euo pipefail

SVC="$HOME/Library/Application Support/Sylvie/services"
SRC="$SVC/searxng"
SETTINGS="$SVC/searxng-settings.yml"
export UV_NO_TELEMETRY=1

mkdir -p "$SVC"
if [[ ! -d "$SRC/.git" ]]; then
  git clone --depth 1 https://github.com/searxng/searxng.git "$SRC"
else
  git -C "$SRC" pull --ff-only
fi

uv venv --allow-existing --python 3.13 "$SRC/.venv"
uv pip install --python "$SRC/.venv/bin/python" -r "$SRC/requirements.txt" -r "$SRC/requirements-server.txt"
uv pip install --python "$SRC/.venv/bin/python" --no-build-isolation -e "$SRC"

if [[ ! -f "$SETTINGS" ]]; then
  SECRET=$(openssl rand -hex 32)
  cat > "$SETTINGS" <<EOF
use_default_settings: true
general:
  instance_name: "Sylvie search"
  enable_metrics: false
server:
  bind_address: "127.0.0.1"   # only this Mac can use it
  port: 8888
  secret_key: "$SECRET"
  limiter: false
  public_instance: false
  image_proxy: false
search:
  formats: [html, json]       # Sylvie uses the JSON API
  safe_search: 0
  autocomplete: ""
outgoing:
  request_timeout: 4.0
EOF
  chmod 600 "$SETTINGS"
fi

echo "SearXNG installed. Start it with:  make search"
