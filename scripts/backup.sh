#!/bin/zsh
# Backs up Sylvie's data folder (memory, profile, conversations, datasets, adapters) to an
# ENCRYPTED disk image on an external drive. Lea runs it:  zsh scripts/backup.sh /Volumes/MyDrive
# The first run creates the encrypted image and macOS asks you for a password (choose a strong one
# and keep it in your password manager). Nothing is ever deleted from the source.
set -euo pipefail
DEST="${1:?Usage: zsh scripts/backup.sh /Volumes/<external drive>}"
[[ -d "$DEST" ]] || { echo "No such drive: $DEST"; exit 1; }
SRC="$HOME/Library/Application Support/Sylvie"
IMG="$DEST/Sylvie-backup.sparsebundle"
MNT="$(mktemp -d)"

if [[ ! -d "$IMG" ]]; then
  echo "Creating encrypted backup image (AES-256) — macOS will ask for a new password…"
  hdiutil create -size 200g -type SPARSEBUNDLE -fs APFS -encryption AES-256 -volname SylvieBackup "$IMG"
fi
hdiutil attach "$IMG" -mountpoint "$MNT" -nobrowse
trap 'hdiutil detach "$MNT" -quiet || true' EXIT
STAMP="$(date +%Y-%m-%d_%H%M)"
# Consistent copy of the live database, then everything else (services/ is reinstallable, skip it).
mkdir -p "$MNT/$STAMP"
sqlite3 "$SRC/sylvie.db" ".backup '$MNT/$STAMP/sylvie.db'"
rsync -a --exclude 'sylvie.db*' --exclude 'services/' "$SRC/" "$MNT/$STAMP/"
echo "Backup done: $IMG → $STAMP"
