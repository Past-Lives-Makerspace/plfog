"""One-shot management command to invite a user and immediately grant them admin role."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from allauth.account.models import EmailAddress

from core.models import Invite
from membership.models import Member


class Command(BaseCommand):
    """Invite an email address as an admin member, or promote an existing one."""

    help = "Create an admin invite (or promote an existing member) for the given email(s)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("emails", nargs="+", type=str, help="Email addresses to invite as admin.")

    def handle(self, *args: Any, **options: Any) -> None:
        for email in options["emails"]:
            self._process(email)

    def _find_member(self, email: str) -> Member | None:
        """Look up a Member by pre-signup email or linked allauth EmailAddress."""
        try:
            return Member.objects.get(_pre_signup_email__iexact=email)
        except Member.DoesNotExist:
            pass
        try:
            ea = EmailAddress.objects.select_related("user__member").get(email__iexact=email)
            return ea.user.member
        except (EmailAddress.DoesNotExist, AttributeError):
            pass
        return None

    def _process(self, email: str) -> None:
        existing = self._find_member(email)

        if existing is None:
            # New user — create invite placeholder and send invite email.
            try:
                invite = Invite.create_and_send(email=email, invited_by=None)
                member = invite.member
                self.stdout.write(f"  Created invite for {email} and sent invite email.")
            except ValueError:
                # Invite already exists — find it and resend.
                invite = Invite.objects.get(email__iexact=email)
                invite.send_invite_email()
                member = invite.member
                self.stdout.write(f"  Resent invite email to {email}.")
        else:
            member = existing
            # Resend invite if they haven't signed up yet.
            if hasattr(member, "invite") and member.invite.is_pending:
                member.invite.send_invite_email()
                self.stdout.write(f"  Found existing member, resent invite email to {email}.")
            else:
                self.stdout.write(f"  Found existing member: {member}")

        if member is None:
            self.stderr.write(f"  ERROR: could not resolve member for {email}. Skipping.")
            return

        old_role = member.fog_role
        member.fog_role = Member.FogRole.ADMIN
        member.save(update_fields=["fog_role"])
        member.sync_user_permissions()

        self.stdout.write(self.style.SUCCESS(f"  {email} — fog_role: {old_role} → {member.fog_role}"))
