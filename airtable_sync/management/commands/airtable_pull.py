"""Pull changes from Airtable into Django (inbound sync).

Fetches all records from synced Airtable tables and creates/updates Django records.
Designed to run on a cron schedule for polling-based inbound sync.

Usage:
    python manage.py airtable_pull
    python manage.py airtable_pull --model=member
    python manage.py airtable_pull --dry-run
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.management.base import BaseCommand

from airtable_sync.client import get_table
from airtable_sync.config import (
    MEMBERS_TABLE_ID,
    SPACES_TABLE_ID,
    member_from_airtable,
    space_from_airtable,
)

logger = logging.getLogger("airtable_sync")

MODEL_CHOICES = ["member", "space", "all"]


class Command(BaseCommand):
    help = "Pull changes from Airtable into Django for synced models."

    def add_arguments(self, parser: object) -> None:  # type: ignore[override]
        parser.add_argument("--model", choices=MODEL_CHOICES, default="all", help="Which model to pull.")  # type: ignore[attr-defined]
        parser.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes.")  # type: ignore[attr-defined]

    def handle(self, *args: object, **options: object) -> None:
        model = options["model"]
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be made"))

        models_to_pull = ["member", "space"] if model == "all" else [model]

        for m in models_to_pull:
            if m == "member":
                self._pull_members(dry_run)
            elif m == "space":
                self._pull_spaces(dry_run)

    def _pull_members(self, dry_run: object) -> None:
        from membership.models import Member, MembershipPlan

        self.stdout.write(self.style.MIGRATE_HEADING("Pulling Members from Airtable"))
        table = get_table(MEMBERS_TABLE_ID)
        at_records = table.all()
        self.stdout.write(f"  Found {len(at_records)} Airtable records")

        default_plan = MembershipPlan.objects.first()
        results = {"created": 0, "updated": 0, "skipped": 0}
        at_record_ids = set()

        for rec in at_records:
            record_id = rec["id"]
            at_record_ids.add(record_id)
            django_kwargs = member_from_airtable(rec["fields"])
            self._upsert_member(Member, record_id, django_kwargs, default_plan, dry_run, results)

        self._report_orphaned_members(Member, at_record_ids)
        self.stdout.write(
            f"  Results: created={results['created']}, updated={results['updated']}, skipped={results['skipped']}"
        )

    def _upsert_member(
        self,
        model: type,
        record_id: str,
        django_kwargs: dict[str, Any],
        default_plan: Any,
        dry_run: object,
        results: dict[str, int],
    ) -> None:
        existing = model.objects.filter(airtable_record_id=record_id).first()  # type: ignore[attr-defined]  # dynamic model arg
        if existing:
            self._update_instance(existing, django_kwargs, dry_run, label=f"Member {existing.display_name}")
            results["updated"] += 1
            return

        email = django_kwargs.get("_pre_signup_email", "").strip().lower()
        existing_by_email = model.objects.filter(_pre_signup_email__iexact=email).first() if email else None  # type: ignore[attr-defined]  # dynamic model arg
        if existing_by_email:
            if not dry_run:
                self._update_instance(
                    existing_by_email, django_kwargs, dry_run=False, label=f"Member {existing_by_email.display_name}"
                )
                existing_by_email.airtable_record_id = record_id
                existing_by_email._skip_airtable_sync = True  # type: ignore[attr-defined]
                existing_by_email.save()
            results["updated"] += 1
            return

        if not email:
            logger.info("Skipping Airtable record %s (no email): %s", record_id, django_kwargs.get("full_legal_name"))
            results["skipped"] += 1
            return

        if not dry_run and default_plan:
            django_kwargs["airtable_record_id"] = record_id
            django_kwargs["membership_plan"] = default_plan
            instance = model(**django_kwargs)
            instance._skip_airtable_sync = True  # type: ignore[attr-defined]
            instance.save()
            logger.info("Created Member from Airtable: %s (at=%s)", instance.display_name, record_id)
        results["created"] += 1

    def _report_orphaned_members(self, model: type, at_record_ids: set[str]) -> None:
        orphaned = model.objects.filter(  # type: ignore[attr-defined]  # dynamic model arg
            airtable_record_id__isnull=False,
        ).exclude(airtable_record_id__in=at_record_ids)
        orphan_count = orphaned.count()
        if orphan_count > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"  {orphan_count} Django Members have AT record IDs not found in Airtable (possible deletions)"
                )
            )
            for m in orphaned[:10]:
                self.stdout.write(f"    - {m.display_name} (pk={m.pk}, at={m.airtable_record_id})")

    def _pull_spaces(self, dry_run: object) -> None:
        from membership.models import Space

        self.stdout.write(self.style.MIGRATE_HEADING("Pulling Spaces from Airtable"))
        table = get_table(SPACES_TABLE_ID)
        at_records = table.all()
        self.stdout.write(f"  Found {len(at_records)} Airtable records")

        results = {"created": 0, "updated": 0, "skipped": 0}

        for rec in at_records:
            record_id = rec["id"]
            django_kwargs = space_from_airtable(rec["fields"])
            self._upsert_space(Space, record_id, django_kwargs, dry_run, results)

        self.stdout.write(
            f"  Results: created={results['created']}, updated={results['updated']}, skipped={results['skipped']}"
        )

    def _upsert_space(
        self,
        model: type,
        record_id: str,
        django_kwargs: dict[str, Any],
        dry_run: object,
        results: dict[str, int],
    ) -> None:
        existing = model.objects.filter(airtable_record_id=record_id).first()  # type: ignore[attr-defined]  # dynamic model arg
        if existing:
            self._update_instance(existing, django_kwargs, dry_run, label=f"Space {existing.space_id}")
            results["updated"] += 1
            return

        space_id = django_kwargs.get("space_id", "")
        existing_by_code = model.objects.filter(space_id=space_id).first() if space_id else None  # type: ignore[attr-defined]  # dynamic model arg
        if existing_by_code:
            if not dry_run:
                self._update_instance(
                    existing_by_code, django_kwargs, dry_run=False, label=f"Space {existing_by_code.space_id}"
                )
                existing_by_code.airtable_record_id = record_id
                existing_by_code._skip_airtable_sync = True  # type: ignore[attr-defined]
                existing_by_code.save()
            results["updated"] += 1
        else:
            results["skipped"] += 1
            self.stdout.write(
                self.style.WARNING(f"  Skipping AT Space {record_id} ({space_id}) — no matching Django record")
            )

    def _update_instance(self, instance: Any, kwargs: dict[str, Any], dry_run: object, label: str = "") -> None:
        """Update a Django model instance, logging each field that actually changed."""
        changes: list[str] = []
        for k, v in kwargs.items():
            old_value = getattr(instance, k, None)
            if old_value != v:
                changes.append(f"{k}: {old_value!r} -> {v!r}")
                if not dry_run:
                    setattr(instance, k, v)

        if changes:
            change_log = "; ".join(changes)
            logger.info("Updated %s: %s", label, change_log)
            self.stdout.write(f"  Updated {label}:")
            for change in changes:
                self.stdout.write(f"    {change}")
        else:
            logger.debug("No changes for %s", label)

        if not dry_run and changes:
            instance._skip_airtable_sync = True  # type: ignore[attr-defined]
            instance.save()
