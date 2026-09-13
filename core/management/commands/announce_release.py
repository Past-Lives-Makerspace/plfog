"""Announce the latest release to members — the in-app + email companion to Discord.

Reads the newest entry from ``plfog/version.py`` ``CHANGELOG`` and fires the
``release.published`` event: an in-app bell row + an opt-out email to everyone with a
login (∪ active members ∪ admins), plus a single Discord broadcast. Mirrors the
existing GitHub-Actions Discord changelog post (``.github/workflows/release.yml``)
— that workflow stays; this is the in-app + email companion.

VERSION-TRIGGERED, NOT TIME-TRIGGERED. Run this **once, post-deploy** (the deploy that
ships a new ``VERSION``). It is deduped on the version string via
:class:`core.models.EventDelivery` (period ``release:<version>``), so a second run for
the same version is a safe no-op — but it is deliberately NOT wired into the 15-minute
``run_scheduled_tasks`` cron (a release is a deploy event, not a clock event).

    python manage.py announce_release                        # announce VERSION
    python manage.py announce_release --release-version 0.19.9
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _entry_for(version: str) -> dict[str, Any]:
    """The CHANGELOG entry to announce for ``version``. Raises ``CommandError`` if absent.

    An explicit ``--release-version`` names an already-swept release and is found by its
    number. The default — the current ``VERSION`` — has no numbered entry to find: the
    current batch lives in ``changelog.d/`` and a fragment carries no version of its own
    (``plfog.changelog`` says why), so what just shipped is the newest unswept entry.

    Falling through to the numbered search rather than failing in the first branch matters
    immediately after a sweep, when the batch is empty and ``VERSION`` names the frozen
    entry the sweep just wrote.
    """
    from core.release_email import current_release_entries
    from plfog.version import CHANGELOG, VERSION

    if version == VERSION:
        current = current_release_entries()
        if current:
            return current[0]
    for entry in CHANGELOG:
        # Only swept entries carry a version; skipping the rest is what makes an explicit
        # --release-version mean "a release that has already been folded into history".
        if str(entry.get("version", "")) == version:
            return entry
    raise CommandError(f"No CHANGELOG entry found for version {version!r}.")


def _release_notes(entry: dict[str, Any]) -> str:
    """Render an entry's change list into a plain-text bullet block."""
    changes = entry.get("changes", []) or []
    return "\n".join(f"• {line}" for line in changes)


class Command(BaseCommand):
    help = "Announce the latest release (in-app + email + Discord). Run once post-deploy; idempotent per version."

    def add_arguments(self, parser: Any) -> None:
        # NB: ``--version`` is reserved by Django's BaseCommand, so the override flag
        # is ``--release-version``.
        parser.add_argument(
            "--release-version",
            dest="release_version",
            type=str,
            default=None,
            help="The version to announce (defaults to the current plfog VERSION).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from core.events.emit import emit

        from plfog.version import VERSION

        # Resolved here rather than as the argument default, so the default is the VERSION at
        # RUN time. VERSION is folded from changelog.d/ at import now, not a literal, and
        # binding it into the parser at module import made the current-batch branch below
        # unreachable from a spec that pins the changelog — the one branch a real post-deploy
        # run takes.
        version: str = options["release_version"] or VERSION
        entry = _entry_for(version)
        title = str(entry["title"])

        # Absolute URL — the Discord embed + email link need a full host, not "/".
        # No request here (management command), so use the configured member base.
        site_url = settings.MEMBER_BASE_URL

        result = emit(
            "release.published",
            context={
                "member_name": "there",
                "version": version,
                "release_title": title,
                "release_notes": _release_notes(entry),
                "site_url": site_url,
            },
            url=site_url,
            period=f"release:{version}",
        )
        if result.delivery_count:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Announced v{version}: {result.delivery_count} delivery(ies) to "
                    f"{result.recipient_count} recipient(s)."
                )
            )
        else:
            self.stdout.write(f"v{version} already announced — nothing sent (idempotent).")
