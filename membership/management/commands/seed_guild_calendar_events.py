"""One-time, re-runnable importer that pre-fills each guild's studio hours + meetings from the
Public Google Calendar so leads don't start from a blank page.

Manual + re-runnable — NEVER wired into ``sync_all_sources`` (the nightly sweep). It only READS
Google and WRITES FOG rows; it never pushes back to Google (a lead's next save does that).

**Create-if-absent:** a VEVENT whose iCal UID already matches a row is reported "exists, skipped"
and never touched, so a re-run never stomps a lead's later edits. The flip side: if a lead
*deletes* a seeded row, a re-run resurrects it. Treat this as a run-once-early tool (before leads
curate their hours), or accept the resurrection.

Usage::

    manage.py seed_guild_calendar_events --ical-url <public-ical-url> [--dry-run]
    manage.py seed_guild_calendar_events --feed "General Calendar" [--dry-run]
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError


def _fetch_ical_bytes(url: str) -> bytes:
    """Fetch the raw iCal document from ``url`` (its own seam, patched to fixture bytes in tests)."""
    import urllib.request

    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - operator-supplied URL
        return bytes(response.read())


class Command(BaseCommand):
    help = (
        "Seed guild studio hours + meetings from a Public Calendar iCal feed (create-if-absent, "
        "re-runnable). Never pushes to Google; a re-run resurrects a lead-deleted row, so run it "
        "early, before leads curate their hours."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--ical-url", dest="ical_url", help="Public iCal URL to read events from.")
        parser.add_argument("--feed", dest="feed", help="Name of a configured CalendarFeed to read its iCal URL.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the report without writing any rows.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.calendar_seed import (
            AmbiguousMatch,
            classify_type,
            guild_for_title,
            parse_seed_vevents,
            recurrence_for_rrule,
        )
        from membership.models import CommunityEvent, Guild

        raw = self._load_bytes(options)
        dry_run = bool(options["dry_run"])
        guilds = list(Guild.objects.all())
        # Preload every already-imported UID in one query, then dedupe in memory — no per-VEVENT
        # exists() round-trip. Newly created UIDs are added so a repeat within this file is skipped.
        existing_uids = set(
            CommunityEvent.objects.exclude(import_source_uid="").values_list("import_source_uid", flat=True)
        )
        rows: list[tuple[str, str, str, str, str]] = []
        created = 0
        skipped = 0

        for seed in parse_seed_vevents(raw):
            if not seed.uid:
                rows.append((seed.summary, "—", "—", "—", "skip: no uid"))
                skipped += 1
                continue
            match = guild_for_title(seed.summary, guilds)
            if match is None:
                rows.append((seed.summary, "—", "—", "—", "skip: no guild match"))
                skipped += 1
                continue
            if isinstance(match, AmbiguousMatch):
                names = "/".join(guild.name for guild in match.guilds)
                rows.append((seed.summary, names, "—", "—", "skip: ambiguous"))
                skipped += 1
                continue
            event_type = classify_type(seed.summary)
            if event_type is None:
                rows.append((seed.summary, match.name, "—", "—", "skip: unclassified"))
                skipped += 1
                continue
            if seed.ends_at <= seed.starts_at:
                rows.append((seed.summary, match.name, event_type.label, "—", "skip: bad time range"))
                skipped += 1
                continue
            recurrence = recurrence_for_rrule(seed.rrule)
            if seed.uid in existing_uids:
                rows.append((seed.summary, match.name, event_type.label, recurrence.label, "exists, skipped"))
                skipped += 1
                continue
            if not dry_run:
                CommunityEvent.objects.create(
                    guild=match,
                    event_type=event_type,
                    title=seed.summary,
                    starts_at=seed.starts_at,
                    ends_at=seed.ends_at,
                    location=seed.location,
                    description=seed.description,
                    recurrence=recurrence,
                    google_calendar_target=CommunityEvent.GoogleCalendarTarget.PUBLIC,
                    moderation_state=CommunityEvent.ModerationState.PUBLISHED,
                    import_source_uid=seed.uid,
                )
                existing_uids.add(seed.uid)
            rows.append(
                (seed.summary, match.name, event_type.label, recurrence.label, "would create" if dry_run else "created")
            )
            created += 1

        self._print_report(rows)
        verb = "would create" if dry_run else "created"
        self.stdout.write(self.style.SUCCESS(f"{created} {verb}, {skipped} skipped."))

    def _load_bytes(self, options: dict[str, Any]) -> bytes:
        from core.models import CalendarFeed

        url = options["ical_url"]
        feed_name = options["feed"]
        if not url and not feed_name:
            raise CommandError("Provide --ical-url <url> or --feed <CalendarFeed name>.")
        if url and feed_name:
            raise CommandError("Provide only one of --ical-url or --feed, not both.")
        if feed_name:
            try:
                url = CalendarFeed.objects.get(name=feed_name).ical_url
            except CalendarFeed.DoesNotExist as exc:
                raise CommandError(f"No CalendarFeed named '{feed_name}'.") from exc
        return _fetch_ical_bytes(url)

    def _print_report(self, rows: list[tuple[str, str, str, str, str]]) -> None:
        self.stdout.write("")
        self.stdout.write(f"{'Source title':<40} {'Guild':<22} {'Type':<16} {'Recurrence':<14} Action")
        for summary, guild_label, type_label, recurrence_label, action in rows:
            self.stdout.write(
                f"{summary[:40]:<40} {guild_label[:22]:<22} {type_label[:16]:<16} {recurrence_label[:14]:<14} {action}"
            )
