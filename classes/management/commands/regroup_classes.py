"""Recompute class grouping keys so duplicate dated offerings collapse into one card."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from classes.grouping import regroup_offerings


class Command(BaseCommand):
    help = "Recompute grouping keys so the same class offered on multiple dates collapses into one catalog card."

    def handle(self, *args, **options) -> None:
        offerings, groups = regroup_offerings()
        self.stdout.write(self.style.SUCCESS(f"Regrouped {offerings} offering(s) into {groups} catalog group(s)."))
