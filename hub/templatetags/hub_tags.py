from __future__ import annotations

from typing import Any

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def active_nav(context: dict[str, Any], url_name: str, pk: int | None = None) -> str:
    """Return 'active' if the current URL matches the given URL name."""
    request = context.get("request")
    if request is None:
        return ""
    from django.urls import reverse

    if pk is not None:
        target = reverse(url_name, args=[pk])
    else:
        target = reverse(url_name)
    return "active" if request.path == target else ""
