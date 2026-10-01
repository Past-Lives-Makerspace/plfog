"""Seed the Leadership Directory roster from a JSON document kept outside the repo (#464).

The roster names real people, so it never lives in git: the ``seed_leadership_roster``
command takes it as a file or a base64 argument and hands it to :func:`seed_roster`. Each
person is matched to an existing member (never invented) and each guild's channel to one
active guild; anything that does not match exactly one row is reported and skipped for a
human to settle. No guild lead is ever changed.

Roster shape::

    {
      "team": [
        {"name": "Ada Lovelace", "discord": "@ada",
         "roles": [{"title": "Founder", "email": "ada@example.org"}]}
      ],
      "guild_channels": [
        {"guild": "Woodwork", "channel": "#woodworkers"}
      ]
    }

``discord`` is a handle (``@ada``), a Discord mention (``<@123>``) or blank; ``email`` may be
blank; ``guild`` is a fragment of the guild's name, so a rename such as "Woodworkers" to
"Woodworking Guild" still finds it. Every key is required, so a malformed roster fails loudly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from membership.models import Guild, LeadershipListing, LeadershipRole, Member

_DISCORD_MENTION = re.compile(r"<@(\d+)>")


@dataclass
class RosterReport:
    """What one run did, and what it left for a human, as roster names and guild names."""

    listed: list[str] = field(default_factory=list)
    already_listed: list[str] = field(default_factory=list)
    unmatched_people: list[str] = field(default_factory=list)
    channels_set: list[str] = field(default_factory=list)
    channels_kept: list[str] = field(default_factory=list)
    unmatched_guilds: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        """The report as printable lines; the two "did" lines always show, the rest only when non-empty."""
        out = [f"Listed: {', '.join(self.listed) or 'nobody new'}"]
        if self.already_listed:
            out.append(f"Already listed, left alone: {', '.join(self.already_listed)}")
        if self.unmatched_people:
            out.append(f"No single member matched, add by hand: {', '.join(self.unmatched_people)}")
        out.append(f"Channel names set: {', '.join(self.channels_set) or 'none'}")
        if self.channels_kept:
            out.append(f"Channel names already set, kept: {', '.join(self.channels_kept)}")
        if self.unmatched_guilds:
            out.append(f"No single active guild matched, set by hand: {', '.join(self.unmatched_guilds)}")
        return out


def find_member(name: str, discord: str) -> Member | None:
    """The one member whose legal name contains every word of ``name``, else the one with that Discord.

    A name that matches more than one member is ambiguous and yields None; Discord is tried
    only when the name matches nobody. A ``<@id>`` mention matches the verified
    ``discord_user_id``; anything else matches the typed ``discord_handle``, case-insensitive.
    """
    by_name = Member.objects.all()
    for token in name.split():
        by_name = by_name.filter(full_legal_name__icontains=token)
    candidates = list(by_name[:2])
    if not candidates and discord:
        mention = _DISCORD_MENTION.fullmatch(discord)
        if mention:
            candidates = list(Member.objects.filter(discord_user_id=mention.group(1))[:2])
        else:
            candidates = list(Member.objects.filter(discord_handle__iexact=discord)[:2])
    return candidates[0] if len(candidates) == 1 else None


def find_guild(fragment: str) -> Guild | None:
    """The one active guild whose name contains ``fragment``, or None."""
    candidates = list(Guild.objects.filter(is_active=True, name__icontains=fragment)[:2])
    return candidates[0] if len(candidates) == 1 else None


def seed_roster(roster: dict[str, Any], *, dry_run: bool = False) -> RosterReport:
    """Create the team listings and fill blank guild channel names from ``roster``; return the report.

    Idempotent: a member who already has a listing is left alone, as is a guild whose channel
    name is already set (``sync_guild_discord_channels`` owns that field once a webhook exists).
    With ``dry_run`` the report is the same and nothing is written.
    """
    report = RosterReport()
    for position, person in enumerate(roster["team"]):
        member = find_member(person["name"], person["discord"])
        if member is None:
            report.unmatched_people.append(person["name"])
            continue
        if LeadershipListing.objects.filter(member=member).exists():
            report.already_listed.append(person["name"])
            continue
        report.listed.append(person["name"])
        if dry_run:
            continue
        listing = LeadershipListing.objects.create(member=member, is_listed=True, sort_order=position)
        for index, role in enumerate(person["roles"]):
            LeadershipRole.objects.create(listing=listing, title=role["title"], email=role["email"], sort_order=index)

    for entry in roster["guild_channels"]:
        guild = find_guild(entry["guild"])
        if guild is None:
            report.unmatched_guilds.append(entry["guild"])
            continue
        if guild.discord_channel_name:
            report.channels_kept.append(guild.name)
            continue
        report.channels_set.append(guild.name)
        if dry_run:
            continue
        guild.discord_channel_name = entry["channel"]
        guild.save(update_fields=["discord_channel_name"])
    return report
