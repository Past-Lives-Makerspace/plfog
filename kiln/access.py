"""Who may use kiln tickets, and as what (#691).

- The kiln belongs to one guild, found by ``settings.KILN_GUILD_SLUG`` (``ceramics-guild``,
  guild 8 in production). With no such guild nobody is crew.
- Crew are that guild's staff: ``Guild.guild_lead`` plus its ``GuildStaffMembership`` rows
  (:func:`is_crew`, which crew notifications follow). Site admins see the crew screens too
  (:func:`can_run_kiln`) but are not crew. No new role.
- Kiln Tickets lives on the kiln guild's page as a tab (``?tab=kiln``), not in the sidebar;
  ``/kiln/`` sends everyone but a guest there.
- A maker is any member the portal lets in, plus any **guest** account (#654, the account a
  class booking makes): students in ceramics classes file tickets that way. A guest reaches
  the kiln pages and nothing else (``core.middleware.MemberLockoutMiddleware`` and
  ``AdminRedirectAccountAdapter.pre_login``). Former and suspended members stay locked out.
- The launch switch ``SiteConfiguration.kiln_tickets_open`` (off by default) holds all of that
  back until the crew screens ship: while it is off only the crew and admins reach ``/kiln/`` (everyone
  else gets a 404) and guests are locked out exactly as before #691.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.urls import reverse

from membership.models import Guild, GuildStaffMembership, Member

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

# What the breadcrumbs call the kiln guild, so no page pays a query for its name.
KILN_GUILD_LABEL = "Ceramics Guild"

# Every status that files tickets. FORMER never does. A suspended member files tickets only
# while the portal admits them (the lockout middleware decides that before any kiln view).
_MAKER_STATUSES = (Member.Status.ACTIVE, Member.Status.INVITED, Member.Status.SUSPENDED, Member.Status.GUEST)


def kiln_guild() -> Guild | None:
    """The guild that runs the kiln, or None when this database has none."""
    return Guild.objects.filter(slug=settings.KILN_GUILD_SLUG).first()


def is_crew(member: Member | None, guild: Guild | None = None) -> bool:
    """Whether ``member`` is the kiln guild's lead or holds a staff row on it."""
    if member is None:
        return False
    guild = guild if guild is not None else kiln_guild()
    if guild is None:
        return False
    if guild.guild_lead_id == member.pk:
        return True
    return GuildStaffMembership.objects.filter(guild=guild, member=member).exists()


def can_run_kiln(member: Member | None, guild: Guild | None = None) -> bool:
    """Whether ``member`` sees the crew screens: the crew, and site admins (who run the site and
    must see what they ship, switch or no switch). Crew notifications stay with :func:`is_crew`.
    Pass the kiln ``guild`` when the caller already holds it, to save its query."""
    return member is not None and (member.is_fog_admin or is_crew(member, guild))


def kiln_is_open() -> bool:
    """Whether the launch switch lets everyone in (one query; ``load`` is not cached)."""
    from core.models import SiteConfiguration

    return SiteConfiguration.load().kiln_tickets_open


def can_reach_kiln(member: Member | None) -> bool:
    """The launch gate: everyone while the switch is on, the crew and admins while it is off."""
    return kiln_is_open() or can_run_kiln(member)


def guest_may_use_kiln() -> bool:
    """Whether a guest account is let onto the members site for kiln tickets (the switch)."""
    return kiln_is_open()


def can_file_tickets(member: Member | None) -> bool:
    """Any member the portal admits, and any guest account."""
    return member is not None and member.status in _MAKER_STATUSES


def maker_type_for(member: Member) -> str:
    """The maker type a ticket records: guild staff, a student or guest, or a member."""
    from kiln.models import KilnTicket

    if is_crew(member):
        return KilnTicket.MakerType.STAFF
    if member.status == Member.Status.GUEST:
        return KilnTicket.MakerType.STUDENT
    return KilnTicket.MakerType.MEMBER


def kiln_home_url(*, older: bool = False) -> str:
    """The Kiln Tickets tab on the kiln guild's page, where every maker but a guest keeps their tickets."""
    url = f"{reverse('hub_guild_detail', args=[settings.KILN_GUILD_SLUG])}?tab=kiln"
    return f"{url}&older=1" if older else url


def _request_member(request: HttpRequest) -> Member | None:
    member: Member | None = getattr(request.user, "member", None)
    return member


def kiln_open_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """While the launch switch is off, every kiln URL is a 404 for anyone but the crew."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not can_reach_kiln(_request_member(request)):
            raise Http404("Kiln tickets are not open yet.")
        return view(request, *args, **kwargs)

    return wrapped


def maker_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Admit a signed-in member who may file tickets; refuse anyone else with a 403."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not can_file_tickets(_request_member(request)):
            raise PermissionDenied("Kiln tickets are for members and class guests.")
        return view(request, *args, **kwargs)

    return wrapped


def crew_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Admit the kiln guild's lead and staff, and site admins, checked on every request."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not can_run_kiln(_request_member(request)):
            raise PermissionDenied("Only the Ceramics Guild crew can do this.")
        return view(request, *args, **kwargs)

    return wrapped


class KilnNav:
    """What the hub layout needs about the kiln, with no query: the guest menu and where "back" goes."""

    def __init__(self, member: Member | None) -> None:
        self._member = member

    @property
    def guest_only(self) -> bool:
        """A guest account sees the kiln pages and Sign out, nothing else."""
        return self._member is not None and self._member.status == Member.Status.GUEST

    @property
    def guild_slug(self) -> str:
        """The kiln guild's slug, so the sidebar can light its entry on a kiln page."""
        return str(settings.KILN_GUILD_SLUG)

    @property
    def home_url(self) -> str:
        """Where a kiln page's way back goes: a guest's own page, else the guild's Kiln Tickets tab."""
        return reverse("kiln:mine") if self.guest_only else kiln_home_url()

    @property
    def home_label(self) -> str:
        """The name of :attr:`home_url` in a breadcrumb."""
        return "Kiln Tickets" if self.guest_only else KILN_GUILD_LABEL
