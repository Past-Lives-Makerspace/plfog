"""Discord reply formatting + resolution helpers shared by the member slash-command handlers.

The per-app ``discord_commands`` handlers stay thin: they resolve a domain object, call a
reusable model/manager/service method, and hand the result to a builder here. Keeping the
Discord-format concerns (absolute hub links, site-timezone dates, embed-field truncation,
lenient ``guild`` resolution) in one place keeps them out of the ``membership`` / ``billing``
domain apps — the reply *shape* is a Discord concern, not a domain one.

The low-level reply primitives (:func:`~core.events.discord_interactions.reply` and friends)
live next door in :mod:`core.events.discord_interactions`; this module composes them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.events.discord_interactions import reply

if TYPE_CHECKING:
    from datetime import datetime

    from membership.models import Guild

# An interaction payload is Discord's JSON dict (mirrors core.events.discord_commands).
Interaction = dict


def option_value(interaction: Interaction, name: str) -> str | None:
    """The string value of the named command option, or ``None`` if it wasn't supplied.

    Every member-command option is a Discord STRING option, so the value is coerced to
    ``str`` (Discord may pass an int for a numeric-looking value).
    """
    for option in interaction.get("data", {}).get("options", []):
        if option.get("name") == name:
            value = option.get("value")
            return str(value) if value is not None else None
    return None


def hub_url(viewname: str, *args: object) -> str:
    """An absolute member-hub URL for ``viewname`` (reverse + ``MEMBER_BASE_URL`` base)."""
    from django.conf import settings
    from django.urls import reverse

    return f"{settings.MEMBER_BASE_URL}{reverse(viewname, args=args)}"


def format_local(dt: datetime) -> str:
    """A human date + time in the site timezone, e.g. ``'Sat Jul 19, 2:00 PM'``."""
    from django.utils import timezone

    local = timezone.localtime(dt)
    return f"{local.strftime('%a %b %-d')}, {local.strftime('%-I:%M %p')}"


def truncate(text: str, limit: int, *, suffix: str = "…") -> str:
    """Trim ``text`` to at most ``limit`` characters, appending ``suffix`` when it overflows.

    Discord embed-field values are capped at 1024 characters; long guild copy is trimmed
    with a "read more on the page" style tail rather than dumped or dropped.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - len(suffix), 0)].rstrip() + suffix


def resolve_command_guild(interaction: Interaction) -> Guild | None:
    """The guild a member command targets: an explicit ``guild`` string beats the channel.

    1. An explicit ``guild`` option is matched leniently (case-insensitive name/slug
       substring, active guilds only). A single match wins; when several match, an exact
       name/slug match breaks the tie, otherwise it's treated as unresolved (``None``).
    2. Else the guild mapped to the invoking channel (``for_discord_channel``).
    3. Neither → ``None`` (the caller returns :func:`guild_not_specified_reply`).
    """
    from membership.models import Guild

    explicit = option_value(interaction, "guild")
    if explicit:
        matches = list(Guild.objects.matching(explicit))
        if len(matches) == 1:
            return matches[0]
        lowered = explicit.strip().lower()
        exact = [g for g in matches if lowered in (g.name.lower(), g.slug.lower())]
        return exact[0] if len(exact) == 1 else None
    return Guild.objects.for_discord_channel(interaction.get("channel_id", ""))


def guild_not_specified_reply() -> dict:
    """The ephemeral "which guild?" nudge, listing the active guilds so it's never a dead end."""
    from membership.models import Guild

    names = list(Guild.objects.filter(is_active=True).order_by("name").values_list("name", flat=True)[:25])
    content = "Which guild? Run this in the guild's Discord channel, or add the `guild` option."
    if names:
        content += "\n\nGuilds: " + ", ".join(names) + "."
    return reply(content, ephemeral=True)
