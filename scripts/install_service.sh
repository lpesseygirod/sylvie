#!/bin/zsh
# Installs Sylvie's backend as a LaunchAgent: starts at login, restarts automatically if it crashes,
# keeps the model warm. Lea runs this herself (it adds a login item):  zsh scripts/install_service.sh
# Remove with:  launchctl bootout gui/$(id -u)/app.sylvie.server && rm ~/Library/LaunchAgents/app.sylvie.server.plist
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/app.sylvie.server.plist"
LOGS="$HOME/Library/Application Support/Sylvie/logs"
UV="$(command -v uv)"
mkdir -p "$LOGS" "$(dirname "$PLIST")"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>app.sylvie.server</string>
  <key>ProgramArguments</key>
  <array><string>$UV</string><string>run</string><string>--no-sync</string><string>python</string><string>-m</string><string>core.server</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HF_HUB_OFFLINE</key><string>1</string>
    <key>HF_HUB_DISABLE_TELEMETRY</key><string>1</string>
    <key>DO_NOT_TRACK</key><string>1</string>
    <key>UV_NO_TELEMETRY</key><string>1</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$LOGS/service.out.log</string>
  <key>StandardErrorPath</key><string>$LOGS/service.err.log</string>
</dict>
</plist>
PL
launchctl bootout "gui/$(id -u)/app.sylvie.server" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Sylvie's server now starts at login and restarts if it crashes."
