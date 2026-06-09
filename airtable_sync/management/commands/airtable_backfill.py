"""Match existing Django records with Airtable records and optionally push/pull data.

Usage:
    python manage.py airtable_backfill --direction=match --model=member
    python manage.py airtable_backfill --direction=push --model=all
    python manage.py airtable_backfill --direction=pull --model=member
"""

from __future__ import annotations

import logging
from typing import Any, cast

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from airtable_sync.client import get_table
from airtable_sync.config import (
    LEASES_TABLE_ID,
    MEMBERS_TABLE_ID,
    SPACES_TABLE_ID,
    member_from_airtable,
    member_to_airtable,
    space_from_airtable,
    space_to_airtable,
)

logger = logging.getLogger("airtable_sync")

MODEL_CHOICES = ["member", "space", "lease", "all"]
DIRECTION_CHOICES = ["match", "push", "pull"]


class Command(BaseCommand):
    help = "Sync existing Django records with Airtable: match by natural key, push, or pull."

    def add_arguments(self, parser: object) -> None:  # type: ignore[override]
        parser.add_argument("--model", choices=MODEL_CHOICES, default="all", help="Which model to sync.")  # type: ignore[attr-defined]
        parser.add_argument("--direction", choices=DIRECTION_CHOICES, default="match", help="match/push/pull.")  # type: ignore[attr-defined]
        parser.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes.")  # type: ignore[attr-defined]

    def handle(self, *args: object, **options: object) -> None:
        model = options["model"]
        direction = options["direction"]
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be made"))

        models_to_sync = MODEL_CHOICES[:-1] if model == "all" else [model]

        for m in models_to_sync:
            if m == "member":
                self._sync_members(direction, dry_run)
            elif m == "space":
                self._sync_spaces(direction, dry_run)
            elif m == "lease":
                self._sync_leases(direction, dry_run)

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    def _sync_members(self, direction: object, dry_run: object) -> None:
        from membership.models import Member

        self.stdout.write(self.style.MIGRATE_HEADING("Members"))
        table = get_table(MEMBERS_TABLE_ID)
        at_records = cast("list[dict]", table.all())
        self.stdout.write(f"  Found {len(at_records)} Airtable records")

        at_by_email = _build_email_lookup(at_records)
        results = {"matched": 0, "created": 0, "updated": 0, "skipped": 0}

        if direction == "match":
            self._match_members(Member, at_by_email, dry_run, results)
        elif direction == "push":
            self._push_members(Member, table, dry_run, results)
        elif direction == "pull":
            self._pull_members(at_records, table, dry_run, results)

        self._print_results(results)

    def _match_members(
        self, model: type, at_by_email: dict[str, dict], dry_run: object, results: dict[str, int]
    ) -> None:
        for member in model.objects.select_related("membership_plan").all():  # type: ignore[attr-defined]  # dynamic model arg
            if member.airtable_record_id:
                results["skipped"] += 1
                continue
            email = member._pre_signup_email.strip().lower()
            if email and email in at_by_email:
                rec = at_by_email[email]
                if not dry_run:
                    model.objects.filter(pk=member.pk).update(airtable_record_id=rec["id"])  # type: ignore[attr-defined]  # dynamic model arg
                results["matched"] += 1
                self.stdout.write(f"  Matched: {member.display_name} -> {rec['id']}")
            else:
                results["skipped"] += 1

    def _push_members(self, model: type, table: Any, dry_run: object, results: dict[str, int]) -> None:
        for member in model.objects.select_related("membership_plan").all():  # type: ignore[attr-defined]  # dynamic model arg
            fields = member_to_airtable(member)
            if member.airtable_record_id:
                if not dry_run:
                    table.update(member.airtable_record_id, fields)
                results["updated"] += 1
            else:
                if not dry_run:
                    rec = table.create(fields)
                    model.objects.filter(pk=member.pk).update(airtable_record_id=rec["id"])  # type: ignore[attr-defined]  # dynamic model arg
                results["created"] += 1

    def _pull_members(self, at_records: list[dict], table: Any, dry_run: object, results: dict[str, int]) -> None:
        from membership.models import Member, MembershipPlan

        default_plan = MembershipPlan.objects.first()
        for rec in at_records:
            record_id = rec["id"]
            django_kwargs = member_from_airtable(rec["fields"])

            existing = Member.objects.filter(airtable_record_id=record_id).first()
            if existing:
                _update_instance(existing, django_kwargs, dry_run)
                results["updated"] += 1
                continue

            email = django_kwargs.get("_pre_signup_email", "").strip().lower()
            existing_by_email = Member.objects.filter(_pre_signup_email__iexact=email).first() if email else None
            if existing_by_email:
                if not dry_run:
                    _update_instance(existing_by_email, django_kwargs, dry_run=False)
                    existing_by_email.airtable_record_id = record_id
                    existing_by_email._skip_airtable_sync = True  # type: ignore[attr-defined]
                    existing_by_email.save()
                results["matched"] += 1
            elif not dry_run and default_plan:
                django_kwargs["airtable_record_id"] = record_id
                django_kwargs["membership_plan"] = default_plan
                member = Member(**django_kwargs)
                member._skip_airtable_sync = True  # type: ignore[attr-defined]
                member.save()
                results["created"] += 1
            else:
                results["created"] += 1

    # ------------------------------------------------------------------
    # Spaces
    # ------------------------------------------------------------------

    def _sync_spaces(self, direction: object, dry_run: object) -> None:
        from membership.models import Space

        self.stdout.write(self.style.MIGRATE_HEADING("Spaces"))
        table = get_table(SPACES_TABLE_ID)
        at_records = cast("list[dict]", table.all())
        self.stdout.write(f"  Found {len(at_records)} Airtable records")

        results = {"matched": 0, "created": 0, "updated": 0, "skipped": 0}

        if direction == "match":
            self._match_spaces(Space, at_records, dry_run, results)
        elif direction == "push":
            self._push_spaces(Space, table, dry_run, results)
        elif direction == "pull":
            self._pull_spaces(Space, at_records, dry_run, results)

        self._print_results(results)

    def _match_spaces(self, model: type, at_records: list[dict], dry_run: object, results: dict[str, int]) -> None:
        at_by_code: dict[str, dict] = {}
        for rec in at_records:
            code = (rec["fields"].get("Space Code") or "").strip()
            if code:
                at_by_code[code] = rec

        for space in model.objects.all():  # type: ignore[attr-defined]  # dynamic model arg
            if space.airtable_record_id:
                results["skipped"] += 1
                continue
            if space.space_id in at_by_code:
                rec = at_by_code[space.space_id]
                if not dry_run:
                    model.objects.filter(pk=space.pk).update(airtable_record_id=rec["id"])  # type: ignore[attr-defined]  # dynamic model arg
                results["matched"] += 1
                self.stdout.write(f"  Matched: {space.space_id} -> {rec['id']}")
            else:
                results["skipped"] += 1

    def _push_spaces(self, model: type, table: Any, dry_run: object, results: dict[str, int]) -> None:
        for space in model.objects.all():  # type: ignore[attr-defined]  # dynamic model arg
            fields = space_to_airtable(space)
            if space.airtable_record_id:
                if not dry_run:
                    table.update(space.airtable_record_id, fields)
                results["updated"] += 1
            else:
                if not dry_run:
                    rec = table.create(fields)
                    model.objects.filter(pk=space.pk).update(airtable_record_id=rec["id"])  # type: ignore[attr-defined]  # dynamic model arg
                results["created"] += 1

    def _pull_spaces(self, model: type, at_records: list[dict], dry_run: object, results: dict[str, int]) -> None:
        for rec in at_records:
            record_id = rec["id"]
            django_kwargs = space_from_airtable(rec["fields"])

            existing = model.objects.filter(airtable_record_id=record_id).first()  # type: ignore[attr-defined]  # dynamic model arg
            if existing:
                _update_instance(existing, django_kwargs, dry_run)
                results["updated"] += 1
                continue

            space_id = django_kwargs.get("space_id", "")
            existing_by_code = model.objects.filter(space_id=space_id).first() if space_id else None  # type: ignore[attr-defined]  # dynamic model arg
            if existing_by_code:
                if not dry_run:
                    _update_instance(existing_by_code, django_kwargs, dry_run=False)
                    existing_by_code.airtable_record_id = record_id
                    existing_by_code._skip_airtable_sync = True  # type: ignore[attr-defined]
                    existing_by_code.save()
                results["matched"] += 1
            else:
                results["skipped"] += 1
                self.stdout.write(self.style.WARNING(f"  No Django Space for AT record {record_id} ({space_id})"))

    # ------------------------------------------------------------------
    # Leases
    # ------------------------------------------------------------------

    def _sync_leases(self, direction: object, dry_run: object) -> None:
        from membership.models import Lease, Member

        self.stdout.write(self.style.MIGRATE_HEADING("Leases"))
        table = get_table(LEASES_TABLE_ID)
        at_records = cast("list[dict]", table.all())
        self.stdout.write(f"  Found {len(at_records)} Airtable records")

        results = {"matched": 0, "skipped": 0}

        if direction == "match":
            member_ct = ContentType.objects.get_for_model(Member)
            django_leases = Lease.objects.filter(content_type=member_ct).select_related("space")
            at_lookup = _build_lease_lookup(at_records)

            for lease in django_leases:
                if lease.airtable_record_id:
                    results["skipped"] += 1
                    continue
                tenant = lease.tenant
                if not tenant or not getattr(tenant, "airtable_record_id", None):
                    results["skipped"] += 1
                    continue
                if not lease.space.airtable_record_id:
                    results["skipped"] += 1
                    continue
                key = (tenant.airtable_record_id, lease.space.airtable_record_id, str(lease.start_date))
                if key in at_lookup:
                    if not dry_run:
                        Lease.objects.filter(pk=lease.pk).update(airtable_record_id=at_lookup[key])
                    results["matched"] += 1
                    self.stdout.write(f"  Matched: Lease pk={lease.pk} -> {at_lookup[key]}")
                else:
                    results["skipped"] += 1
        else:
            self.stdout.write(self.style.WARNING("  Lease push/pull not yet implemented. Use --direction=match."))

        self.stdout.write(f"  Results: matched={results['matched']}, skipped={results['skipped']}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _print_results(self, results: dict[str, int]) -> None:
        parts = [f"{k}={v}" for k, v in results.items()]
        self.stdout.write(f"  Results: {', '.join(parts)}")


def _build_email_lookup(at_records: list[dict]) -> dict[str, dict]:
    """Build a lookup of AT records by lowercase email."""
    lookup: dict[str, dict] = {}
    for rec in at_records:
        email = (rec["fields"].get("Email") or "").strip().lower()
        if email:
            lookup[email] = rec
    return lookup


def _build_lease_lookup(at_records: list[dict]) -> dict[tuple, str]:
    """Build a lookup of AT lease records by (member_id, space_id, start_date)."""
    lookup: dict[tuple, str] = {}
    for rec in at_records:
        fields = rec["fields"]
        member_ids = fields.get("Member", [])
        space_ids = fields.get("Space", [])
        start = fields.get("Start Date", "")
        if member_ids and space_ids and start:
            lookup[(member_ids[0], space_ids[0], start)] = rec["id"]
    return lookup


def _update_instance(instance: Any, kwargs: dict[str, Any], dry_run: object) -> None:
    """Update a Django model instance with kwargs and save with sync disabled."""
    if dry_run:
        return
    for k, v in kwargs.items():
        setattr(instance, k, v)
    instance._skip_airtable_sync = True  # type: ignore[attr-defined]
    instance.save()
