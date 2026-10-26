"""Template helpers for polls (#925 part 3)."""

from __future__ import annotations

from django import template

from polls.forms import WriteInForm
from polls.models import PollCard

register = template.Library()


@register.filter
def write_in_form(card: PollCard) -> WriteInForm:
    """The unbound write your own form for ``card``'s poll, prefixed by the poll so ids stay unique."""
    return WriteInForm.for_poll(card.poll)
