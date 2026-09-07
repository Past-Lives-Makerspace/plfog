"""Safely assign or clear a guild's lead, with validation warnings.

Guild was removed from the Django admin (v1.6.0); this command and the
admin-only Guild Lead control on the guild editor's Staff tab are the supported
ways to set ``Guild.guild_lead``. Assigning the FK is all that's needed
— guild-lead perks flow from it, no FOG role required. The command warns when
the chosen member has no linked User (they can't log in to use the perks until
they sign up with a matching email) or is not Active.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from membership.models import Guild, Member


class Command(BaseCommand):
    help = "Assign or clear a guild's lead (Guild.guild_lead), with validation."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--guild", required=True, help="Guild id or exact name.")
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--member", help="Email of the member to set as the guild's lead.")
        group.add_argument("--clear", action="store_true", help="Remove the guild's current lead.")

    def handle(self, *args: Any, **options: Any) -> None:
        guild = self._resolve_guild(options["guild"])

        if options["clear"]:
            previous = guild.guild_lead
            guild.guild_lead = None
            guild.save(update_fields=["guild_lead"])
            who = previous.display_name if previous is not None else "(none)"
            self.stdout.write(self.style.SUCCESS(f"Cleared lead of '{guild.name}' (was {who})."))
            return

        member = self._resolve_member(options["member"])
        warnings = guild.assign_lead(member)
        self.stdout.write(
            self.style.SUCCESS(
                f"Set lead of '{guild.name}' to {member.display_name} <{member.primary_email or 'no email'}>."
            )
        )
        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"  Warning: {warning}"))

    def _resolve_guild(self, ref: str) -> Guild:
        if ref.isdigit():
            try:
                return Guild.objects.get(pk=int(ref))
            except Guild.DoesNotExist:
                raise CommandError(f"No guild with id {ref}.")
        try:
            return Guild.objects.get(name=ref)
        except Guild.DoesNotExist:
            raise CommandError(f"No guild named '{ref}'.")

    def _resolve_member(self, email: str) -> Member:
        member = (
            Member.objects.filter(user__email__iexact=email).first()
            or Member.objects.filter(_pre_signup_email__iexact=email).first()
        )
        if member is None:
            raise CommandError(f"No member found with email '{email}'.")
        return member
