"""Who may use kiln tickets, and as what (#691).

- The kiln belongs to one guild, found by ``settings.KILN_GUILD_SLUG`` (``ceramics-guild``,
  guild 8 in production). With no such guild nobody is crew and the sidebar entry hides.
- Crew are that guild's staff: ``Guild.guild_lead`` plus its ``GuildStaffMembership`` rows.
  No admin override and no new role.
- A maker is any member the portal lets in, plus any **guest** account (#654, the account a
  class booking makes): students in ceramics classes file tickets that way. A guest reaches
  the kiln pages and nothing else (``core.middleware.MemberLockoutMiddleware`` and
  ``AdminRedirectAccountAdapter.pre_login``). Former and suspended members stay locked out.
- The launch switch ``SiteConfiguration.kiln_tickets_open`` (off by default) holds all of that
  back until the crew screens ship: while it is off only the crew reach ``/kiln/`` (everyone
  else gets a 404) and guests are locked out exactly as before #691.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.db.models import Exists, OuterRef, Q

from membership.models import Guild, GuildStaffMembership, Member

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

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


def kiln_is_open() -> bool:
    """Whether the launch switch lets everyone in (one query; ``load`` is not cached)."""
    from core.models import SiteConfiguration

    return SiteConfiguration.load().kiln_tickets_open


def can_reach_kiln(member: Member | None) -> bool:
    """The launch gate: everyone while the switch is on, the crew alone while it is off."""
    return kiln_is_open() or is_crew(member)


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


def shows_kiln_nav(member: Member) -> bool:
    """Whether the sidebar lists Kiln Tickets: the guild's lead and staff always, its members
    once the launch switch is on.

    One query, the launch switch folded in, so the sidebar costs every page one query and no
    more. Everyone else still reaches the pages from the guild's own page, and a guest gets the
    guest menu (:attr:`KilnNav.guest_only`) instead of this entry.
    """
    from core.models import SiteConfiguration

    kiln = Guild.objects.filter(slug=settings.KILN_GUILD_SLUG)
    crew = kiln.filter(Q(guild_lead=OuterRef("pk")) | Q(staff_memberships__member=OuterRef("pk")))
    joined = kiln.filter(memberships__member=OuterRef("pk"))
    is_open = SiteConfiguration.objects.filter(pk=1, kiln_tickets_open=True)
    return Member.objects.filter(pk=member.pk).filter(Exists(crew) | (Exists(joined) & Exists(is_open))).exists()


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
    """Admit the kiln guild's lead and staff only, checked on every request."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not is_crew(_request_member(request)):
            raise PermissionDenied("Only the Ceramics Guild crew can do this.")
        return view(request, *args, **kwargs)

    return wrapped


class KilnNav:
    """The sidebar's kiln state, computed only when a template reads it."""

    def __init__(self, member: Member | None) -> None:
        self._member = member
        self._show: bool | None = None

    @property
    def guest_only(self) -> bool:
        """A guest account sees the kiln pages and Sign out, nothing else."""
        return self._member is not None and self._member.status == Member.Status.GUEST

    @property
    def show(self) -> bool:
        """Whether the Kiln Tickets entry appears in the sidebar."""
        if self._member is None:
            return False
        if self._show is None:
            self._show = shows_kiln_nav(self._member)
        return self._show

    @property
    def guild_link(self) -> bool:
        """Whether the kiln guild's page links here: everyone while open, the crew while not.

        Read only on the kiln guild's own page, so the query never reaches another page.
        """
        return self._member is not None and can_reach_kiln(self._member)

    @property
    def guild_slug(self) -> str:
        """The kiln guild's slug, so its guild page can link here without a query."""
        return str(settings.KILN_GUILD_SLUG)
