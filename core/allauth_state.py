"""Thread-local state for allauth signup coordination.

Used to prevent ``migrate_to_user`` from creating EmailAddress records
during allauth's ``save_user`` phase, before allauth's own ``setup_user_email``
assertion runs. Set the flag in the adapter; clear it immediately after.
"""

from __future__ import annotations

from threading import local

_local: local = local()


def is_in_allauth_signup() -> bool:
    return getattr(_local, "in_signup", False)


def enter_allauth_signup() -> None:
    _local.in_signup = True


def exit_allauth_signup() -> None:
    _local.in_signup = False
