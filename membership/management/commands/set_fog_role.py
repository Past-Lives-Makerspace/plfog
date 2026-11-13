"""Management command to set a member's FOG role."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from membership.models import Member


class Command(BaseCommand):
    """Set the fog_role for a member identified by email."""

    help = "Set a member's FOG role (member, admin)."

    def add_arguments(self, parser: CommandParser) -> None:
        """Define positional arguments."""
        parser.add_argument("email", type=str, help="Email address of the member.")
        parser.add_argument(
            "role",
            type=str,
            choices=[c.value for c in Member.FogRole],
            help="FOG role to assign.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        email: str = options["email"]
        role: str = options["role"]

        try:
            member = Member.objects.get(_pre_signup_email=email)
        except Member.DoesNotExist:
            raise CommandError(f"No member found with email '{email}'.")

        old_role = member.get_fog_role_display()
        member.fog_role = role
        member.save(update_fields=["fog_role"])
        # A demotion takes the Admin badge back, as the portal's role setters do (#1167).
        member.withdraw_admin_setup_unless_admin()

        new_role = member.get_fog_role_display()
        self.stdout.write(f"{member} — fog_role changed from '{old_role}' to '{new_role}'.")
