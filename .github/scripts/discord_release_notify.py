"""Post the current release's changelog to Discord — chunked under the embed limit.

Aggregates every CHANGELOG entry in the current release line (MAJOR.MINOR) into one
announcement, newest first, and posts it to the ``DISCORD_WEBHOOK_URL`` webhook. The
release line can accumulate several patch entries (e.g. 0.19.1 … 0.19.11), so the
combined notes routinely exceed Discord's 4096-char embed-description limit — this
splits them across as many embeds/messages as needed and FAILS LOUDLY on a rejected
post (the old inline ``curl`` had no ``--fail``, so a 400 looked like success and the
0.19 release silently never posted).

Run from the repo root (so ``plfog.version`` imports). Reads the webhook from the
``DISCORD_WEBHOOK_URL`` env var; a blank webhook is a no-op (the "disabled when blank"
idiom), logged so it is never silent.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, ".")
from plfog.version import CHANGELOG, VERSION  # noqa: E402

_EMBED_COLOR = 3066993  # the workflow's original green
_DESC_LIMIT = 4000  # Discord's hard cap is 4096; leave headroom


def _release_entries() -> list[dict[str, object]]:
    """Every CHANGELOG entry sharing the current MAJOR.MINOR line, newest first."""
    minor = ".".join(VERSION.split(".")[:2])
    entries = [e for e in CHANGELOG if ".".join(str(e["version"]).split(".")[:2]) == minor]
    return entries or [CHANGELOG[0]]


def _bullets(entries: list[dict[str, object]]) -> list[str]:
    """Flatten every entry's changes into ``• line`` bullets."""
    return [f"• {change}" for entry in entries for change in entry["changes"]]


def _chunks(bullets: list[str]) -> list[str]:
    """Greedily pack bullets into description blocks no longer than ``_DESC_LIMIT``."""
    chunks: list[str] = []
    current = ""
    for bullet in bullets:
        candidate = f"{current}\n{bullet}" if current else bullet
        if len(candidate) > _DESC_LIMIT and current:
            chunks.append(current)
            current = bullet
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _post(webhook: str, payload: dict[str, object]) -> None:
    """POST one message to the webhook, raising loudly on any non-2xx response."""
    request = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            # Discord/Cloudflare rejects the default "Python-urllib/x" UA with a 403
            # (error 1010). A descriptive User-Agent is required — see Discord's API docs.
            "User-Agent": "PastLivesReleaseBot (https://github.com/Past-Lives-Makerspace/plfog, 1.0)",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310 - trusted webhook URL
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        sys.exit(f"Discord rejected the post: HTTP {exc.code} {body}")
    except urllib.error.URLError as exc:
        sys.exit(f"Discord post failed (network error): {exc}")
    if not 200 <= status < 300:
        sys.exit(f"Discord post failed: HTTP {status}")


def main() -> None:
    webhook = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if not webhook:
        print("DISCORD_WEBHOOK_URL is blank — skipping Discord release post.")
        return

    entries = _release_entries()
    title = str(entries[0]["title"])
    chunks = _chunks(_bullets(entries))
    footer = {"text": f"Past Lives FOG v{VERSION}"}

    for index, description in enumerate(chunks):
        embed: dict[str, object] = {"description": description, "color": _EMBED_COLOR, "footer": footer}
        if index == 0:
            embed["title"] = f"🚀 New update: {title}"
        elif len(chunks) > 1:
            embed["title"] = f"…continued ({index + 1}/{len(chunks)})"
        _post(webhook, {"embeds": [embed]})

    print(f"Posted v{VERSION} release notes to Discord in {len(chunks)} message(s).")


if __name__ == "__main__":
    main()
