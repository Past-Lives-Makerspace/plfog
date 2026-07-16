"""The ``/link`` slash command — the connect on-ramp for unlinked members.

``/link`` is the highest-leverage command: it's how a member in Discord connects their
account to Past Lives, which gates *every* other part of the integration (guild sync, DMs,
and every other slash command). The command itself makes **no state change** — it hands the
member the one-tap web OAuth connect link (which links only on a verified-email match), or,
if they're already linked, a friendly "you're all set". Linking happens exclusively inside
the vetted OAuth flow behind that link, never here.

``requires_link=False`` is essential: ``/link``'s whole audience is *unlinked* members, so
the platform's auto-connect gate must not intercept it — the handler does its own
linked/unlinked branching. It replies ephemerally in three cases: integration not
configured, already linked, and not-yet-linked (the connect link). Any unexpected exception
is turned into the friendly error reply by :func:`core.events.discord_commands.dispatch`'s
per-command guard, so Discord never sees a 500.

Mirrors the built-in ``/fog-ping`` reference command in :mod:`core.events.discord_commands`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.events.discord_commands import SlashCommand, register
from core.events.discord_interactions import reply

if TYPE_CHECKING:
    from core.events.discord_commands import Interaction
    from membership.models import Member

# --- Reply copy (spec §6 States 1-3) ------------------------------------------

_NOT_CONFIGURED = (
    "Connecting Discord isn't set up right now. Please try again later, or reach out to a Past Lives organizer."
)
_ALREADY_LINKED = (
    "You're already connected as **{display_name}** — you're all set. Try `/whats-on` to see what's coming up."
)
_CONNECT = (
    "Let's connect your Discord to your Past Lives account. **[Connect Discord]({connect_url})**"
    "\n\nAfter you approve, we'll match you by your verified email, link you automatically, and set you up in "
    "your guilds. It only takes a few seconds."
)


def _connect_url() -> str:
    """The absolute one-tap ``hub_discord_link_start`` URL handed to an unlinked member.

    Built from :data:`settings.MEMBER_BASE_URL` (the handler has no request), mirroring how
    the ``/fog-ping`` reference command builds its absolute hub link.
    """
    from django.conf import settings
    from django.urls import reverse

    return f"{settings.MEMBER_BASE_URL}{reverse('hub_discord_link_start')}"


def _link(interaction: Interaction, member: Member | None) -> dict:
    """Serve the caller their connect state — never links anyone (the OAuth flow does).

    ``requires_link=False`` means dispatch calls this for *every* caller, passing ``member``
    as the linked :class:`~membership.models.Member` for the caller's Discord id or ``None``
    if they aren't linked yet. The three ephemeral outcomes mirror spec §6 States 1-3.
    """
    from core.events import discord_oauth

    if not discord_oauth.is_configured():
        return reply(_NOT_CONFIGURED, ephemeral=True)

    if member is not None:
        return reply(_ALREADY_LINKED.format(display_name=member.display_name), ephemeral=True)

    connect_url = _connect_url()
    button_row = {
        "type": 1,
        "components": [
            {"type": 2, "style": 5, "label": "Connect Discord", "url": connect_url},
        ],
    }
    return reply(_CONNECT.format(connect_url=connect_url), ephemeral=True, components=[button_row])


LINK = SlashCommand(
    name="link",
    description="Connect your Discord to your Past Lives account.",
    handler=_link,
    requires_link=False,
    ephemeral=True,
    defer=False,
    scope="guild",
)

register(LINK)
