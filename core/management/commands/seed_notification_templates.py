"""Seed the DB-backed notification copy catalogue from code defaults (§2.3, P3).

Creates / refreshes one :class:`core.models.NotificationTemplate` row per
``(event, channel)`` the catalogue authors copy for (:func:`core.events.copy.seedable_rows`).

Idempotent and override-safe:

* a **missing** row is created from the seeded default;
* an existing **non-overridden** row is refreshed to the current default (so copy
  changes in code propagate until the copy team takes ownership);
* an existing **overridden** row (``is_overridden=True``) is left untouched — the
  seed never clobbers authored copy.

Run after deploy (or any time the defaults change). Safe to run repeatedly.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from core.events.copy import seedable_rows
from core.models import NotificationTemplate


class Command(BaseCommand):
    help = "Seed/refresh NotificationTemplate copy from code defaults (preserves admin overrides)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress the per-row summary; print only the totals line.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        created = 0
        refreshed = 0
        preserved = 0

        for event_key, channel, default in seedable_rows():
            row = NotificationTemplate.objects.filter(event_key=event_key, channel=channel.value).first()
            if row is None:
                NotificationTemplate.objects.create(
                    event_key=event_key,
                    channel=channel.value,
                    subject=default.subject,
                    body_text=default.body_text,
                    body_html=default.body_html,
                    is_overridden=False,
                )
                created += 1
                continue
            if row.is_overridden:
                preserved += 1
                continue
            row.subject = default.subject
            row.body_text = default.body_text
            row.body_html = default.body_html
            row.save(update_fields=["subject", "body_text", "body_html", "updated_at"])
            refreshed += 1

        summary = (
            f"Seeded notification copy: {created} created, {refreshed} refreshed, {preserved} preserved (overridden)."
        )
        self.stdout.write(self.style.SUCCESS(summary))
