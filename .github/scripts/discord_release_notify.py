"""Post what this release shipped to Discord — chunked under the embed limit.

Reads the entries to announce from the JSON file ``release_plan.py`` wrote (its path arrives
in ``ANNOUNCE_PATH``) rather than filtering ``CHANGELOG`` by an exact ``VERSION`` string.
That filter was the last place a release could go quiet: an entry stamped at the wrong number
deployed, announced nothing, and went green, because "no entry at this version" is also what a
deliberate tooling release looks like. The planner passes the fragments the push **added**, so
the two cases are now distinguishable at the source and neither is inferred from a number.

Fails loudly on a rejected post. The original inline ``curl`` had no ``--fail``, so a 400
looked like success and the 0.19 release silently never posted.

Reads the webhook from ``DISCORD_WEBHOOK_URL``; a blank webhook is a no-op (the "disabled when
blank" idiom), logged so it is never silent.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

_EMBED_COLOR = 3066993  # the workflow's original green
_DESC_LIMIT = 4000  # Discord's hard cap is 4096; leave headroom


def _entries() -> list[dict[str, object]]:
    """The entries to announce, as written by ``release_plan.py``."""
    path = pathlib.Path(os.environ["ANNOUNCE_PATH"])
    entries: list[dict[str, object]] = json.loads(path.read_text(encoding="utf-8"))
    return entries


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

    entries = _entries()
    if not entries:
        print("Nothing member-facing in this release; announcing nothing.")
        return

    version = os.environ["VERSION"]
    title = str(entries[0]["title"])
    chunks = _chunks(_bullets(entries))
    footer = {"text": f"Past Lives Member Portal v{version}"}

    for index, description in enumerate(chunks):
        embed: dict[str, object] = {"description": description, "color": _EMBED_COLOR, "footer": footer}
        if index == 0:
            embed["title"] = f"🚀 New update: {title}"
        elif len(chunks) > 1:
            embed["title"] = f"…continued ({index + 1}/{len(chunks)})"
        _post(webhook, {"embeds": [embed]})

    print(f"Posted v{version} release notes to Discord in {len(chunks)} message(s).")


if __name__ == "__main__":
    main()
