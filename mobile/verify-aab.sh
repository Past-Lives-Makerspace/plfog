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

# --- Native push plugin must be compiled into the binary ---------------------
# Push is the one feature the server-URL architecture CANNOT ship on its own:
# native-push.js is served from members.pastlives.space, but it no-ops unless
# the @capacitor/push-notifications plugin is inside the APK's dex. The
# versionCode-5 release announced "push on your phone" while shipping a binary
# that lacked the plugin, so the app never prompted and prod logged zero device
# registrations. This asserts the plugin's class descriptor is actually present.
# Use `grep -c` (reads to EOF), NOT `grep -q` (early close -> SIGPIPE -> pipefail
# false failure), per the note at the top. Multidex: the plugin can land in any
# classesN.dex, so scan every dex entry.
push_found=0
for dex in ${(f)"$(print -r -- "$listing" | grep -oE 'base/dex/[^[:space:]]+\.dex' || true)"}; do
  if [[ "$(unzip -p "$AAB" "$dex" | grep -ac 'capacitorjs/plugins/pushnotifications' || true)" -gt 0 ]]; then
    push_found=1
    break
  fi
done
if [[ "$push_found" -eq 1 ]]; then
  echo "  ok   native push plugin in dex (capacitorjs/plugins/pushnotifications)"
else
  echo "  MISS native push plugin absent from dex (app cannot receive push; run: npx cap sync android, then rebuild)"
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo ""
  echo "GATE FAILED. Do NOT upload this AAB. Run: npx cap sync android, then rebuild."
  exit 1
fi

echo ""
echo "GATE PASSED. Safe to upload to Play."
