"""``{% new_badge %}`` and ``{% whats_new_button %}`` (#1007, part 3): what marks a thing as new.

``{% walkthrough_button %}`` (#1076) sits beside them: the always-available button that starts a
tool's own walkthrough, for the guild staff and admins it is for.

Both read the request's one :class:`core.whats_new.WhatsNew` (D9), so a page with twenty
badges pays the roles query once and a page with none pays nothing. A key is a feature in
:data:`core.guidance.GUIDANCE` or an area with one under it (``guild`` covers ``guild.*``).

A key registered nowhere is a typo, handled as help keys are: ``core.help_registry.entry``
raises on an unknown key and ``tests/hub/help_keys_spec.py`` holds every key a template
writes to the registry. Here the tag raises under ``DEBUG``, and in production logs and
renders nothing, so a badge never takes a page down; ``tests/hub/guidance_keys_spec.py`` is
the drift guard that fails CI on the typo.

Usage::

    {% load guidance %}
    <button class="vote-tab">Cubbies{% new_badge "guild.cubbies" %}</button>
    {% whats_new_button "guild" %}
    {% whats_new_button labelled=True %}
    {% walkthrough_button "guild-cubbies" guild %}
"""

from __future__ import annotations

import logging
from typing import Any

from django import template
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import format_html
from django.utils.safestring import SafeString

from core.guidance import registered, title_for
from core.whats_new import WhatsNew, whats_new_for

register = template.Library()
log = logging.getLogger(__name__)


def _news(context: dict[str, Any]) -> WhatsNew | None:
    """The request's What's New, or None outside a request (an email, a string rendered by hand)."""
    request = context.get("request")
    return whats_new_for(request) if request is not None else None


def _known(key: str) -> bool:
    """Whether ``key`` may be named at all; a typo raises in development and is logged in production."""
    if registered(key):
        return True
    if settings.DEBUG:
        raise KeyError(f"{key!r} is not a feature in core.guidance.GUIDANCE or an area of one")
    log.warning("guidance: unregistered key %r in a template", key)
    return False


@register.simple_tag(takes_context=True)
def new_badge(context: dict[str, Any], key: str) -> SafeString | str:
    """The New! badge after a label, only while the viewer has an unseen entry for ``key`` or a key under it.

    Nothing otherwise, so it is safe on every label. The badge carries its key for
    ``static/js/guidance.js``, which marks the key seen on a click and removes the badge.
    """
    news = _news(context)
    if news is None or not _known(key) or not news.has_unseen(key):
        return ""
    return format_html('<span class="pl-new" data-new-key="{}">New!</span>', key)


@register.simple_tag(takes_context=True)
def whats_new_button(context: dict[str, Any], key: str | None = None, labelled: bool = False) -> str:
    """The What's New button, for a viewer the modal is for.

    With ``key``, the icon button that opens What's New filtered to the key and everything under
    it: it renders only while an entry of any age names the key, and pulses while one is there
    that no open of What's New has covered (``Member.whats_new_opened``, not the badges' seen
    list: one open quiets it until the next release). Without a key, Home's button over every
    entry, ``labelled`` with its wordmark. The page includes the ``whats-new`` modal the button
    loads into.
    """
    news = _news(context)
    if news is None:
        return ""
    # The registry and the changelog first (no query): a page whose key no entry names pays no roles query.
    if key is not None and (not _known(key) or not news.has_entries(key)):
        return ""
    if not news.audiences:
        return ""
    return render_to_string(
        "hub/partials/_whats_new_button.html",
        {
            "key": key or "",
            "title": title_for(key) if key else "",
            "labelled": labelled,
            "unopened": news.has_unopened(key) if key else news.unopened,
        },
    )


@register.simple_tag(takes_context=True)
def walkthrough_button(context: dict[str, Any], tour_key: str, guild: Any = None) -> str:
    """The walkthrough button for ``tour_key``, only for a viewer who runs ``guild`` (or the tour is theirs).

    The one control that starts a guild staff walkthrough on its own tool
    (``components/walkthrough_button.html``): Take starts the tour on this page, and the choice
    is logged. With ``guild``, only someone who may edit that guild sees it (its lead and staff,
    and admins, ``view_as`` aware), so an ordinary member or another guild's lead never does;
    without one, anyone the tour's audience admits. An unknown key raises, as a typo should.
    """
    from core.tours import TOURS
    from membership.permissions import can_edit_guild

    tour = TOURS[tour_key]
    request = context.get("request")
    member = getattr(getattr(request, "user", None), "member", None) if request is not None else None
    if member is None:
        return ""
    if guild is not None:
        # Editing the guild implies the staff tours' audience; the guild page has already asked.
        allowed = context.get("can_edit_this_guild")
        if not (allowed if allowed is not None else can_edit_guild(request, guild)):
            return ""
    elif not tour.audience(member):
        return ""
    return render_to_string(
        "components/walkthrough_button.html", {"tour_key": tour_key, "start": request.get_full_path()}, request=request
    )
