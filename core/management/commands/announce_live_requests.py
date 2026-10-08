"""Tell members their feedback request is live once the release that delivers it is serving (#693).

A changelog fragment may list ``requests = [12, 15]``. Every tick of ``run_scheduled_tasks``
runs this job, which marks each listed request Live (Fixed for a bug) and tells its sender
once, linking to the release's entry in the changelog.

**Only once the web service serves it.** The cron is its own Render service with its own
build, so it can be running a commit the web service failed to deploy. Before acting, the job
reads the version the web service reports at ``{MEMBER_BASE_URL}/health/`` and goes ahead only
when it is at least this process's folded version. Fragments only accumulate, so an equal or
newer web version carries every fragment this process can see. An unreachable or unreadable
web version skips the tick; the next one retries.

**Never twice.** :meth:`core.models.FeedbackRequest.announce_live` locks the row and skips a
request that is already Live or already told (``live_notified_at``), so reruns and redeploys
are no-ops; a sweep removes the fragment from ``changelog.d/`` and the job never reads it
again. A listed number that does not exist, or a request that cannot go live, is logged and
skipped: one bad number never stops the rest or fails the run.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import reverse

from core.models import FeedbackRequest
from plfog.changelog import Fragment, load_fragments, parse_version
from plfog.version import FRAGMENTS_PATH, VERSION

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT_SECONDS = 10


def web_version() -> str | None:
    """The version the web service reports on ``/health/``, or ``None`` when it cannot be read."""
    url = f"{settings.MEMBER_BASE_URL}{reverse('health_check')}"
    try:
        response = httpx.get(url, timeout=HEALTH_TIMEOUT_SECONDS)
        response.raise_for_status()
        version = str(response.json()["version"])
        parse_version(version)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("announce_live_requests: could not read the web version from %s: %s", url, exc)
        return None
    return version


def changelog_url(fragment: Fragment) -> str:
    """Absolute link that opens ``fragment``'s entry in the changelog."""
    return f"{settings.MEMBER_BASE_URL}{reverse('hub_home')}#changelog-{fragment.slug}"


class Command(BaseCommand):
    help = "Mark feedback requests listed in shipped changelog fragments Live and tell their senders once."

    def handle(self, *args: Any, **options: Any) -> None:
        listed = [(fragment, pk) for fragment in load_fragments(FRAGMENTS_PATH) for pk in fragment.requests]
        if not listed:
            self.stdout.write("No changelog fragment lists a request.")
            return
        serving = web_version()
        if serving is None:
            self.stdout.write("Web version unknown; trying again next tick.")
            return
        if parse_version(serving) < parse_version(VERSION):
            self.stdout.write(f"Web serves {serving}, not yet {VERSION}; trying again next tick.")
            return
        found = FeedbackRequest.objects.in_bulk([pk for _fragment, pk in listed])
        for fragment, pk in listed:
            self._announce(fragment, pk, found)

    def _announce(self, fragment: Fragment, pk: int, found: dict[Any, FeedbackRequest]) -> None:
        """Announce one listed request, logging why when it is skipped."""
        if pk not in found:
            self._skip(fragment, pk, "does not exist")
            return
        skip = found[pk].announce_live(release_title=fragment.title, changelog_url=changelog_url(fragment))
        if skip is not None:
            self._skip(fragment, pk, skip)
            return
        self.stdout.write(f"  Request #{pk} is live ({fragment.path.name}); its sender was told.")

    def _skip(self, fragment: Fragment, pk: int, reason: str) -> None:
        logger.info("announce_live_requests: request #%s in %s skipped: it %s", pk, fragment.path.name, reason)
        self.stdout.write(f"  Request #{pk} in {fragment.path.name} skipped: it {reason}.")
