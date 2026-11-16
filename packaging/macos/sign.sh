#!/bin/bash
# Sign the built app with a Developer ID, inside-out: every nested binary first, then each
# framework (deepest first), then the app bundle itself. Hardened runtime and a secure timestamp
# throughout; the entitlements go on the app's main executable only.
#
#   packaging/macos/sign.sh "dist/Let AI Explain.app" "<identity name or SHA-1>"
#
# Add "--adhoc" as a third argument to use an ad-hoc identity ("-") with no timestamp, for local testing.
set -euo pipefail
APP="${1:?usage: sign.sh <app> <identity> [--adhoc]}"
IDENTITY="${2:?usage: sign.sh <app> <identity> [--adhoc]}"
ENTITLEMENTS="$(cd "$(dirname "$0")" && pwd)/entitlements.plist"
TS="--timestamp"
[ "${3:-}" = "--adhoc" ] && TS="--timestamp=none"
MAIN="$APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"

sign() { codesign --force --options runtime $TS --sign "$IDENTITY" "$@"; }
# Deepest paths first, so anything inside a folder is signed before the folder.
by_depth() { awk -F/ '{ print NF "\t" $0 }' | sort -t$'\t' -k1,1nr -k2 | cut -f2-; }

# 1. Loose Mach-O files (.so, .dylib, helper executables), skipping symlinks and the main executable.
count=0
while IFS= read -r f; do
  [ "$f" = "$MAIN" ] && continue
  if file -b "$f" | grep -q "Mach-O"; then sign "$f"; count=$((count + 1)); fi
done < <(find "$APP/Contents" -type f ! -path "*/_CodeSignature/*" | by_depth)
echo "Signed $count nested binaries."

# 2. Nested bundles (frameworks, plugins, helper apps), deepest first.
while IFS= read -r b; do
  sign "$b"; echo "Signed ${b#"$APP"/}"
done < <(find "$APP/Contents" -type d \( -name "*.framework" -o -name "*.bundle" -o -name "*.app" -o -name "*.xpc" -o -name "*.plugin" \) | by_depth)

# 3. The app itself, with the entitlements the hardened runtime needs for Python and pywebview.
sign --entitlements "$ENTITLEMENTS" "$APP"
echo "Signed $(basename "$APP")."
codesign --verify --deep --strict --verbose=2 "$APP"
