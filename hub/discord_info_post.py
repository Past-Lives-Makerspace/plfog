"""The FOG-managed #important-info pinned post: important links + the bot-commands guide.

The locked #important-info channel holds one pinned bot message with two embeds. FOG owns
that message: :func:`build_info_embeds` builds both embeds (pure — no HTTP), and
:func:`sync_info_post` PATCHes them onto the existing message in place via
:func:`core.integrations.discord_channel.edit_channel_message`, so the message id — and its
pin — never change.

The links embed comes from ``SiteConfiguration.discord_info_links_content`` (edited on Site
Settings → Discord; :data:`core.models.DISCORD_INFO_LINKS_DEFAULT` backstops a blanked
field). The commands embed is generated from the same slash-command registry
(:func:`core.events.discord_commands.all_commands`) that ``register_discord_commands`` PUTs
to Discord — one source of truth, so a newly registered command appears in the guide on the
next admin save without anyone editing copy.

Sync happens on Site Settings save only (no scheduled job); the caller catches
:class:`~core.integrations.discord_channel.DiscordChannelError` and surfaces it to the admin.
"""

from __future__ import annotations

from typing import Any

from core.integrations.discord_channel import edit_channel_message

# Discord's hard cap on one embed's description.
EMBED_DESCRIPTION_MAX = 4096
# The community blue used by the other FOG channel posts.
_EMBED_COLOR = 0x3D8BD4
LINKS_TITLE = "📌 Past Lives — Important Links"
COMMANDS_TITLE = "🤖 Fog Bot Commands"


def _links_content() -> str:
    """The admin-edited links markdown, backstopped by the default so it's never empty."""
    from core.models import DISCORD_INFO_LINKS_DEFAULT, SiteConfiguration

    return SiteConfiguration.load().discord_info_links_content.strip() or DISCORD_INFO_LINKS_DEFAULT


def _command_lines() -> list[str]:
    """One ``/name — description`` bullet per registered slash command, alphabetized.

    Reads the same registry ``register_discord_commands`` serializes (populated by
    ``autodiscover()`` at app startup), so the guide can never drift from the real set.
    """
    from core.events.discord_commands import all_commands

    return [f"• `/{cmd.name}` — {cmd.description}" for cmd in sorted(all_commands(), key=lambda c: c.name)]


def _clip(text: str) -> str:
    """Keep a description within Discord's cap, dropping whole trailing lines if needed.

    The links content is form-capped, so this only guards content saved outside the form
    and a (hypothetically) enormous command set — where a truncated guide beats a 400.
    """
    if len(text) <= EMBED_DESCRIPTION_MAX:
        return text
    clipped = ""
    for line in text.split("\n"):
        candidate = f"{clipped}\n{line}" if clipped else line
        if len(candidate) > EMBED_DESCRIPTION_MAX - 2:  # leave room for the ellipsis line
            break
        clipped = candidate
    return f"{clipped}\n…"


def build_info_embeds() -> list[dict[str, Any]]:
    """The two #important-info embeds: the links embed then the commands guide.

    Pure over the config row + the command registry — no HTTP — and every description
    fits Discord's 4096-char cap.
    """
    return [
        {"title": LINKS_TITLE, "description": _clip(_links_content()), "color": _EMBED_COLOR},
        {"title": COMMANDS_TITLE, "description": _clip("\n".join(_command_lines())), "color": _EMBED_COLOR},
    ]


def sync_info_post() -> None:
    """Edit the pinned #important-info message in place to match the current embeds.

    No-op unless BOTH ``discord_info_channel_id`` and ``discord_info_message_id`` are set
    (the surface isn't configured yet). Raises
    :class:`~core.integrations.discord_channel.DiscordChannelError` on any Discord failure —
    the Site Settings save catches it and shows the admin an error, never a silent pass.
    """
    from core.models import SiteConfiguration

    config = SiteConfiguration.load()
    channel_id = config.discord_info_channel_id.strip()
    message_id = config.discord_info_message_id.strip()
    if not channel_id or not message_id:
        return
    edit_channel_message(channel_id, message_id, build_info_embeds())
