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
    from django.http import HttpRequest, HttpResponseBase


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


def actual_member(request: HttpRequest) -> Member | None:
    """The signed in Member whatever the preview, for what the request may do; none while signed out."""
    if not request.user.is_authenticated:
        return None
    member: Member | None = getattr(request.user, "member", None)
    return member


def hub_or_404(slug: str, request: HttpRequest) -> EventHub:
    """The hub at ``slug`` for rendering: an open hub for everyone, a closed one for admins only (effective role)."""
    return _hub(slug, admin=renders_as_admin(request))


def hub_for_action(slug: str, request: HttpRequest) -> EventHub:
    """The hub at ``slug`` for an action: as :func:`hub_or_404`, but a closed hub admits the ACTUAL admin,
    so an admin previewing as a member can still act on an HQ that is not open yet."""
    return _hub(slug, admin=is_actual_admin(request))


def _hub(slug: str, *, admin: bool) -> EventHub:
    hub = EventHub.objects.filter(slug=slug).select_related("event").first()
    if hub is None or (not hub.is_open and not admin):
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


def card_view(kind: str) -> Callable[[Callable[..., HttpResponseBase]], Callable[..., HttpResponseBase]]:
    """Resolve the hub at ``slug`` and its ``kind`` card for the viewer, and hand both to the view.

    The view is called as ``view(request, hub=hub, card=card, **rest)``; a ``members`` card hit
    signed out goes to the login page with this URL as the way back. C2 and C3 wrap their card
    pages in this.
    """

    def decorator(view: Callable[..., HttpResponseBase]) -> Callable[..., HttpResponseBase]:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, slug: str, **kwargs: Any) -> HttpResponseBase:
            hub = hub_or_404(slug, request)
            try:
                card = card_or_404(hub, kind, request)
            except SignInRequired:
                return redirect_to_login(request.get_full_path())
            return view(request, *args, hub=hub, card=card, **kwargs)

        return wrapped

    return decorator


def organizer_page(kind: str) -> Callable[[Callable[..., HttpResponseBase]], Callable[..., HttpResponseBase]]:
    """A page for the ``kind`` card's organisers only: :func:`card_view`'s gate, then a 403 for anyone it lets
    through who does not organise the card.

    A hidden or closed door is still the 404 :func:`card_or_404` gives, so it never reveals itself; a viewer
    the card is open to who is not an organiser gets the 403 the volunteer roster gives. The check reads the
    effective role, as the page renders: an admin previewing as a member sees the 403 that member would.
    Vendor Tables (D3) is such a page, because every row on it is a contact.
    """

    def decorator(view: Callable[..., HttpResponseBase]) -> Callable[..., HttpResponseBase]:
        @card_view(kind)
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, hub: EventHub, card: HubCard, **kwargs: Any) -> HttpResponseBase:
            if not card.can_organize(viewer_member(request), is_admin=renders_as_admin(request)):
                raise PermissionDenied("Only the organisers of this part of the event can open this page.")
            return view(request, *args, hub=hub, card=card, **kwargs)

        return wrapped

    return decorator


def organizer_required(kind: str) -> Callable[[Callable[..., HttpResponseBase]], Callable[..., HttpResponseBase]]:
    """Admit admins and the ``kind`` card's named members, by their actual roles, on the hub at ``slug``.

    The view is called as ``view(request, hub=hub, card=card, **rest)``. Anyone else gets a 403,
    and a card the hub does not have is a 404.
    """

    def decorator(view: Callable[..., HttpResponseBase]) -> Callable[..., HttpResponseBase]:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, slug: str, **kwargs: Any) -> HttpResponseBase:
            hub = hub_for_action(slug, request)
            card = hub.cards.prefetch_related("named_members").filter(kind=kind).first()
            if card is None:
                raise Http404("No such card.")
            if not card.can_organize(actual_member(request), is_admin=is_actual_admin(request)):
                raise PermissionDenied("Only the organisers of this part of the event can do this.")
            return view(request, *args, hub=hub, card=card, **kwargs)

        return wrapped

    return decorator


def can_check_in(card: HubCard, member: Member, *, is_admin: bool) -> bool:
    """Who may file a market report and log hours behind the Market Check-in card (D4): a signed in member the card
    is open to who is ACTIVE, an admin, or named on the card. Members only by design: there is no guest path, and
    identity is the member, never a typed email. A GUEST account the card is open to reads the page but files
    nothing. ``is_admin`` is the role being checked: the effective one to render, the actual one to act. Signed
    out is nobody to check, so callers ask only with a member.
    """
    if is_admin or card.is_named(member):
        return True
    return member.status == Member.Status.ACTIVE and card.is_open_to(member, is_admin=False)


def check_in_required(kind: str) -> Callable[[Callable[..., HttpResponseBase]], Callable[..., HttpResponseBase]]:
    """Admit whoever :func:`can_check_in` allows, by their actual role, on the hub at ``slug``.

    The view is called as ``view(request, hub=hub, card=card, member=member, **rest)`` with the signed in member.
    A hidden card is a 404 for anyone but an actual admin (it never reveals itself); anyone else who may not
    check in gets a 403, signed out included, since every use is a POST.
    """

    def decorator(view: Callable[..., HttpResponseBase]) -> Callable[..., HttpResponseBase]:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, slug: str, **kwargs: Any) -> HttpResponseBase:
            hub = hub_for_action(slug, request)
            card = hub.cards.prefetch_related("named_members").filter(kind=kind).first()
            is_admin = is_actual_admin(request)
            if card is None or (card.is_hidden and not is_admin):
                raise Http404("No such card.")
            member = actual_member(request)
            if member is None or not can_check_in(card, member, is_admin=is_admin):
                raise PermissionDenied("Only Past Lives members can add a report or log hours here.")
            return view(request, *args, hub=hub, card=card, member=member, **kwargs)

        return wrapped

    return decorator
