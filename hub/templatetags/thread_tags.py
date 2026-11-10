"""Template helpers for changing a posted thread message (#1140): who sees its menu, and its edit box.

``{% load thread_tags %}`` in ``components/thread_message.html`` and ``kiln/partials/_reply.html``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django import template

if TYPE_CHECKING:
    from hub.forms import ThreadMessageEditForm

register = template.Library()


@register.filter
def may_change(message: Any, request: Any) -> bool:
    """Whether ``request`` may edit and delete ``message``: its author or an admin.

    A feedback request's opening (no pk) is never a message to change, and a signed out reader
    (a class review link) sees no menu.
    """
    from membership.permissions import can_change_message

    if not request.user.is_authenticated or getattr(message, "pk", None) is None:
        return False
    return can_change_message(request, message.author_user_id)


@register.simple_tag
def message_edit_form(message: Any, bound: ThreadMessageEditForm | str | None = None) -> ThreadMessageEditForm:
    """The edit box's form for ``message``: ``bound`` when an edit was just refused, else one holding its text.

    A page with no refused edit passes the template's blank for a missing variable.
    """
    from hub.forms import ThreadMessageEditForm

    if isinstance(bound, ThreadMessageEditForm):
        return bound
    return ThreadMessageEditForm(
        initial={"body": message.body},
        photo_ids=message.photo_ids,
        body_max_length=getattr(message, "BODY_MAX_LENGTH", None),
        auto_id=f"{message.dom_id}-%s",
    )
