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
