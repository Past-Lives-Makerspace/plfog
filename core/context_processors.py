"""Template context processors for core app."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest


def registration_mode(request: HttpRequest) -> dict[str, bool]:
    """Add registration_is_open flag to template context."""
    from core.models import SiteConfiguration

    config = SiteConfiguration.load()
    return {"registration_is_open": config.registration_mode == SiteConfiguration.RegistrationMode.OPEN}


def app_version(request: HttpRequest) -> dict[str, Any]:
    """Add app version and changelog to template context."""
    from plfog.version import CHANGELOG, VERSION

    return {"app_version": VERSION, "changelog": CHANGELOG}


def surface(request: HttpRequest) -> dict[str, str | bool]:
    """Expose which surface the request arrived on so templates can branch chrome.

    ``surface`` is ``"public"`` on book.pastlives.space and ``"members"``
    everywhere else (members host, local dev, Hetzner staging, Render preview).
    ``is_public_surface`` is the convenience boolean templates branch on.
    """
    value = getattr(request, "surface", "members")
    return {"surface": value, "is_public_surface": value == "public"}


def google_analytics(request: HttpRequest) -> dict[str, str]:
    """Expose the GA4 measurement ID site-wide.

    Returns an empty string on the Django admin so analytics never fire on
    internal back-office pages. The base template gates the gtag block on
    the truthy value, so an empty string acts as "disabled".
    """
    if request.path.startswith("/admin/"):
        return {"google_analytics_measurement_id": ""}
    from core.models import SiteConfiguration

    return {"google_analytics_measurement_id": SiteConfiguration.load().google_analytics_measurement_id}


def persona(request: HttpRequest) -> dict[str, str | bool]:
    """Derive the active persona for the current request.

    Returns a single string in {"anon", "nonmember", "member", "instructor"} plus
    convenience booleans so templates can render banners and topbar variants
    without re-deriving. Cached on the request so it's safe to call from both
    the context processor and view code.
    """
    cached = getattr(request, "_persona", None)
    if cached is not None:
        return cached

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        result: dict[str, str | bool] = {"persona": "anon", "is_member_persona": False, "is_instructor_persona": False}
        request._persona = result
        return result

    from membership.models import Member

    member = getattr(user, "member", None)
    is_active_member = bool(member and member.status == Member.Status.ACTIVE)
    is_instructor = hasattr(user, "instructor")

    if is_active_member:
        slug = "member"
    elif is_instructor:
        slug = "instructor"
    else:
        slug = "nonmember"

    result = {
        "persona": slug,
        "is_member_persona": is_active_member,
        "is_instructor_persona": is_instructor,
    }
    request._persona = result
    return result
