"""Shared template context for every page that extends hub/base.html."""

from __future__ import annotations

import re
from functools import partial
from typing import Any

from django.http import HttpRequest
from django.utils import timezone
from django.utils.functional import SimpleLazyObject

from hub.spotlight import Spotlight, sees_spotlight
from kiln.access import KilnNav
from membership.models import AdminCapability, Guild, Member

# The per-class management screen an admin lands on. After #399 that is
# ``/classes/teach/classes/<pk>/…`` for everyone — the old ``/classes/admin/<pk>/`` pages
# merged into it — so the Admin Tools entry has to recognise the new shape by path. The
# pattern is deliberately the PER-CLASS one and not the whole ``/classes/teach/`` prefix:
# an admin who also instructs still gets Teaching lit on the portal landing, their My
# Classes list and their Instructor Profile, which is the entry those pages belong to.
_MERGED_CLASS_PATH = re.compile(r"^/classes/teach/classes/\d+(?:/|$)")


def hub_sidebar(request: HttpRequest) -> dict[str, Any]:
    """Populate guilds + user initials for the hub sidebar.

    Lives at the project level so any view rendering a template that extends
    hub/base.html gets the sidebar data for free — without each view having
    to call a _get_hub_context helper. Returns empty values for anonymous
    requests so login/public pages don't hit the DB.

    ``request.user`` is read defensively. ``SurfaceMiddleware`` short-circuits member-only
    paths on the guest surfaces with an ``Http404`` *before* ``AuthenticationMiddleware``
    has run, and the themed ``templates/404.html`` renders every context processor — so on
    those responses the attribute does not exist yet. Same guard as ``core.persona``.
    """
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return {
            "guilds": Guild.objects.none(),
            "user_initials": "",
            "user_profile_photo_url": "",
            "can_create_classes": False,
            "teach_nav": None,
            "manage_bookings_nav": None,
            "classes_admin_nav_active": False,
            "classes_catalog_active_class": "",
            "kiln_nav": KilnNav(None),
            "spotlight": None,
            "hq_nav": False,
        }

    initials = ""
    photo_url = ""
    member: Member | None = getattr(user, "member", None)
    if member is not None:
        initials = member.initials
        if member.profile_photo:
            photo_url = member.profile_photo.url
    admin_nav_active = _classes_admin_nav_active(request)
    teach_nav = _teach_nav(request, member, admin_nav_active)
    return {
        # Inactive guilds are hidden everywhere they're listed (directory, voting,
        # My Guilds) — the sidebar follows suit. Their detail pages stay reachable
        # by direct link, which is how the Help Center's example guild works.
        "guilds": Guild.objects.filter(is_active=True).order_by("name"),
        "user_initials": initials,
        "user_profile_photo_url": photo_url,
        "can_use_admin_tools": _can_use_admin_tools(request, member),
        "view_as_capabilities": _admin_capability_rows(request, member),
        "view_as_instructor": _instructor_row(request, member),
        "can_create_classes": member is not None and member.can_create_classes,
        "teach_nav": teach_nav,
        "manage_bookings_nav": _manage_bookings_nav(request),
        "classes_admin_nav_active": admin_nav_active,
        "classes_catalog_active_class": _classes_catalog_active_class(request, admin_nav_active, teach_nav),
        # The guest menu and the kiln pages' way back (#691); no query.
        "kiln_nav": KilnNav(member),
        # Lazy as well (#709): the Spotlight's two queries run only when a page renders it, once per request.
        # Who sees it: hub.spotlight.sees_spotlight (members, not guests or former members).
        "spotlight": SimpleLazyObject(partial(request_spotlight, request)) if shows_spotlight(request) else None,
        # The Holiday Bizarre entry: one query, run only when the sidebar renders it.
        "hq_nav": SimpleLazyObject(partial(_hq_nav, request, member)),
    }


def _hq_nav(request: HttpRequest, member: Member | None) -> bool:
    """Whether the sidebar shows the Holiday Bizarre entry, in one query.

    An admin, by the effective role, sees it whenever any HQ exists (a closed one included, which only admins
    reach). Anyone else sees it only when the open HQ has a card they may open (``HubCardQuerySet.open_to``, the
    rule of ``HubCard.is_open_to``), so a member never gets an entry that lands on a 404. An admin previewing as a
    Guest is nobody, as ``hq.access.viewer_member`` has it. The same rule in every block of the sidebar, the guest
    accounts' included.
    """
    from hq.models import EventHub, HubCard

    view_as = getattr(request, "view_as", None)
    if view_as is not None and view_as.is_admin:
        return EventHub.objects.exists()
    viewer = None if view_as is not None and view_as.is_previewing and view_as.is_guest else member
    return HubCard.objects.filter(hub__is_open=True).open_to(viewer).exists()


def _spotlight() -> Spotlight:
    """The Spotlight now (#709); every member sees the same one."""
    return Spotlight.load(timezone.now())


def shows_spotlight(request: HttpRequest) -> bool:
    """Whether this request's pages carry the Spotlight: a signed in account that :func:`hub.spotlight.sees_spotlight` lets in."""
    user = getattr(request, "user", None)
    return bool(getattr(user, "is_authenticated", False)) and sees_spotlight(getattr(user, "member", None))


def request_spotlight(request: HttpRequest) -> Spotlight:
    """This request's Spotlight, read once however many places ask: Home's banner shares its announcements (#890)."""
    cached: Spotlight | None = getattr(request, "_pl_spotlight", None)
    if cached is None:
        cached = _spotlight()
        request._pl_spotlight = cached  # type: ignore[attr-defined]
    return cached


def _classes_admin_nav_active(request: HttpRequest) -> bool:
    """Whether the Admin Tools sidebar entry owns the current classes-management path.

    Admin Tools has claimed ``/classes/admin/`` ever since Manage Classes became one of its
    cards. #399 merges the per-class admin screen into the instructor's own
    ``/classes/teach/classes/<pk>/…`` page, so the same admin doing the same job now stands on
    a ``/classes/teach/`` path — which is where ``_teach_nav`` lights. Deciding it here rather
    than in the template keeps the two entries reading one answer, so they can never both light.

    The merged path only belongs to Admin Tools for a viewer in the admin capability set, and
    ``request.view_as.is_admin`` is the EFFECTIVE read (ruling 9): an admin previewing as a
    member is previewing the member's sidebar, and gets Teaching lit like the member would.
    An instructor on that same page is simply an instructor, and keeps Teaching.

    ``request.view_as`` is read defensively for the same reason the rest of this module does:
    a 404 rendered before ``ViewAsMiddleware`` has run has no such attribute.
    """
    path = request.path
    if path.startswith("/classes/admin/"):
        return True
    view_as = getattr(request, "view_as", None)
    return view_as is not None and view_as.is_admin and _MERGED_CLASS_PATH.match(path) is not None


def _classes_catalog_active_class(
    request: HttpRequest, admin_nav_active: bool, teach_nav: dict[str, Any] | None
) -> str:
    """``"active"`` when the Classes sidebar entry owns the current page, else ``""``.

    Catalog lights on every ``/classes/`` path EXCEPT the two other entries carve out of it:
    a class-management path, which belongs to Admin Tools (:func:`_classes_admin_nav_active`),
    and the teaching portal, which belongs to Teaching (:func:`_teach_nav`). Exactly one of the
    three is ever lit.

    This used to be an inline ``{% if %}`` in base.html, written out twice. #405 routes the entry
    through ``_sidebar_feature_link.html`` so all three switch states come from one call site,
    and a ``{% include ... with %}`` argument cannot hold an ``{% if %}`` — so the answer is
    computed here as a CSS class, the same shape ``teach_nav["active_class"]`` already uses.
    """
    if not request.path.startswith("/classes/"):
        return ""
    if admin_nav_active or (teach_nav is not None and teach_nav["is_active"]):
        return ""
    return "active"


def _teach_nav(request: HttpRequest, member: Member | None, admin_nav_active: bool) -> dict[str, Any] | None:
    """The sidebar's Teaching entry, or ``None`` for anyone who is not an active member.

    Every ACTIVE member gets the entry now. Teaching is something we recruit for, and a
    member who cannot see the door cannot knock on it — the entry used to be gated on
    ``can_create_classes``, which meant the only people who could find the teaching
    pages were the people who already had them. ``classes:teach_overview`` is the one
    destination and it branches by itself, showing the teaching dashboard to a member
    who can teach and the "Host a Class" page (with I'm Interested) to everyone else.
    The label follows the same split: an instructor reads "Teaching", everyone else
    reads "Host a Class", the invitation rather than the portal.

    Deliberately NOT gated on ``is_instructor`` (the public profile slug): that is the
    Instructor *role*, and someone can hold the portal unlock without a slug, which would
    leave them with access and no way in. Active on every ``/classes/teach/`` path, which
    the Classes entry excludes — except the one an admin owns instead, which is
    ``admin_nav_active`` (see :func:`_classes_admin_nav_active`); the two entries never
    both light.

    The ``teach`` feature switch (``core.features``) governs the entry, and can only reach the
    NON-teaching branch: a member who cannot teach yet loses the invitation, an instructor
    keeps the portal in all three states. Gating both would lock every instructor out of their
    own teaching pages, which is not what a visibility switch is for. That is what ``teaches``
    says, and the template pairs it with the feature's state.

    The state is deliberately NOT read here. ``core.context_processors.feature_flags`` already
    puts every feature in the same context as ``features``, and reading it a second time costs
    one extra query on EVERY page in the app, for every member who cannot teach yet, which is
    most of them. ``tests/hub/wiki_views_spec.py``'s home-page query budget catches it. So this
    returns the entry and the two facts the template cannot derive — whether they teach, and
    whether this entry owns the current page — and the sidebar renders it against the state it
    was already given.
    """
    from django.urls import reverse

    if member is None or member.status != Member.Status.ACTIVE:
        return None
    is_active = request.path.startswith("/classes/teach/") and not admin_nav_active
    return {
        "label": "Teaching" if member.can_create_classes else "Host a Class",
        "url": reverse("classes:teach_overview"),
        "is_active": is_active,
        # The same answer as a CSS class, so the entry can be handed to the shared
        # _sidebar_feature_link.html include — a `with` argument cannot hold an {% if %}.
        "active_class": "active" if is_active else "",
        # False means this is the "Host a Class" invitation, which features.teach governs.
        "teaches": member.can_create_classes,
    }


def _manage_bookings_nav(request: HttpRequest) -> dict[str, Any] | None:
    """The sidebar's Manage Bookings entry (#768), or ``None`` for anyone who runs nothing.

    One home for everyone who runs orientations or equipment, the way Teaching is for classes.
    The gate is :func:`membership.permissions.runs_bookings`: an admin or officer (``view_as``
    aware, so a preview shows the member's sidebar, and no query), else a member who leads or
    staffs a guild or holds an equipment staff row, in ONE query. That query is the whole cost
    the entry adds to every hub page for a member with nothing to run, and
    ``tests/hub/wiki_views_spec.py``'s home page budget holds it there. No feature switch: an
    admin keeps the entry with Orientations and Reservations both off.

    Active on the home and every path under it, the same shape as :func:`_teach_nav`.
    """
    from django.urls import reverse

    from membership.permissions import runs_bookings

    if not runs_bookings(request):
        return None
    is_active = request.path.startswith("/manage/bookings/")
    return {
        "url": reverse("hub_manage_bookings"),
        "is_active": is_active,
        "active_class": "active" if is_active else "",
    }


def _admin_capability_rows(request: HttpRequest, member: Member | None) -> list[dict[str, Any]]:
    """Rows for the "View As" dropdown's self-service admin-duty toggles.

    Returns one ``{value, label, checked, description}`` dict per :class:`AdminCapability` for an
    ACTUAL admin (``request.view_as.actual_is_admin`` — a view-as preview can't unlock
    it), and an empty list otherwise. ``checked`` reflects the current member's own held
    capabilities so the toggles start in the right state. ``description`` is the shared
    ``AdminCapability.DESCRIPTIONS`` line the member edit page also shows, rendered here as
    the "?" tooltip beside each duty.
    """
    view_as = getattr(request, "view_as", None)
    if member is None or view_as is None or not view_as.actual_is_admin:
        return []
    held = set(member.admin_capabilities.values_list("capability", flat=True))
    return [
        {
            "value": value,
            "label": label,
            "checked": value in held,
            "description": AdminCapability.DESCRIPTIONS[value],
        }
        for value, label in AdminCapability.Capability.choices
    ]


def _instructor_row(request: HttpRequest, member: Member | None) -> dict[str, Any] | None:
    """The "View As" dropdown's self-service Instructor toggle, or ``None`` when it has no place.

    Same audience and same realness as :func:`_admin_capability_rows`: an ACTUAL admin only
    (a view-as preview can't unlock it), and flipping it is a REAL grant on their own member,
    not a preview. Mirrors the member edit Permissions tab, where Instructor is one unified
    permission sitting above the admin capabilities — so the dropdown puts it in the same
    place, above the duty toggles, and shows the same shared
    ``Member.INSTRUCTOR_PERMISSION_DESCRIPTION`` copy that page does.
    """
    view_as = getattr(request, "view_as", None)
    if member is None or view_as is None or not view_as.actual_is_admin:
        return None
    return {"checked": member.is_instructor, "description": Member.INSTRUCTOR_PERMISSION_DESCRIPTION}


def _can_use_admin_tools(request: HttpRequest, member: Member | None) -> bool:
    """Whether to show the Admin Tools sidebar entry.

    Delegates to the page's own gate (``hub.views._can_use_admin_tools``) so the entry and the
    page can never disagree: anyone whose elevated perms unlock a tool sees it (admin, guild
    lead/staff, or instructor), and an actual admin previewing as a plain member does not.
    """
    from hub.views import _can_use_admin_tools as _gate

    return _gate(request, member)
