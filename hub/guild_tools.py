"""Guild Tools (#921): the tools page as guild officers, leads and staff see it.

``/tools/`` (``hub_admin_tools``) is one page under two names. An admin (``view_as`` aware) sees
Admin Tools, exactly as before. Anyone else who runs a guild sees Guild Tools: the same page,
plus one section for a guild they run (Guild Settings, its Orientations, Reservations and
Events), with a picker when they run more than one. This module holds the two names, the role
question and the picker's read model; ``hub.views._tools_page_title`` decides which name a
viewer gets, and ``hub.views.hub_admin_tools`` builds the cards.

Nothing here grants anything. Every guild offered comes from
:func:`membership.permissions.manageable_guilds`, the queryset form of ``can_edit_guild``, which
is the gate on every page the guild section's cards open, except Cubbies (#1015): its page is
gated by :func:`membership.permissions.can_manage_cubbies` (the guild's lead and staff, and
admins), so ``hub_admin_tools`` shows that card only while the selected guild passes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from membership.permissions import is_effective_staff, manageable_guilds

if TYPE_CHECKING:
    from django.http import HttpRequest

    from membership.models import Guild

ADMIN_TOOLS_TITLE = "Admin Tools"
GUILD_TOOLS_TITLE = "Guild Tools"


def runs_a_guild(request: HttpRequest) -> bool:
    """True when this request runs a guild: a guild officer, or a guild's lead or staff (``view_as`` aware).

    An officer is effective staff and edits every guild, so no query is made for one; anyone
    else costs one EXISTS over the guilds they lead or staff. Read on every hub page through the
    sidebar. The caller asks about admins first, since an admin is effective staff too.
    """
    return is_effective_staff(request) or manageable_guilds(request).exists()


@dataclass(frozen=True)
class GuildToolsGuilds:
    """The guilds a Guild Tools viewer runs, and the one the guild section is showing.

    Attributes:
        guilds: every guild :func:`~membership.permissions.manageable_guilds` gives this
            request, name ordered: the guilds they lead or staff, or every active guild for an
            officer.
        selected: the guild whose cards show, from ``?guild=<pk>`` when it names one of
            ``guilds``, else the first; None when there is no guild at all.
    """

    guilds: list[Guild]
    selected: Guild | None

    @classmethod
    def for_request(cls, request: HttpRequest) -> GuildToolsGuilds:
        """Read the viewer's guilds (one query) and pick the one ``?guild=`` asks for.

        A ``?guild=`` that is not a guild's pk (compared as text, so a Unicode digit such as ``²``
        cannot reach ``int``), or names a guild this viewer does not run, is
        ignored and the first guild shows, so a crafted link never puts another guild's cards
        on the page.
        """
        guilds = list(manageable_guilds(request))
        asked = request.GET.get("guild", "")
        chosen = [guild for guild in guilds if str(guild.pk) == asked]
        if chosen:
            return cls(guilds=guilds, selected=chosen[0])
        return cls(guilds=guilds, selected=guilds[0] if guilds else None)

    @property
    def shows_picker(self) -> bool:
        """True when there is more than one guild to choose from."""
        return len(self.guilds) > 1
