"""Template tags exposing site base URLs to templates that render without a request.

Emails render via ``render_to_string`` (no request → no context processors), so a
template like ``membership/emails/_footer.html`` can't reach ``settings`` for the
member-hub host. ``{% member_base_url %}`` reads it directly, so every email footer
points at the configured host (``pastlives.app`` in prod) instead of a hardcoded one.
"""

from __future__ import annotations

from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def member_base_url() -> str:
    """The absolute base URL of the member hub, e.g. ``https://pastlives.app`` (no trailing slash)."""
    return settings.MEMBER_BASE_URL
