"""Shared fixtures for the event-spine (core.events) specs.

The resolvers only yield Users that carry a usable email, so the central helper
here is :func:`linked_member` — a Member with a freshly created, email-bearing
User attached. Tests build their audiences out of these.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from tests.membership.factories import MemberFactory

# Sentinel: distinguishes "caller didn't specify last_login" (→ activated by default)
# from an explicit ``last_login=None`` (→ a member who has never signed in).
_UNSET = object()


@pytest.fixture
def linked_member():
    """Factory fixture: a Member with a linked, email-bearing User.

    Usage: ``m = linked_member(email="a@x.com")`` or just ``linked_member()``.
    The returned Member has ``.user`` set so the resolvers can address it.

    Signals are muted while creating the User so the ``ensure_user_has_member``
    post-save signal does not auto-create a *second* Member for it (which would
    collide on the one-to-one ``user`` key); we attach the User to our own Member
    instead.

    The User is "activated" (``last_login`` set) by default so it's addressable by
    the broadcast resolvers, which skip never-signed-in accounts. Pass
    ``last_login=None`` to build a member who has never logged in.
    """
    counter = {"n": 0}

    def _make(*, email: str | None = None, last_login: object = _UNSET, **member_kwargs) -> object:
        counter["n"] += 1
        n = counter["n"]
        member = MemberFactory(**member_kwargs)
        resolved_last_login = timezone.now() if last_login is _UNSET else last_login
        with mute_signals(post_save):
            user = User.objects.create_user(
                username=f"evt_user_{n}_{member.pk}",
                email=email if email is not None else f"evt{n}_{member.pk}@example.com",
            )
            if resolved_last_login is not None:
                user.last_login = resolved_last_login
                user.save(update_fields=["last_login"])
        member.user = user
        member.save(update_fields=["user"])
        return member

    return _make
