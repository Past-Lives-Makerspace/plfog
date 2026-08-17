#!/usr/bin/env zsh
# Preflight gate for the Play Store bundle.
#
# The v1.0.0 (versionCode 3) launch shipped an AAB with NO capacitor.config.json
# and NO bundled web assets. Without the config the native shell has no
# server.url, so it defaults to serving local files from https://localhost/,
# finds nothing, and dies with ERR_CONNECTION_REFUSED. Google review does not
# functionally load the page, so it passed and reached real users broken.
#
# Run this on EVERY release bundle before uploading. It fails loudly if the
# web assets are missing. Never upload an AAB this script has not passed.
#
# Usage:
#   zsh verify-aab.sh [path/to/app-release.aab]

set -euo pipefail

AAB="${1:-android/app/build/outputs/bundle/release/app-release.aab}"

if [[ ! -f "$AAB" ]]; then
  echo "FAIL: no AAB at $AAB (build it first: cd android && ./gradlew clean bundleRelease)"
  exit 1
fi

echo "Checking: $AAB"

# Capture the listing once. Do NOT pipe `unzip -l` straight into `grep -q`:
# grep -q closes the pipe on first match, unzip dies with SIGPIPE, and under
# `set -o pipefail` that reports failure even when the entry IS present.
listing="$(unzip -l "$AAB")"

fail=0
for entry in base/assets/capacitor.config.json base/assets/public/index.html; do
  if print -r -- "$listing" | grep -q "$entry"; then
    echo "  ok   $entry"
  else
    echo "  MISS $entry"
    fail=1
  fi
done

# The config must actually point at the live origin, not localhost.
url="$(unzip -p "$AAB" base/assets/capacitor.config.json 2>/dev/null | grep -o 'https://members.pastlives.space' || true)"
if [[ "$url" == "https://members.pastlives.space" ]]; then
  echo "  ok   server.url -> https://members.pastlives.space"
else
  echo "  MISS server.url is not https://members.pastlives.space"
  fail=1
fi

# Must be signed.
if print -r -- "$listing" | grep -qE 'META-INF/.*\.(RSA|EC|DSA)'; then
  echo "  ok   signed"
else
  echo "  MISS not signed (check key.properties)"
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo ""
  echo "GATE FAILED. Do NOT upload this AAB. Run: npx cap sync android, then rebuild."
  exit 1
fi

echo ""
echo "GATE PASSED. Safe to upload to Play."
