"""Show, and with ``--apply`` make, the Discord leadership role changes the app wants (#1148).

Dry run by default: for every linked member whose managed roles differ from what the app says
they should hold, one line with the roles it would add and remove, by name. A server member
who holds a managed role but whom no linked member answers to (they unlinked, their account
was deleted, or they never linked) gets a line too, with their Discord username, marked
"(matches unlinked app member X)" when that username is an unlinked member's typed Discord
handle. ``--apply`` leaves those people alone; ``--apply --strip-unlinked`` also takes their
managed roles off, except from the matched ones, who may be real leaders yet to link.
Members who should hold a role but have no linked Discord are listed once at the end.
``--apply`` makes the changes and prints how many roles were added and removed, and how many
calls failed. Reads the server's member list once (one request per thousand members), never one
request per member. This is how the drift from before the sync gets cleaned up, and how an
admin checks the role ids are right.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from core.events.discord_dm import bot_disabled
from core.events.discord_leadership_roles import (
    NO_FACTS,
    LeadershipFacts,
    LeadershipRoleSettings,
    RoleChanges,
    desired_role_ids,
    leadership_facts,
    plan_changes,
    reconcile_member,
    server_id,
    strip_managed_roles,
)
from core.events.discord_roles import ServerMember, server_members
from membership.models import Member


@dataclass
class _Tally:
    differing: int = 0
    added: int = 0
    removed: int = 0
    failed: int = 0

    def count(self, changes: RoleChanges) -> None:
        self.added += len(changes.added)
        self.removed += len(changes.removed)
        self.failed += len(changes.failed)


class Command(BaseCommand):
    help = "List the Discord leadership role changes the app wants; --apply makes them."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--apply", action="store_true", help="Make the changes instead of listing them.")
        parser.add_argument(
            "--strip-unlinked",
            action="store_true",
            help="With --apply, also take managed roles off server members no linked member answers to "
            "(never one whose username matches an unlinked member's Discord handle).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        server = server_id()
        settings = LeadershipRoleSettings.load()
        off = self._off_reason(server, settings)
        if off:
            self.stdout.write(off)
            return
        if options["strip_unlinked"] and not options["apply"]:
            raise CommandError("--strip-unlinked takes roles off people, so it needs --apply.")
        members = server_members(server, retry=True)
        if members is None:
            raise CommandError("Could not read the Discord server's member list; see the log for why.")
        roster = {user_id: member.roles for user_id, member in members.items()}

        apply = options["apply"]
        tally = _Tally()
        unlinked: list[Member] = []
        facts = leadership_facts()
        for member in self._candidates(facts, roster, settings):
            member_facts = facts.get(member.pk, NO_FACTS)
            user_id = (member.discord_user_id or "").strip()
            if not user_id:
                if desired_role_ids(member_facts, settings):
                    unlinked.append(member)
                continue
            current = roster.get(user_id)
            if current is None:
                self.stdout.write(f"{member.display_name} ({user_id}): not on the Discord server, skipped.")
                continue
            to_add, to_remove = plan_changes(member_facts, current, settings)
            if to_add or to_remove:
                tally.differing += 1
                self.stdout.write(f"{member.display_name} ({user_id}): {self._describe(to_add, to_remove, settings)}")
                if apply:
                    tally.count(reconcile_member(member, current=current, settings=settings))
        self._strangers(members, settings, tally, strip=options["strip_unlinked"])
        self._summarize(unlinked, tally, apply=apply)

    @staticmethod
    def _off_reason(server: str, settings: LeadershipRoleSettings) -> str:
        """Why nothing can be synced here, or "" when it can."""
        if not server:
            return "The Discord server id is blank in Site Settings, so the sync is off."
        if bot_disabled("leadership role sync"):
            return "The Discord bot token is blank here (unset, or staging), so the sync is off."
        if not settings.managed:
            return "No leadership, Guild Officers or guild lead role id is set, so there is nothing to sync."
        return ""

    def _strangers(
        self, members: dict[str, ServerMember], settings: LeadershipRoleSettings, tally: _Tally, *, strip: bool
    ) -> None:
        """Server members holding a managed role whom no linked member answers to; strip them when asked.

        One whose username is an unlinked member's typed Discord handle is marked and never
        stripped: they are likely a real leader who has not linked yet.
        """
        linked = set(Member.objects.exclude(discord_user_id="").values_list("discord_user_id", flat=True))
        handles: dict[str, str] = {}
        for member in Member.objects.filter(discord_user_id="").exclude(discord_handle=""):
            if _handle(member.discord_handle):
                handles.setdefault(_handle(member.discord_handle), member.display_name)
        for user_id in sorted(members):
            server_member = members[user_id]
            held = sorted(server_member.roles & settings.managed)
            if not held or user_id in linked:
                continue
            tally.differing += 1
            match = handles.get(_handle(server_member.username))
            marker = f" (matches unlinked app member {match})" if match else ""
            self.stdout.write(
                f"Discord account {server_member.username or user_id} ({user_id}), no linked member{marker}: "
                f"{self._describe([], held, settings)}"
            )
            if strip and not match:
                tally.count(strip_managed_roles(user_id, current=server_member.roles, settings=settings))

    @staticmethod
    def _candidates(
        facts: dict[int, LeadershipFacts], roster: dict[str, set[str]], settings: LeadershipRoleSettings
    ) -> list[Member]:
        """Everyone who earns a managed role or holds one on the server, by name."""
        held_managed = {user_id for user_id, roles in roster.items() if roles & settings.managed}
        members = Member.objects.filter(pk__in=list(facts)) | Member.objects.filter(discord_user_id__in=held_managed)
        return sorted(members.distinct(), key=lambda member: member.display_name.casefold())

    def _summarize(self, unlinked: list[Member], tally: _Tally, *, apply: bool) -> None:
        if unlinked:
            self.stdout.write("")
            self.stdout.write("Should hold a role but have not linked Discord:")
            for member in unlinked:
                self.stdout.write(f"  {member.display_name}")
        self.stdout.write("")
        if apply:
            self.stdout.write(self.style.SUCCESS(f"Added {tally.added} roles and removed {tally.removed}."))
            if tally.failed:
                self.stdout.write(
                    self.style.ERROR(f"{tally.failed} role changes failed; see the log, then run --apply again.")
                )
        elif tally.differing:
            self.stdout.write(f"Dry run: {tally.differing} members differ and nothing was changed. Run with --apply.")
        else:
            self.stdout.write("Every linked member's leadership roles already match.")

    @staticmethod
    def _describe(to_add: list[str], to_remove: list[str], settings: LeadershipRoleSettings) -> str:
        parts = []
        if to_add:
            parts.append("add " + ", ".join(settings.role_name(role_id) for role_id in to_add))
        if to_remove:
            parts.append("remove " + ", ".join(settings.role_name(role_id) for role_id in to_remove))
        return "; ".join(parts)


def _handle(value: str) -> str:
    """A Discord username or typed handle compared loosely: no leading @, no surrounding space, any case."""
    return value.strip().lstrip("@").strip().casefold()
