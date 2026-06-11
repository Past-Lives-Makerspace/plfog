from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django import template

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from membership.models import Guild

register = template.Library()


@register.simple_tag(takes_context=True)
def active_nav(context: dict[str, Any], *args: str | int) -> str:
    """Return 'active' if the current URL matches any of the given URL names.

    Examples:
        {% active_nav 'hub_guild_voting' %}
        {% active_nav 'hub_guild_detail' guild.pk %}
        {% active_nav 'hub_tab_detail' 'hub_tab_history' %}
    """
    request = context.get("request")
    if request is None:
        return ""
    from django.urls import reverse

    url_names: list[str] = []
    pk: int | None = None
    for arg in args:
        if isinstance(arg, int):
            pk = arg
        else:
            url_names.append(arg)

    for name in url_names:
        target = reverse(name, args=[pk] if pk is not None else [])
        if request.path == target:
            return "active"
    return ""


@register.filter
def get_item(dictionary: dict, key: str) -> Any:
    """Look up a key in a dict: {{ my_dict|get_item:key }}"""
    return dictionary.get(str(key))


@register.filter
def is_public(member: Any, field_name: str) -> bool:
    """Return whether a member has marked the given directory field as public.

    Usage: ``{% if member|is_public:"phone" %}…{% endif %}``
    """
    if member is None:
        return False
    return bool(member.is_public(field_name))


@register.simple_tag(takes_context=True)
def has_active_guild(context: dict[str, Any], guilds: QuerySet[Guild]) -> bool:
    """Return True if the current page is a guild detail page."""
    request = context.get("request")
    if request is None:
        return False
    from django.urls import reverse

    for guild in guilds:
        if request.path == reverse("hub_guild_detail", args=[guild.pk]):
            return True
    return False


@register.filter
def guild_logo_prefix(name: str) -> str | None:
    """Map a guild name string to its logo prefix."""
    name = name.lower()
    mapping = {
        "art framing": "art_framing",
        "ceramics": "ceramics",
        "events": "events",
        "food independence": "food_independence",
        "garden": "garden",
        "glass": "glass",
        "jewelry": "jewelers",
        "jeweler": "jewelers",
        "leather": "leatherwork",
        "metal": "metalworking",
        "prison": "prison_outreach",
        "tech": "tech",
        "textile": "textiles",
        "visual": "visual_arts",
        "wood": "woodworking",
        "writer": "writers",
    }
    for key, prefix in mapping.items():
        if key in name:
            return prefix
    return None
