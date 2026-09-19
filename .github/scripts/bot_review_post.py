"""Post PastLivesReviewBot's verdict on a pull request, approving only on a clean one.

The reviewer in ``.github/workflows/bot-review.yml`` writes its verdict to a JSON file and
does nothing else; this posts it. Splitting the two is deliberate and is the security model:
the step that runs the model never holds a write credential, and the step that holds the
credential never runs a model, so a prompt injection in the diff under review has nothing to
reach for.

Everything here fails closed. A missing file, unreadable JSON, an empty body, or a verdict
this does not recognise all post a ``COMMENT`` saying no approval was given — never an
``APPROVE``. Note that blockers post as a comment too, not as a ``REQUEST_CHANGES`` review:
withholding the approval is already the block, since main's ruleset will not merge without
one, and a blocking review would only strand the pull request behind something that just the
bot can dismiss.

Reads ``GITHUB_REPOSITORY``, ``PR_NUMBER``, ``GITHUB_TOKEN`` (the bot's PAT) and
``VERDICT_FILE`` from the environment. Exits non-zero when there was no clean verdict to post,
so the failure shows up in the Actions tab as well as on the pull request.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

_RETRY = "Add the `bot-review` label to try again, or review this by hand."

_NO_VERDICT = (
    "PastLivesReviewBot could not complete its review: the review step produced no verdict. "
    f"**No approval has been given.** {_RETRY}"
)
_UNREADABLE = (
    f"PastLivesReviewBot produced a verdict that could not be read as JSON. **No approval has been given.** {_RETRY}"
)
_NO_BODY = f"PastLivesReviewBot produced a verdict with no review text. **No approval has been given.** {_RETRY}"
_UNKNOWN = "PastLivesReviewBot returned an unrecognised verdict, so **no approval has been given**. Its review follows."

#: Fifty funny LGTM, ship it and thumbs up GIFs, each looked at before it was added (2026-09-19). The pull request number
#: picks one, so a re-review of the same PR shows the same GIF.
_LGTM_GIFS = (
    "https://media.giphy.com/media/111ebonMs90YLu/giphy.gif",
    "https://media.giphy.com/media/1ZkMDj88mQ1rO/giphy.gif",
    "https://media.giphy.com/media/QyrysGo7Hz70I/giphy.gif",
    "https://media.giphy.com/media/RIhNQOjGa39Ze/giphy.gif",
    "https://media.giphy.com/media/SWeJXPJPvluIZQSk5j/giphy.gif",
    "https://media.giphy.com/media/iXQ8SgaMQAgtq/giphy.gif",
    "https://media.giphy.com/media/tIeCLkB8geYtW/giphy.gif",
    "https://media.giphy.com/media/13zeE9qQNC5IKk/giphy.gif",
    "https://media.giphy.com/media/143vPc6b08locw/giphy.gif",
    "https://media.giphy.com/media/3kuSo744UIPJjcJUEn/giphy.gif",
    "https://media.giphy.com/media/7Fjz4vLxl6WxbXfbMa/giphy.gif",
    "https://media.giphy.com/media/8VrtCswiLDNnO/giphy.gif",
    "https://media.giphy.com/media/9Ai5dIk8xvBm0/giphy.gif",
    "https://media.giphy.com/media/9JyQbpKdPa1DeDAFyo/giphy.gif",
    "https://media.giphy.com/media/9xt1MUZqkneFiWrAAD/giphy.gif",
    "https://media.giphy.com/media/BEiR3vW16SYt2JosBT/giphy.gif",
    "https://media.giphy.com/media/EPnMuQi2AV9nJup2Se/giphy.gif",
    "https://media.giphy.com/media/F56tNEGsltde/giphy.gif",
    "https://media.giphy.com/media/GVPhxSBr8gVy1yhxDN/giphy.gif",
    "https://media.giphy.com/media/Hc8PMCBjo9BXa/giphy.gif",
    "https://media.giphy.com/media/MZUfuJSlrbWPxqBRwx/giphy.gif",
    "https://media.giphy.com/media/MeChHLiJhQ6vANKIGn/giphy.gif",
    "https://media.giphy.com/media/Q54fw7cxrs9BzCkzQv/giphy.gif",
    "https://media.giphy.com/media/QXJhYxcCaU1LJUHmU2/giphy.gif",
    "https://media.giphy.com/media/S6wdJ27DLVfh9mA9dE/giphy.gif",
    "https://media.giphy.com/media/SShJcu4ySty1G6MX9A/giphy.gif",
    "https://media.giphy.com/media/VhWVAa7rUtT3xKX6Cd/giphy.gif",
    "https://media.giphy.com/media/XdUMQuuiaAEkOUtezd/giphy.gif",
    "https://media.giphy.com/media/YnHrhXDadyaF6q1yBW/giphy.gif",
    "https://media.giphy.com/media/d31xcQedqXbduoyA/giphy.gif",
    "https://media.giphy.com/media/fJ5veFeMaJd3UXGfc3/giphy.gif",
    "https://media.giphy.com/media/g3k2ZBNe5F31kv5KR7/giphy.gif",
    "https://media.giphy.com/media/h9TQwUxEla2nias5mi/giphy.gif",
    "https://media.giphy.com/media/hpAMh2sBYpsmFhSRPl/giphy.gif",
    "https://media.giphy.com/media/iBEW5Amz0ztza/giphy.gif",
    "https://media.giphy.com/media/kBbbp1gMez273lheaf/giphy.gif",
    "https://media.giphy.com/media/kigfYxdEa5s1ziA2h1/giphy.gif",
    "https://media.giphy.com/media/l41lUjUgLLwWrz20w/giphy.gif",
    "https://media.giphy.com/media/qX6rrmE39VCUB8MZyi/giphy.gif",
    "https://media.giphy.com/media/qYGvebgOKGygdOgflY/giphy.gif",
    "https://media.giphy.com/media/rq7jBVljy6Kw35PAEi/giphy.gif",
    "https://media.giphy.com/media/sAPtuCkZzI59WvkMwg/giphy.gif",
    "https://media.giphy.com/media/uUzyWtasBuVu9GUr1l/giphy.gif",
    "https://media.giphy.com/media/wolgqFz9BgIiJmTEfz/giphy.gif",
    "https://media.giphy.com/media/wtUTJUtDDKB36UN7X0/giphy.gif",
    "https://media.giphy.com/media/xCgeEazpis53cUqoZY/giphy.gif",
    "https://media.giphy.com/media/xDgaiJzfzQOLOERc7f/giphy.gif",
    "https://media.giphy.com/media/xHMIDAy1qkzNS/giphy.gif",
    "https://media.giphy.com/media/xUOxfg0ESyhKOv4Vva/giphy.gif",
    "https://media.giphy.com/media/zczuaAkE8TDri/giphy.gif",
)
_LEADING_LGTM = re.compile(r"\ALGTM\W*\n+", re.IGNORECASE)


def read_verdict(path: pathlib.Path) -> str | None:
    """Read the verdict file the reviewer was asked to write.

    Args:
        path: Where the reviewer was told to write its verdict.

    Returns:
        The file's contents, or ``None`` when it is missing or unreadable — which is the
        normal shape of a review that failed, timed out, or never started.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def decide(raw: str | None) -> tuple[str, str, bool]:
    """Turn the reviewer's raw verdict into the review to post.

    Args:
        raw: The verdict file's contents, or ``None`` when there was no file. Treat this as
            untrusted: it is written by a model that has just read an attacker-supplied diff.

    Returns:
        The GitHub review event to submit, the Markdown body to post, and whether the
        reviewer actually produced a clean verdict. Only a well-formed ``approve`` verdict
        with a body ever yields ``APPROVE``.
    """
    if raw is None or not raw.strip():
        return "COMMENT", _NO_VERDICT, False

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return "COMMENT", _UNREADABLE, False

    if not isinstance(parsed, dict):
        return "COMMENT", _UNREADABLE, False

    # `.get` rather than `[...]` on purpose: this is the one place in the codebase where a
    # missing key is an expected outcome to be handled, not a bug to fail loudly on.
    body = str(parsed.get("body") or "").strip()
    if not body:
        return "COMMENT", _NO_BODY, False

    verdict = str(parsed.get("verdict") or "").strip()
    if verdict == "approve":
        return "APPROVE", body, True
    if verdict == "request_changes":
        return "COMMENT", body, True
    return "COMMENT", f"{_UNKNOWN}\n\n{body}", False


def with_lgtm(body: str, pull_request: str) -> str:
    """Open an approval with "LGTM" and a GIF.

    Added here rather than asked of the model, so an approval always starts the same way and
    the only image URLs ever posted are the ones in ``_LGTM_GIFS``. A leading "LGTM" line the
    model wrote anyway is dropped so it does not appear twice.

    Args:
        body: The approval's review text.
        pull_request: The pull request number, as a string; it picks the GIF.

    Returns:
        The body to post.
    """
    gif = _LGTM_GIFS[int(pull_request) % len(_LGTM_GIFS)]
    return f"LGTM\n\n![LGTM]({gif})\n\n{_LEADING_LGTM.sub('', body)}"


def post_review(repo: str, pull_request: str, token: str, event: str, body: str) -> None:
    """Submit the review to GitHub as whichever account owns the token.

    Args:
        repo: The ``owner/name`` of the repository.
        pull_request: The pull request number, as a string.
        token: The bot's personal access token. Its owner is the review's author.
        event: ``APPROVE`` or ``COMMENT``.
        body: The Markdown review body.

    Raises:
        SystemExit: If GitHub rejects the review or cannot be reached. Failing loudly
            matters here — a silently dropped approval looks exactly like a blocked PR.
    """
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/pulls/{pull_request}/reviews",
        data=json.dumps({"event": event, "body": body}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "PastLivesReviewBot",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        sys.exit(f"GitHub rejected the review: HTTP {exc.code} {detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"Could not reach GitHub to post the review: {exc}")
    if status >= 300:
        sys.exit(f"GitHub rejected the review: HTTP {status}")


def main() -> None:
    """Post the verdict, then fail the job if there was not a clean one."""
    repo = os.environ["GITHUB_REPOSITORY"]
    pull_request = os.environ["PR_NUMBER"]
    token = os.environ["GITHUB_TOKEN"]
    path = pathlib.Path(os.environ["VERDICT_FILE"])

    event, body, clean = decide(read_verdict(path))
    if event == "APPROVE":
        body = with_lgtm(body, pull_request)
    post_review(repo, pull_request, token, event, body)
    print(f"Posted a {event} review on #{pull_request}.")

    if not clean:
        sys.exit("No clean verdict was produced, so no approval was given.")


if __name__ == "__main__":
    main()
