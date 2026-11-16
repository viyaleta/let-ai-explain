#!/bin/bash
# Zip and DMG the built app.  Usage: packaging/macos/package.sh "dist/Let AI Explain.app" out/
set -euo pipefail
APP="$1"
OUT="${2:-out}"
NAME="Let AI Explain"
mkdir -p "$OUT"

# Zip with ditto so symlinks, permissions and signatures survive.
rm -f "$OUT/LetAIExplain-macOS.zip"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$OUT/LetAIExplain-macOS.zip"

# DMG with the app next to an Applications shortcut, for drag-to-install.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/$NAME.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$OUT/LetAIExplain-macOS.dmg"
hdiutil create -volname "$NAME" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$OUT/LetAIExplain-macOS.dmg" >/dev/null

ls -lh "$OUT"
