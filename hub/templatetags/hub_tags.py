from __future__ import annotations

import re

from typing import TYPE_CHECKING, Any

from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import SafeString, mark_safe

from membership.logos import logo_prefix_for

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from membership.models import Guild

register = template.Library()


@register.simple_tag(takes_context=True)
def active_nav(context: dict[str, Any], *args: str | int) -> str:
    """Return 'active' if the current URL matches any of the given URL names.

    Examples:
        {% active_nav 'hub_guild_voting' %}
        {% active_nav 'hub_guild_detail' guild.slug %}
        {% active_nav 'hub_tab_detail' 'hub_tab_history' %}
    """
    request = context.get("request")
    if request is None:
        return ""
    from django.urls import NoReverseMatch, get_resolver, reverse

    # Args are a mix of URL *names* and reverse *args* (a pk or a slug). Type can't tell
    # a name from a slug — both are strings — so ask the resolver: a registered pattern
    # name is a name; anything else (e.g. "ceramics-guild") is a reverse arg.
    reverse_dict = get_resolver().reverse_dict
    url_names: list[str] = []
    reverse_args: list[Any] = []
    for arg in args:
        if isinstance(arg, str) and arg in reverse_dict:
            url_names.append(arg)
        else:
            reverse_args.append(arg)

    for name in url_names:
        try:
            target = reverse(name, args=reverse_args)
        except NoReverseMatch:
            continue
        if request.path == target:
            return "active"
    return ""


@register.simple_tag(takes_context=True)
def active_path(context: dict[str, Any], prefix: str) -> str:
    """Return 'active' when the current path starts with ``prefix``.

    The path-prefix companion to :func:`active_nav`, which can only match a reversible URL
    exactly. A section whose inner pages carry a pk (``/meetings/12/``) cannot be reversed
    without that pk, so its nav entry has always matched by prefix instead — this puts that
    test behind ``as`` so it can be handed to an include, which an inline ``{% if %}`` cannot.

    Examples:
        {% active_path '/meetings/' as meetings_active %}
    """
    request = context.get("request")
    if request is None:
        return ""
    return "active" if request.path.startswith(prefix) else ""


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


# An extension marker and its digits: "x12", "ext 12", "ext. 12", "extension 12", "#12". Not \b
# before the letter: "0199x12" has no word boundary there.
_EXTENSION = re.compile(r"(?<![a-z])(?:x|ext\.?|extension|#)\s*(\d+)", re.IGNORECASE)
_SECOND_NUMBER = re.compile(r"[,/]")
_DIGITS = frozenset("0123456789")


@register.filter
def tel_href(phone: str) -> str:
    """``phone`` as the number part of a ``tel:`` link: its digits, with a leading plus kept.

    Members type their number any way they like ("(503) 555 0199", "503.555.0199"); a dialer
    wants the digits. A ``+`` in front marks a country code and stays, so an international
    number still dials. An extension ("x12", "ext. 12", "#12", ", ext 12") rides along as RFC 3966's
    ``;ext=12`` instead of being dialled as part of the number, and of two numbers split by a
    comma or a slash only the first is linked. A value with no digits before any extension
    gives "", and the card shows it as text. ``_DIGITS`` and not ``str.isdigit``, which also
    takes superscripts.
    """
    first, *rest = _SECOND_NUMBER.split(phone, maxsplit=1)
    number = first
    extension = _EXTENSION.search(first)
    if extension is not None:
        number = first[: extension.start()]
    elif rest:
        extension = _EXTENSION.match(rest[0].strip())
    digits = "".join(ch for ch in number if ch in _DIGITS)
    if not digits:
        return ""
    href = f"+{digits}" if number.strip().startswith("+") else digits
    return f"{href};ext={extension.group(1)}" if extension is not None else href


@register.filter
def initials(name: str) -> str:
    """The first letter of the first two words of ``name``, upper-cased: "Lee Mendelsohn" → "LM".

    ``Member.initials`` reads the linked auth user and is blank for a member with no login,
    which a leadership card cannot show, so the cards work from the display name instead.
    A token that is only punctuation ("Sam / Samuel Rook") is skipped, not counted.
    """
    words = [word for word in name.split() if word[0].isalnum()]
    return "".join(word[0].upper() for word in words[:2])


@register.filter
def email_breaks(address: str) -> SafeString:
    """``address`` with a line-break opportunity after its ``@``.

    A leadership card is half a phone screen wide, and an address has no space to wrap on,
    so without this the browser breaks it mid-word. With a ``<wbr>`` it wraps as ``name@``
    over ``domain`` instead. The address is escaped first; only the ``<wbr>`` is trusted.
    """
    return mark_safe(conditional_escape(address).replace("@", "@<wbr>"))


@register.simple_tag(takes_context=True)
def has_active_guild(context: dict[str, Any], guilds: QuerySet[Guild]) -> bool:
    """Return True if the current page is a guild detail page."""
    request = context.get("request")
    if request is None:
        return False
    from django.urls import reverse

    for guild in guilds:
        if request.path == reverse("hub_guild_detail", args=[guild.slug]):
            return True
    return False


@register.filter
def by_kind(contacts: Any, kind: str) -> list[Any]:
    """Filter a list or queryset of MemberContact by their `kind`."""
    if not contacts:
        return []
    return [c for c in contacts if c.kind == kind]


@register.filter
def guild_logo_prefix(name: str) -> str | None:
    """Map a guild name string to its logo prefix, through the one shared name map."""
    return logo_prefix_for(name)


@register.filter
def required_fields(form: Any) -> str:
    """The names of a form's required, visible fields, space separated, for a formset row's completeness test.

    Formset rows render without the ``required`` attribute (Django switches it off so a blank
    extra row never blocks a submit), so the autosave script reads this instead to tell a half
    typed new row, posted as rendered and skipped, from a complete one that saves. Hidden fields
    (the row's ``id``, a parent key, a sort order with a default) are left out.
    """
    return " ".join(
        name
        for name, field in form.fields.items()
        if field.required and getattr(field.widget, "input_type", None) != "hidden"
    )
