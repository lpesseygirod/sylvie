#!/bin/zsh
# Builds Sylvie.app (menu bar app) from app/SylvieMac. No Xcode project needed.
# Build files go to ~/Library/Caches/SylvieBuild (code signing refuses files inside ~/Documents).
# The app is signed ad-hoc ("-"); Lea can sign with her Developer ID later for distribution.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="$ROOT/app/SylvieMac"
SCRATCH="$HOME/Library/Caches/SylvieBuild"
OUT="$SCRATCH/Sylvie.app"

swift build -c release --package-path "$PKG" --scratch-path "$SCRATCH"
BIN="$(swift build -c release --package-path "$PKG" --scratch-path "$SCRATCH" --show-bin-path)/Sylvie"

rm -rf "$OUT"
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"
cp "$BIN" "$OUT/Contents/MacOS/Sylvie"
cp "$PKG/Resources/Info.plist" "$OUT/Contents/Info.plist"
xattr -cr "$OUT"
codesign --force --sign - --options runtime --entitlements "$PKG/Resources/Sylvie.entitlements" "$OUT"
codesign --verify --verbose=1 "$OUT"
echo "Built $OUT"
