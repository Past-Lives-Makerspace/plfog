"""Shared template context for every page that extends hub/base.html."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from membership.models import Guild, Member


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
        return {"guilds": Guild.objects.none(), "user_initials": "", "user_profile_photo_url": ""}

    initials = ""
    photo_url = ""
    member: Member | None = getattr(user, "member", None)
    if member is not None:
        initials = member.initials
        if member.profile_photo:
            photo_url = member.profile_photo.url
    return {
        "guilds": Guild.objects.order_by("name"),
        "user_initials": initials,
        "user_profile_photo_url": photo_url,
    }
