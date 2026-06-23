#!/usr/bin/env bash
#
# Fetch a small pool of free stock images for the demo_data seed.
#
# asset-cli (https://github.com/hexagonstorms/asset-cli) runs on the HOST — it
# needs curl/jq and the Pixabay/Unsplash API keys in ~/Code/asset-cli/.env, none
# of which exist inside the plfog-web container. So we download here, into the
# bind-mounted media/ dir, and let `manage.py demo_data` attach whatever it finds.
#
# Re-running is safe: each canonical file is overwritten. If asset-cli or the
# network is unavailable, the seed command simply skips image attachment.
#
# Usage:  ./scripts/fetch_demo_images.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${REPO_ROOT}/media/demo_seed"
TMP="${DEST}/.tmp"
ASSETS_BIN="${ASSETS_BIN:-assets}"

if ! command -v "$ASSETS_BIN" >/dev/null 2>&1; then
  echo "✗ '$ASSETS_BIN' not on PATH — install asset-cli first. Nothing fetched." >&2
  exit 0
fi

mkdir -p "$DEST" "$TMP"

# search "<query>"  then  grab <result#> <canonical-name> [<result#> <name> ...]
search() {
  echo "→ search: $1"
  "$ASSETS_BIN" search "$1" --source pixabay --landscape -n 8 >/dev/null 2>&1 \
    || echo "  ✗ search failed (skipping this batch)"
}

grab() {
  local num="$1" out="$2"
  rm -f "$TMP"/* 2>/dev/null || true
  if "$ASSETS_BIN" download "$num" "$TMP" >/dev/null 2>&1; then
    local f
    f="$(ls -t "$TMP" | head -1 || true)"
    if [[ -n "$f" ]]; then
      mv -f "$TMP/$f" "$DEST/$out"
      echo "  ✓ $out"
      return 0
    fi
  fi
  echo "  ✗ could not download #$num → $out"
}

# Heroes + a shared gallery pool. Names are the contract the seed command reads.
search "pottery ceramics studio"
grab 1 hero_intro.jpg
grab 2 gallery_1.jpg
grab 3 gallery_2.jpg

search "glassblowing glass art"
grab 1 hero_advanced.jpg
grab 2 gallery_3.jpg

search "handmade jewelry workshop"
grab 1 hero_pending.jpg
grab 2 gallery_4.jpg

rmdir "$TMP" 2>/dev/null || true
echo ""
echo "Done. Images in: $DEST"
ls -1 "$DEST" 2>/dev/null | sed 's/^/  /'
