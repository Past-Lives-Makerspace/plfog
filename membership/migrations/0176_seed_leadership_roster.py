"""Seed the first Leadership & Admin Team roster and the roster guilds' channel names (#464).

Matches each roster person to an existing member and each roster guild to one active guild.
It never invents a member, never touches a guild's lead, and skips (and names) anything that
does not match exactly one row, so a human resolves it by hand. The reverse removes exactly
the listings and channel names this seed wrote.
"""

from __future__ import annotations

import re
from typing import Any

from django.db import migrations

# (name, Discord handle from the pinned post, [(role title, contact email), ...]) in page order.
TEAM: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("Morlock", "@Morlock", [("Founder & CEO, Executive Director", "morlock@pastlives.space")]),
    ("Athan Spathas", "<@712772431961653299>", [("Guild Voting Contact", "")]),
    ("Dixie Junius", "@Dixie", [("Member Liaison, Executive Assistant", "dixie@pastlives.space")]),
    (
        "Lee Mendelsohn",
        "@Lee",
        [
            ("Membership Director and Internal Operations Director", "membership@pastlives.space"),
            ("Class Administrator", "lee@pastlives.space"),
        ],
    ),
    ("Shane Stewart", "", [("Tools and Machine Manager", "shops@pastlives.space")]),
    ("Sushuma", "@sushuma", [("Business Office Manager", "sushuma@pastlives.space")]),
    ("Phoebe Valenti", "@Phoebex", [("Design + Build Director & Licensed Contractor", "phoebe@pastlives.space")]),
]

# (a fragment of the guild's name, its Discord channel) for the 14 roster guilds. A fragment
# rather than the full name, so "Woodworkers" on the post still finds "Woodworking Guild".
GUILD_CHANNELS: list[tuple[str, str]] = [
    ("Art Framing", "#🖼️-art-framing"),
    ("Ceramics", "#🪴-ceramics"),
    ("Events", "#🧸-events-guild"),
    ("Gardeners", "#🌼-gardeners"),
    ("Glass", "#🖼-glass"),
    ("Jewelry", "#💎-jewelers"),
    ("Leather", "#🟫-leather-guild"),
    ("Metalworkers", "#🤘🏼-metalworkers"),
    ("Prison Outreach", "#💌-prison-outreach"),
    ("Tech", "#🤖-tech-guild"),
    ("Textiles", "#🪡-textiles-dept"),
    ("Visual Arts", "#🖌️-visual-arts"),
    ("Woodwork", "#🦫-woodworkers"),
    ("Writers", "#📝-writers-guild"),
]

_DISCORD_MENTION = re.compile(r"<@(\d+)>")


def _single(rows: list[Any]) -> Any | None:
    return rows[0] if len(rows) == 1 else None


def find_member(Member: Any, name: str, handle: str) -> Any | None:
    """The one member whose legal name contains every word of ``name``, else the one with ``handle``.

    The pinned post wrote some people as a Discord mention (``<@id>``), matched on the verified
    ``discord_user_id``; a plain ``@handle`` matches the typed ``discord_handle``, case-insensitive.
    Returns None when neither route yields exactly one member.
    """
    by_name = Member.objects.all()
    for token in name.split():
        by_name = by_name.filter(full_legal_name__icontains=token)
    match = _single(list(by_name[:2]))
    if match is not None or not handle:
        return match
    mention = _DISCORD_MENTION.fullmatch(handle)
    if mention:
        return _single(list(Member.objects.filter(discord_user_id=mention.group(1))[:2]))
    return _single(list(Member.objects.filter(discord_handle__iexact=handle)[:2]))


def find_guild(Guild: Any, fragment: str) -> Any | None:
    """The one active guild whose name contains ``fragment``, or None."""
    return _single(list(Guild.objects.filter(is_active=True, name__icontains=fragment)[:2]))


def seed_roster(apps: Any, schema_editor: Any) -> None:
    Member = apps.get_model("membership", "Member")
    Guild = apps.get_model("membership", "Guild")
    LeadershipListing = apps.get_model("membership", "LeadershipListing")
    LeadershipRole = apps.get_model("membership", "LeadershipRole")

    unmatched_people: list[str] = []
    for position, (name, handle, roles) in enumerate(TEAM):
        member = find_member(Member, name, handle)
        if member is None:
            unmatched_people.append(name)
            continue
        if LeadershipListing.objects.filter(member_id=member.pk).exists():
            continue  # a re-run, or a listing an admin already made by hand
        listing = LeadershipListing.objects.create(member_id=member.pk, is_listed=True, sort_order=position)
        for index, (title, email) in enumerate(roles):
            LeadershipRole.objects.create(listing=listing, title=title, email=email, sort_order=index)

    unmatched_guilds: list[str] = []
    for fragment, channel in GUILD_CHANNELS:
        guild = find_guild(Guild, fragment)
        if guild is None:
            unmatched_guilds.append(fragment)
            continue
        if not guild.discord_channel_name:
            guild.discord_channel_name = channel
            guild.save(update_fields=["discord_channel_name"])

    if unmatched_people:
        print(f"leadership seed: no single member matched {unmatched_people}; list them by hand.")
    if unmatched_guilds:
        print(f"leadership seed: no single active guild matched {unmatched_guilds}; set the channel by hand.")


def unseed_roster(apps: Any, schema_editor: Any) -> None:
    """Reverse: delete the seeded listings (roles cascade) and blank the channel names this seed wrote."""
    Member = apps.get_model("membership", "Member")
    Guild = apps.get_model("membership", "Guild")
    LeadershipListing = apps.get_model("membership", "LeadershipListing")
    matches = (find_member(Member, name, handle) for name, handle, _roles in TEAM)
    LeadershipListing.objects.filter(member_id__in=[m.pk for m in matches if m is not None]).delete()
    for _fragment, channel in GUILD_CHANNELS:
        Guild.objects.filter(discord_channel_name=channel).update(discord_channel_name="")


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0175_leadership_directory"),
    ]

    operations = [
        migrations.RunPython(seed_roster, unseed_roster),
    ]
