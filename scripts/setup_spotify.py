"""Lets Sylvie start songs and playlists by name (Lea runs this once):  make spotify

1. Go to https://developer.spotify.com/dashboard, log in with your Spotify account, click
   "Create app" (any name, e.g. "Sylvie"; redirect URI: http://127.0.0.1:8765/; API: Web API).
2. Open the app → Settings: copy the Client ID and the Client secret.
3. Paste them here. They go straight into your Mac's Keychain (never into files or logs).

Sylvie only uses them to search Spotify's catalogue; she never logs into your account.
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import keyring  # noqa: E402

from actions.spotify_api import ID_ACCOUNT, SECRET_ACCOUNT, SERVICE  # noqa: E402

print(__doc__)
cid = input("Client ID: ").strip()
secret = getpass.getpass("Client secret (hidden): ").strip()
if not cid or not secret:
    sys.exit("Nothing saved.")
keyring.set_password(SERVICE, ID_ACCOUNT, cid)
keyring.set_password(SERVICE, SECRET_ACCOUNT, secret)
print("Saved in the Keychain. Restart Sylvie:  launchctl kickstart -k gui/$(id -u)/app.sylvie.server")
