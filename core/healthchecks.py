"""Report each cron run to a Healthchecks check, so a run that did not do its job is seen.

Render's own cron status is not trusted: a non-zero exit has shown as "succeeded", and a
command that catches a task's error and returns normally exits 0 either way. So each cron
command wraps its work in :func:`report_to_healthchecks`, which pings ``<url>/start`` on
entry, then ``<url>`` when the run did its job, or ``<url>/fail`` when it raised or the command
called :meth:`HealthcheckRun.fail`. The POST body (the last ``BODY_LIMIT_BYTES``) is stored by
Healthchecks as the run's log: the summary lines on success, the failures and the traceback
tail on failure.

Render runs a cron command without a shell, so ``cmd && curl ...`` cannot work there; the
ping has to come from inside Python.

``settings.HEALTHCHECKS_PING_URL`` is set on each Render cron service separately (one check per
cron). Blank, the default and always on staging, makes this do nothing. A ping that fails
(network, timeout, non-2xx) is logged as a warning and never changes the run's outcome.
"""

from __future__ import annotations

import logging
import traceback
from contextlib import contextmanager
from typing import TYPE_CHECKING

import httpx
from django.conf import settings

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

# Cloudflare fronts the Healthchecks host and answers httpx's and urllib's default agents with a 403.
USER_AGENT = "plfog-cron"
TIMEOUT_SECONDS = 10
# Healthchecks keeps the first 10,000 bytes of a ping body; the tail is where the error is.
BODY_LIMIT_BYTES = 10_000


class HealthcheckRun:
    """What one run reports: summary lines for the log, and any failures that make it fail."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures: list[str] = []

    def note(self, line: str) -> None:
        """Add a line to the run's log."""
        self.lines.append(line)

    def fail(self, reason: str) -> None:
        """Mark the run failed although the command returns normally (a caught, partial failure)."""
        self.failures.append(reason)

    def body(self) -> str:
        """The log text: every noted line, then every failure."""
        return "\n".join([*self.lines, *(f"FAILED: {reason}" for reason in self.failures)])


def ping(base_url: str, signal: str = "", body: str = "") -> None:
    """POST ``body`` (its last ``BODY_LIMIT_BYTES``) to ``base_url`` plus ``/<signal>``; log a failure, never raise.

    Args:
        base_url: The check's ping URL, without a trailing slash.
        signal: ``"start"``, ``"fail"``, or ``""`` for success.
        body: The run's log text.
    """
    url = f"{base_url}/{signal}" if signal else base_url
    content = body.encode()[-BODY_LIMIT_BYTES:]
    try:
        response = httpx.post(url, content=content, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
    except Exception as exc:  # A monitoring ping must never change the run it reports on.
        # The URL is a secret (anyone holding it can report to the check) and httpx's messages quote
        # it, so the log names only the signal, the error type and, for a refusal, its status.
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else ""
        logger.warning("Healthchecks %s ping failed: %s %s", signal or "success", type(exc).__name__, status)


@contextmanager
def report_to_healthchecks() -> Iterator[HealthcheckRun]:
    """Ping start, run the body, then ping success or fail. An exception is reported and re-raised."""
    url = settings.HEALTHCHECKS_PING_URL.rstrip("/")
    run = HealthcheckRun()
    if not url:
        yield run
        return
    ping(url, "start")
    try:
        yield run
    except BaseException:
        ping(url, "fail", f"{run.body()}\n{traceback.format_exc()}".lstrip("\n"))
        raise
    ping(url, "fail" if run.failures else "", run.body())
