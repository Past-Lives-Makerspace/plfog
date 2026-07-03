"""Template context processors for core app."""

from __future__ import annotations

from typing import Any

from django.conf import settings
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


def theme(request: HttpRequest) -> dict[str, str]:
    """Expose the theme cookie's domain scope to base.html's early inline script.

    The script writes the light/dark choice to the ``pl_theme`` cookie so an
    explicit choice survives navigation across subdomains of the registrable
    domain. Empty string → a host-only cookie (correct for local dev like
    ``pastlives.test``); production sets ``THEME_COOKIE_DOMAIN=.pastlives.app``
    so the member hub and the guilds surface share the choice.
    """
    return {"theme_cookie_domain": settings.THEME_COOKIE_DOMAIN}


def feature_flags(request: HttpRequest) -> dict[str, Any]:
    """Expose the Site Settings → Features toggles site-wide (members + public)."""
    from core.models import SiteConfiguration

    config = SiteConfiguration.load()
    return {
        "tab_payments_enabled": config.tab_payments_enabled,
        "class_registration_enabled": config.class_registration_enabled,
        "class_registration_disabled_note": config.class_registration_disabled_note,
    }


def surface(request: HttpRequest) -> dict[str, str | bool]:
    """Expose which surface the request arrived on so templates can branch chrome.

    ``surface`` is ``"public"`` on book.pastlives.space and ``"members"``
    everywhere else (members host, local dev, Hetzner staging, Render preview).
    ``is_public_surface`` is the convenience boolean templates branch on.
    ``parent_template`` lets allauth templates pick their base via
    ``{% extends parent_template %}`` without forking the template files.
    """
    value = getattr(request, "surface", "members")
    is_public = value == "public"
    return {
        "surface": value,
        "is_public_surface": is_public,
        "MEMBER_HOST": settings.MEMBER_HOST,
        "MEMBER_BASE_URL": getattr(settings, "MEMBER_BASE_URL", f"https://{settings.MEMBER_HOST}"),
        "BOOK_BASE_URL": getattr(settings, "BOOK_BASE_URL", "https://book.pastlives.space"),
        "parent_template": "classes/base_public.html" if is_public else "base.html",
    }


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


def notification_badge(request: HttpRequest) -> dict[str, int]:
    """Unread notification count for the topbar bell. 0 for anonymous users."""
    user = request.user
    if not user.is_authenticated:
        return {"unread_notification_count": 0}
    from core.models import Notification

    count = Notification.objects.filter(user=user, read_at__isnull=True).count()
    return {"unread_notification_count": count}


def persona(request: HttpRequest) -> dict[str, str | bool]:
    """Derive the active persona for the current request.

    Returns a single string in {"anon", "nonmember", "member", "instructor"} plus
    convenience booleans so templates can render banners and topbar variants
    without re-deriving. Cached on the request so it's safe to call from both
    the context processor and view code.

    The "member" persona is reserved for users whose Member record was imported
    from Airtable (``airtable_record_id`` set). Auto-created shell Members from
    the ``ensure_user_has_member`` signal — created for everyone who signs up
    on book.pastlives.space without paying dues — read as "nonmember" here.
    Airtable is the authoritative roster for real dues-paying members.
    """
    cached = getattr(request, "_persona", None)
    if cached is not None:
        return cached

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        result: dict[str, str | bool] = {"persona": "anon", "is_member_persona": False, "is_instructor_persona": False}
        request._persona = result  # type: ignore[attr-defined]
        return result

    from membership.models import Member

    member = getattr(user, "member", None)
    is_active_member = bool(member and member.status == Member.Status.ACTIVE and member.airtable_record_id)
    is_instructor = bool(member and member.instructor_slug)

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
    request._persona = result  # type: ignore[attr-defined]
    return result
