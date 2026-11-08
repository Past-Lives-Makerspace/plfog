"""Decline paid orientation requests left unconfirmed for 6 days and release their card holds (#1128)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from membership import orientations


class Command(BaseCommand):
    help = (
        "Decline orientation requests whose card hold has waited 6 days for an Orientor, releasing the hold "
        "a day before Stripe's 7 day limit and emailing the member, then retry any hold Stripe did not release."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        declined, released = orientations.release_unconfirmed_holds()
        self.stdout.write(f"Declined {declined} unconfirmed request(s); released {released} leftover hold(s).")
