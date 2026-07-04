"""Grid data for the "My Guilds" settings tab (§5).

The Guilds tab is a service-built grid — one on/off row per active guild, pre-checked
for the guilds the member is officially in — mirroring the shape of
``core.events.settings_matrix.build_matrix`` (grid data built in the service layer, out
of the view). "Official membership" is the existing ``GuildMembership`` row; joining
creates it and leaving deletes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from membership.models import Guild

if TYPE_CHECKING:
    from membership.models import Member


@dataclass(frozen=True)
class GuildToggleRow:
    """One active guild plus whether this member is officially in it."""

    guild: Guild
    joined: bool
    meeting_hint: str


def build_my_guilds_rows(member: Member | None) -> list[GuildToggleRow]:
    """All active guilds, each flagged joined-or-not for ``member`` (ordered by name).

    Two queries, no N+1: one for the active guilds, one for the member's joined guild-id
    set. Soft-deleted / inactive guilds are excluded (the default manager hides
    soft-deletes; ``is_active`` filters the rest). An unlinked account (``member is
    None``) has no memberships, so returns ``[]`` and the panel shows the not-linked
    message the other settings tabs use.

    Args:
        member: The logged-in member, or ``None`` for an account not linked to one.

    Returns:
        Ordered rows, one per active guild, each pre-flagged with the member's join state.
    """
    if member is None:
        return []
    joined_ids = set(member.guild_memberships.values_list("guild_id", flat=True))
    return [
        GuildToggleRow(guild=guild, joined=guild.pk in joined_ids, meeting_hint=guild.meeting_schedule)
        for guild in Guild.objects.filter(is_active=True).order_by("name")
    ]
