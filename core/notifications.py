"""Notification audience helpers.

The hand-sequenced ``activity.log()`` + ``dispatch()`` fan-out is gone — every send
now flows through :func:`core.events.emit.emit` (the event spine). What remains here
is the one broadcast-audience helper the spine still reuses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.db.models import QuerySet


def active_member_users() -> "QuerySet[User]":
    """All active members' User objects — the default broadcast audience.

    Reused by :func:`core.events.resolvers.all_active_members` (the
    ``ALL_ACTIVE_MEMBERS`` resolver behind site/class broadcasts).
    """
    from django.contrib.auth.models import User

    return User.objects.filter(member__status="active")
