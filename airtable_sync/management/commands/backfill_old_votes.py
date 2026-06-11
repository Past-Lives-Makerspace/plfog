"""Backfill VotePreference records from the old 'Past Lives Information' Airtable base.

Fetches Guild Votes from the legacy base (appETKQa6ueJsZ2gC), takes the latest vote per
member (by Airtable createdTime), matches members by display name, resolves guild names,
and creates VotePreference rows — skipping members who already have one.

Usage:
    python manage.py backfill_old_votes --dry-run   # preview matches
    python manage.py backfill_old_votes              # actually create records
"""

from __future__ import annotations

import logging
from typing import Any, cast

from django.core.management.base import BaseCommand

logger = logging.getLogger("airtable_sync")

OLD_BASE_ID = "appETKQa6ueJsZ2gC"
OLD_GUILDS_TABLE_ID = "tbla02m2GnUAsg3eW"
OLD_GUILD_VOTES_TABLE_ID = "tblbIhlUK55xeqISc"

OLD_TO_NEW_GUILD_NAME: dict[str, str] = {
    "art framing": "Art Framing Guild",
    "ceramics": "Ceramics Guild",
    "community garden": "Gardeners Guild",
    "food independence": "Food Independence Guild",
    "gallery & retail": "Gallery & Retail Guild",
    "jewelry": "Jewelry Guild",
    "leatherworking": "Leatherwork Guild",
    "metalworkers": "Metalworkers Guild",
    "prison outreach": "Prison Outreach Guild",
    "stained glass": "Glass Guild",
    "textile arts": "Textiles Guild",
    "visual arts": "Visual Arts Guild",
    "woodworkers": "Woodworking Guild",
    "writing guild": "Writers Guild",
}


def _resolve_guild_names(fields: dict[str, Any], guild_id_to_name: dict[str, str]) -> list[str] | None:
    """Extract 3 guild names from a vote record's fields. Returns None if fewer than 3."""
    p1_ids = fields.get("Guild Preference 1", [])
    p2_ids = fields.get("Guild Preference 2", [])
    p3_ids = fields.get("Guild Preference 3", [])

    if p1_ids and p2_ids and p3_ids:
        return [
            guild_id_to_name.get(p1_ids[0], ""),
            guild_id_to_name.get(p2_ids[0], ""),
            guild_id_to_name.get(p3_ids[0], ""),
        ]

    multi = fields.get("Guild Rankings Multiselect", [])
    if len(multi) >= 3:
        return [
            guild_id_to_name.get(multi[0], ""),
            guild_id_to_name.get(multi[1], ""),
            guild_id_to_name.get(multi[2], ""),
        ]

    return None


def _deduplicate_votes(records: list[dict]) -> dict[str, dict]:
    """Keep the latest vote per member name (by Airtable createdTime)."""
    latest_by_name: dict[str, dict] = {}
    for rec in records:
        name = rec["fields"].get("Name", "").strip()
        if not name:
            continue
        created = rec["createdTime"]
        if name not in latest_by_name or created > latest_by_name[name]["createdTime"]:
            latest_by_name[name] = rec
    return latest_by_name


class Command(BaseCommand):
    help = "Backfill VotePreference from old Airtable Guild Votes table."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Preview without making changes.")

    def handle(self, *args: object, **options: object) -> None:
        from django.conf import settings
        from pyairtable import Api

        from membership.models import Guild, Member, VotePreference

        dry_run = bool(options["dry_run"])
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be made\n"))

        api = Api(settings.AIRTABLE_API_TOKEN)

        # Fetch old guilds to build record_id -> name map
        self.stdout.write(self.style.MIGRATE_HEADING("Fetching guilds from old base"))
        old_guild_records = api.table(OLD_BASE_ID, OLD_GUILDS_TABLE_ID).all()
        guild_id_to_name = {
            rec["id"]: rec["fields"]["Guild"] for rec in old_guild_records if rec["fields"].get("Guild")
        }
        self.stdout.write(f"  Loaded {len(guild_id_to_name)} guilds from old base")

        # Fetch and deduplicate old vote records
        self.stdout.write(self.style.MIGRATE_HEADING("Fetching votes from old base"))
        old_vote_records = cast("list[dict]", api.table(OLD_BASE_ID, OLD_GUILD_VOTES_TABLE_ID).all())
        self.stdout.write(f"  Loaded {len(old_vote_records)} vote records")

        latest_by_name = _deduplicate_votes(old_vote_records)
        self.stdout.write(f"  Deduplicated to {len(latest_by_name)} unique members (latest vote each)")

        # Build Django lookup maps
        django_guilds = {g.name.lower(): g for g in Guild.objects.all()}
        django_members = {m.display_name.strip().lower(): m for m in Member.objects.all()}
        existing_voter_ids = set(VotePreference.objects.values_list("member_id", flat=True))
        self.stdout.write(f"  Django has {len(django_guilds)} guilds, {len(django_members)} members")

        # Process each vote
        self.stdout.write(self.style.MIGRATE_HEADING("Processing votes"))
        stats = self._process_votes(
            latest_by_name, guild_id_to_name, django_guilds, django_members, existing_voter_ids, dry_run
        )

        self.stdout.write(self.style.MIGRATE_HEADING("Summary"))
        for k, v in stats.items():
            self.stdout.write(f"  {k}: {v}")

    def _process_votes(
        self,
        latest_by_name: dict[str, dict],
        guild_id_to_name: dict[str, str],
        django_guilds: dict[str, Any],
        django_members: dict[str, Any],
        existing_voter_ids: set[int],
        dry_run: bool,
    ) -> dict[str, int]:
        from membership.models import Member, VotePreference

        stats = {
            "created": 0,
            "skipped_existing": 0,
            "skipped_no_member": 0,
            "skipped_no_guild": 0,
            "skipped_former": 0,
        }

        for name, rec in sorted(latest_by_name.items()):
            guild_names = _resolve_guild_names(rec["fields"], guild_id_to_name)
            if not guild_names:
                self.stdout.write(self.style.WARNING(f"  SKIP (no 3 guilds): {name}"))
                stats["skipped_no_guild"] += 1
                continue

            guild_objs = self._lookup_guilds(guild_names, django_guilds, name)
            if not guild_objs:
                stats["skipped_no_guild"] += 1
                continue

            member = django_members.get(name.strip().lower())
            if not member:
                self.stdout.write(self.style.WARNING(f"  SKIP (member not found): {name}"))
                stats["skipped_no_member"] += 1
                continue

            if member.status == Member.Status.FORMER:
                self.stdout.write(f"  SKIP (former member): {name}")
                stats["skipped_former"] += 1
                continue

            if member.pk in existing_voter_ids:
                self.stdout.write(f"  SKIP (already voted): {name}")
                stats["skipped_existing"] += 1
                continue

            self.stdout.write(
                self.style.SUCCESS(
                    f"  CREATE: {name} -> {guild_objs[0].name} / {guild_objs[1].name} / {guild_objs[2].name}"
                )
            )
            if not dry_run:
                vote = VotePreference(
                    member=member, guild_1st=guild_objs[0], guild_2nd=guild_objs[1], guild_3rd=guild_objs[2]
                )
                vote._skip_airtable_sync = True  # type: ignore[attr-defined]
                vote.save()
                existing_voter_ids.add(member.pk)
            stats["created"] += 1

        return stats

    def _lookup_guilds(
        self, guild_names: list[str], django_guilds: dict[str, Any], member_name: str
    ) -> list[Any] | None:
        """Resolve old guild name strings to current Django Guild objects via name mapping."""
        result = []
        for gn in guild_names:
            if not gn:
                self.stdout.write(self.style.WARNING(f"  SKIP (blank guild name resolved from AT): {member_name}"))
                return None
            resolved = OLD_TO_NEW_GUILD_NAME.get(gn.lower(), gn)
            g = django_guilds.get(resolved.lower())
            if not g:
                self.stdout.write(self.style.WARNING(f"  SKIP (no current guild for {gn!r}): {member_name}"))
                return None
            result.append(g)
        return result
