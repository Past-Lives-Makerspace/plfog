"""Backfill: give every userless ACTIVE Member a linked User (silent, idempotent).

Run manually at deploy (like ``seed_notification_templates``) — NOT in the Render
build command. Running it against prod mints a verified, login-capable account for
each ACTIVE member who never signed up (the owner's explicit Decision 3 / Risk R5).

It is silent (sends zero email — see ``provision_user_for_member``), idempotent
(already-linked members are out of scope), and resilient (a bad row is skipped +
logged, never killing the batch). ``--dry-run`` reports the plan and writes nothing.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from django.core.management.base import BaseCommand
from django.db import DataError, IntegrityError

from membership.models import Member
from membership.services.provisioning import provision_user_for_member

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Provision a passwordless User + verified primary email for every userless ACTIVE Member (silent)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would happen (provision / skip-blank / skip-duplicate) without writing anything.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run: bool = options["dry_run"]

        # ACTIVE only (Review fix #1): skip INVITED placeholders (provisioning would
        # bypass invite acceptance) and FORMER/SUSPENDED (don't resurrect them).
        scope = list(Member.objects.filter(user__isnull=True, status=Member.Status.ACTIVE).order_by("pk"))

        duplicate_member_ids = self._duplicate_member_ids(scope)

        provisioned = skipped_blank = skipped_duplicate = skipped_error = 0
        prefix = "[dry-run] " if dry_run else ""

        for member in scope:
            email = (member._pre_signup_email or "").strip().lower()
            if not email:
                skipped_blank += 1
                self.stdout.write(f"  {prefix}SKIP (no email): {member.display_name} (pk={member.pk})")
                continue
            if member.pk in duplicate_member_ids:
                skipped_duplicate += 1
                self.stdout.write(f"  {prefix}SKIP (duplicate email {email}): {member.display_name} (pk={member.pk})")
                continue
            if dry_run:
                provisioned += 1
                self.stdout.write(f"  {prefix}PROVISION: {member.display_name} (pk={member.pk}) -> {email}")
                continue
            if self._provision(member, email):
                provisioned += 1
            else:
                skipped_error += 1

        self._summary(dry_run, provisioned, skipped_blank, skipped_duplicate, skipped_error)

    def _duplicate_member_ids(self, scope: list[Member]) -> set[int]:
        """Member pks whose ``_pre_signup_email`` is shared by another member in scope.

        Pre-flight (Review fix #2): without a unique constraint, two userless members
        can share an email. Provisioning the second would try to claim the first's
        User (a OneToOne collision). Report ALL members of a duplicate set as skips
        rather than crash mid-batch on ``MultipleObjectsReturned``.
        """
        by_email: dict[str, list[int]] = defaultdict(list)
        for member in scope:
            email = (member._pre_signup_email or "").strip().lower()
            if email:
                by_email[email].append(member.pk)
        return {pk for pks in by_email.values() if len(pks) > 1 for pk in pks}

    def _provision(self, member: Member, email: str) -> bool:
        """Provision one member; return True on success, False on a skip/error.

        Username-length and integrity failures are caught so one bad row never kills
        the batch (Review fix #4). ``provision_user_for_member`` returning ``None``
        means it declined (e.g. the email turned out to belong to another member).
        """
        try:
            user = provision_user_for_member(member)
        except (IntegrityError, DataError) as exc:
            logger.warning("Failed to provision member %s (pk=%s): %s", member.display_name, member.pk, exc)
            self.stdout.write(self.style.WARNING(f"  SKIP (error): {member.display_name} (pk={member.pk}): {exc}"))
            return False
        if user is None:
            self.stdout.write(
                self.style.WARNING(f"  SKIP (could not provision): {member.display_name} (pk={member.pk})")
            )
            return False
        return True

    def _summary(
        self,
        dry_run: bool,
        provisioned: int,
        skipped_blank: int,
        skipped_duplicate: int,
        skipped_error: int,
    ) -> None:
        lead = "Would provision" if dry_run else "Provisioned"
        message = (
            f"{lead} {provisioned} users, skipped {skipped_blank} (no email), "
            f"{skipped_duplicate} (duplicate email), {skipped_error} (errors)."
        )
        self.stdout.write(self.style.SUCCESS(message))
