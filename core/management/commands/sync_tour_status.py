"""Bulk-refresh Simplybook tour status for users with stale caches.

Cron-friendly: run nightly. Polls Simplybook for every user whose
``tour_status_checked_at`` is older than ``--max-age-hours`` (default 24)
or has never been checked.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from core.services.tour_status import force_refresh


class Command(BaseCommand):
    help = "Refresh Simplybook tour status for users with stale caches."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--max-age-hours",
            type=int,
            default=24,
            help="Refresh users whose tour_status_checked_at is older than this (default: 24).",
        )

    def handle(self, *args, **options) -> None:
        max_age = options["max_age_hours"]
        cutoff = timezone.now() - timedelta(hours=max_age)
        User = get_user_model()
        stale = User.objects.filter(
            Q(profile__tour_status_checked_at__lt=cutoff) | Q(profile__tour_status_checked_at__isnull=True),
            profile__isnull=False,
            email__gt="",
        )
        refreshed = 0
        for user in stale.iterator():
            force_refresh(user)
            refreshed += 1
        self.stdout.write(self.style.SUCCESS(f"Refreshed tour status for {refreshed} user(s)."))
