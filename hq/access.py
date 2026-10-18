"""Who may open the HQ, its cards and their organiser screens.

- The open hub's page is public: the sidebar entry and ``/hq/`` land there, and a signed-out
  visitor sees every card whose audience is ``public``. A closed hub is reachable by admins
  only, so an HQ can be built before it is shown.
- A card's audience is the one gate (``HubCard.is_open_to``): admins and the card's named
  members always; ``public`` everyone; ``members`` an ACTIVE member. A hidden card is kept to
  admins on the page and at its URL.
- Rendering reads the effective role, ``request.view_as.is_admin``, so an admin previewing as
  a member or a guest sees that viewer's page. Action gates read the actual role
  (:func:`organizer_required`), and the edit pages use ``hub.view_as.fog_admin_required``.
- A guest account (#654) reaches ``/hq/`` on the members surface
  (``core.middleware.MemberLockoutMiddleware``), because the volunteer and wine wall doors are
  for people with or without an account; what it may open is still the card's audience.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404

from hq.models import EventHub, HubCard
from membership.models import Member

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


class SignInRequired(Exception):
    """A members card was hit signed out; the view answers with the login page and a way back."""


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


def hub_or_404(slug: str, request: HttpRequest) -> EventHub:
    """The hub at ``slug``: an open hub for everyone, a closed one for admins only."""
    hub = EventHub.objects.filter(slug=slug).select_related("event").first()
    if hub is None or (not hub.is_open and not renders_as_admin(request)):
        raise Http404("No such HQ.")
    return hub


def card_or_404(hub: EventHub, kind: str, request: HttpRequest) -> HubCard:
    """The ``kind`` card on ``hub`` for this viewer.

    A hidden card is a 404 for anyone but an admin; a ``members`` card hit signed out raises
    :class:`SignInRequired` so the view can send the visitor through login and back; any other
    card the viewer may not open is a 404, so a closed door never reveals what is behind it.
    """
    card = hub.cards.prefetch_related("named_members").filter(kind=kind).first()
    if card is None:
        raise Http404("No such card.")
    is_admin = renders_as_admin(request)
    if card.is_hidden and not is_admin:
        raise Http404("No such card.")
    if card.audience == HubCard.Audience.MEMBERS and not request.user.is_authenticated:
        raise SignInRequired
    if not card.is_open_to(viewer_member(request), is_admin=is_admin):
        raise Http404("No such card.")
    return card


def card_view(kind: str) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    """Resolve the hub at ``slug`` and its ``kind`` card for the viewer, and hand both to the view.

    The view is called as ``view(request, hub=hub, card=card, **rest)``; a ``members`` card hit
    signed out goes to the login page with this URL as the way back. C2 and C3 wrap their card
    pages in this.
    """

    def decorator(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, slug: str, **kwargs: Any) -> HttpResponse:
            hub = hub_or_404(slug, request)
            try:
                card = card_or_404(hub, kind, request)
            except SignInRequired:
                return redirect_to_login(request.get_full_path())
            return view(request, *args, hub=hub, card=card, **kwargs)

        return wrapped

    return decorator


def organizer_required(kind: str) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    """Admit admins and the ``kind`` card's named members, by their actual roles, on the hub at ``slug``.

    The view is called as ``view(request, hub=hub, card=card, **rest)``. Anyone else gets a 403,
    and a card the hub does not have is a 404.
    """

    def decorator(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, slug: str, **kwargs: Any) -> HttpResponse:
            hub = hub_or_404(slug, request)
            card = hub.cards.prefetch_related("named_members").filter(kind=kind).first()
            if card is None:
                raise Http404("No such card.")
            member: Member | None = getattr(request.user, "member", None) if request.user.is_authenticated else None
            if not card.can_organize(member, is_admin=is_actual_admin(request)):
                raise PermissionDenied("Only the organisers of this part of the event can do this.")
            return view(request, *args, hub=hub, card=card, **kwargs)

        return wrapped

    return decorator
