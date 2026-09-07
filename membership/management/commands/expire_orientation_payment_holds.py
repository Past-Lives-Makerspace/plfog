"""Release orientation seats held by checkouts that were never completed (Stripe-verified)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from membership import orientations


class Command(BaseCommand):
    help = (
        "Sweep PENDING_PAYMENT orientation holds older than two hours: verify each with Stripe, "
        "delete confirmed-unpaid holds, and recover paid ones whose webhook was lost."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        released, recovered = orientations.expire_payment_holds()
        self.stdout.write(f"Released {released} hold(s); recovered {recovered} paid booking(s).")
