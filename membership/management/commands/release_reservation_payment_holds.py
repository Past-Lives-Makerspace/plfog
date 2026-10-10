"""Release equipment time held by reservation checkouts that were never completed (Stripe-verified, #749)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from membership import equipment


class Command(BaseCommand):
    help = (
        "Sweep unpaid equipment reservation holds older than two hours: verify each with Stripe, "
        "release confirmed-unpaid holds, and recover paid ones whose webhook was lost."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        released, recovered = equipment.expire_payment_holds()
        self.stdout.write(f"Released {released} hold(s); recovered {recovered} paid reservation(s).")
