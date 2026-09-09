"""Shared template context for every page that extends hub/base.html."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from membership.models import AdminCapability, Guild, Member


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
        }

    initials = ""
    photo_url = ""
    member: Member | None = getattr(user, "member", None)
    if member is not None:
        initials = member.initials
        if member.profile_photo:
            photo_url = member.profile_photo.url
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
        "teach_nav": _teach_nav(request, member),
    }


def _teach_nav(request: HttpRequest, member: Member | None) -> dict[str, Any] | None:
    """The sidebar's Teaching entry, or ``None`` for anyone who is not an active member.

    Every ACTIVE member gets the entry now. Teaching is something we recruit for, and a
    member who cannot see the door cannot knock on it — the entry used to be gated on
    ``can_create_classes``, which meant the only people who could find the teaching
    pages were the people who already had them. ``classes:teach_overview`` is the one
    destination and it branches by itself, showing the teaching dashboard to a member
    who can teach and the "Host a Workshop" page (with I'm Interested) to everyone else.
    The label follows the same split: an instructor reads "Teaching", everyone else
    reads "Host a Workshop", the invitation rather than the portal.

    Deliberately NOT gated on ``is_instructor`` (the public profile slug): that is the
    Instructor *role*, and someone can hold the portal unlock without a slug, which would
    leave them with access and no way in. Active on every ``/classes/teach/`` path, which
    the Class Catalog entry excludes.
    """
    from django.urls import reverse

    if member is None or member.status != Member.Status.ACTIVE:
        return None
    return {
        "label": "Teaching" if member.can_create_classes else "Host a Workshop",
        "url": reverse("classes:teach_overview"),
        "is_active": request.path.startswith("/classes/teach/"),
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
