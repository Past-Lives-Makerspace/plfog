"""Release class seats held by checkouts that were never completed (Stripe-verified)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from classes.models import Registration


class Command(BaseCommand):
    help = (
        "Sweep PENDING class signups older than two hours: verify each with Stripe, cancel the "
        "ones whose checkout is dead, and confirm the ones whose payment webhook was lost."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        released, recovered = Registration.objects.release_abandoned_holds()
        self.stdout.write(f"Released {released} seat(s); recovered {recovered} paid signup(s).")
