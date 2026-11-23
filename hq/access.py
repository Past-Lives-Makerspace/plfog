"""Who may open the HQ: admins and every ACTIVE guild officer, and nobody else (#1307).

- A guild officer is an ACTIVE member who leads or staffs any guild (ADR 0004, ``Member.staffed_guilds``); there is
  no role or capability for it. :func:`can_run_hq` is the one rule and :func:`hq_gate` the one decorator: every HQ
  page, card, CSV, setting, upload and edit page sits behind it, so an officer does everything an admin does.
- It is read on every request, never cached, so a member who stops leading or staffing a guild loses the HQ on
  their next request.
- Rendering (a safe method) reads the effective role, ``request.view_as``, so an admin previewing as a member sees
  that member's answer and as a Guest the 404; an action (any other method) reads the actual role, so a preview
  never grants or takes it away.
- A signed-out visitor goes through login and back; anyone else signed in gets the themed 404, so the HQ never
  reveals itself. A guest account is never ACTIVE, and ``core.middleware.MemberLockoutMiddleware`` keeps it off
  ``/hq/`` before it gets here.
- A hidden card is admins only staging: off the HQ page and a 404 at its URL for anyone but an admin.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any

from django.contrib.auth.views import redirect_to_login
from django.db.models import Q, QuerySet
from django.http import Http404

from hq.models import EventHub, HubCard
from membership.models import Member

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponseBase

# The methods that only render, so they read the effective role; anything else acts and reads the actual one.
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})


def can_run_hq(member: Member | None, *, is_admin: bool) -> bool:
    """Whether this viewer may open and run the whole HQ: an admin, or an ACTIVE member who leads or staffs a guild.

    ``is_admin`` is the role being checked: the effective one to render, the actual one to act. One query for a
    member who is not an admin.
    """
    if is_admin:
        return True
    return member is not None and member.status == Member.Status.ACTIVE and member.staffed_guilds.exists()


def officers() -> QuerySet[Member]:
    """Every ACTIVE guild officer, by name: the people the HQ's Owner, Who and Contacted by fields suggest, and whom
    a ledger staff time row links to."""
    return (
        Member.objects.filter(status=Member.Status.ACTIVE)
        .filter(Q(led_guilds__isnull=False) | Q(guild_staff_roles__isnull=False))
        .distinct()
        .order_by("preferred_name", "full_legal_name")
    )


def viewer_member(request: HttpRequest) -> Member | None:
    """The Member the request speaks for: none while signed out, and none for an admin previewing as Guest."""
    if not request.user.is_authenticated:
        return None
    view_as = request.view_as  # type: ignore[attr-defined]  # ViewAsMiddleware sets it
    if view_as.is_previewing and view_as.is_guest:
        return None
    member: Member | None = getattr(request.user, "member", None)
    return member


def renders_as_admin(request: HttpRequest) -> bool:
    """The effective admin bit, for what the page shows (a preview below admin loses it)."""
    return bool(request.view_as.is_admin)  # type: ignore[attr-defined]  # ViewAsMiddleware sets it


def is_actual_admin(request: HttpRequest) -> bool:
    """The real admin bit, for what the request may do (a preview never grants or revokes it)."""
    return bool(request.view_as.actual_is_admin)  # type: ignore[attr-defined]  # ViewAsMiddleware sets it


def actual_member(request: HttpRequest) -> Member | None:
    """The signed in Member whatever the preview, for what the request may do; none while signed out."""
    if not request.user.is_authenticated:
        return None
    member: Member | None = getattr(request.user, "member", None)
    return member


def runs_hq(request: HttpRequest) -> bool:
    """Whether this viewer runs the HQ by the effective role: what the sidebar and the pages show."""
    return can_run_hq(viewer_member(request), is_admin=renders_as_admin(request))


def _admin_for(request: HttpRequest) -> bool:
    """The admin bit this request is judged by: the effective one to render, the actual one to act."""
    return renders_as_admin(request) if request.method in SAFE_METHODS else is_actual_admin(request)


def _may_run(request: HttpRequest) -> bool:
    if request.method in SAFE_METHODS:
        return runs_hq(request)
    return can_run_hq(actual_member(request), is_admin=is_actual_admin(request))


def card_or_404(hub: EventHub, kind: str, *, is_admin: bool) -> HubCard:
    """The ``kind`` card on ``hub``; a 404 when the hub has none, or for anyone but an admin when it is hidden."""
    card = hub.cards.filter(kind=kind).first()
    if card is None or (card.is_hidden and not is_admin):
        raise Http404("No such card.")
    return card


def hq_gate(kind: str | None = None) -> Callable[[Callable[..., HttpResponseBase]], Callable[..., HttpResponseBase]]:
    """The one gate on every HQ view: admins and ACTIVE guild officers (:func:`can_run_hq`), nobody else.

    Signed out is sent through login and back; anyone else who may not run the HQ gets the themed 404. With
    ``kind`` the view is called as ``view(request, hub=hub, card=card, **rest)`` for the hub at ``slug`` and its
    ``kind`` card (a 404 when it has none, or it is hidden and the viewer is no admin); without, the view gets its
    arguments as they came.
    """

    def decorator(view: Callable[..., HttpResponseBase]) -> Callable[..., HttpResponseBase]:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
            if not _may_run(request):
                if not request.user.is_authenticated:
                    return redirect_to_login(request.get_full_path())
                raise Http404("No such HQ.")
            if kind is None:
                return view(request, *args, **kwargs)
            hub = hub_or_404(kwargs.pop("slug"))
            card = card_or_404(hub, kind, is_admin=_admin_for(request))
            return view(request, *args, hub=hub, card=card, **kwargs)

        return wrapped

    return decorator


def hub_or_404(slug: str) -> EventHub:
    """The hub at ``slug``, open or closed: whoever runs the HQ reaches both, so one can be built before it opens."""
    hub = EventHub.objects.filter(slug=slug).select_related("event__area").first()
    if hub is None:
        raise Http404("No such HQ.")
    return hub
