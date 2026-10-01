"""Template tags exposing site base URLs to templates that render without a request.

Emails render via ``render_to_string`` (no request → no context processors), so a
template like ``membership/emails/_footer.html`` can't reach ``settings`` for the
member-hub host. ``{% member_base_url %}`` reads it directly, so every email footer
points at the configured host (``pastlives.app`` in prod) instead of a hardcoded one.
``{% google_play_url %}`` / ``{% app_store_url %}`` (#467) do the same for the two store
listings behind the footer's "Get the app" badges; pages read the same fields through the
``brand()`` context processor.
"""

from __future__ import annotations

from django import template
from django.conf import settings

from core.models import SiteConfiguration

register = template.Library()


@register.simple_tag
def member_base_url() -> str:
    """The absolute base URL of the member hub, e.g. ``https://pastlives.app`` (no trailing slash)."""
    return settings.MEMBER_BASE_URL


@register.simple_tag
def google_play_url() -> str:
    """The Google Play listing from Site Settings, or ``""`` while Android is not launched."""
    return SiteConfiguration.load().google_play_url


@register.simple_tag
def app_store_url() -> str:
    """The App Store listing from Site Settings, or ``""`` while iOS is not launched."""
    return SiteConfiguration.load().app_store_url
