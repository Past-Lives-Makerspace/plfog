"""Release cubbies held by rent checkouts that were never completed (Stripe verified, #978)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from membership import cubby_rentals


class Command(BaseCommand):
    help = (
        "Sweep cubby holds awaiting payment for over two hours: ask Stripe about each, release the "
        "unpaid ones, and settle paid ones whose webhook was lost."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        released, recovered = cubby_rentals.release_stale_holds()
        self.stdout.write(f"Released {released} cubby hold(s); recovered {recovered} paid rental(s).")
