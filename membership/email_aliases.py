"""Email-alias operations on a linked member's allauth ``EmailAddress`` rows.

The single home for the *rules* of staff-managed email aliases — add, remove,
set-primary, toggle-verified — so both the legacy Django-admin page
(``plfog/admin_views.py``) and the hub-native member edit page
(``hub/views.py``) share one implementation instead of drifting.

Each function performs its mutation and returns a list of ``(level, message)``
flashes for the caller to surface via ``django.contrib.messages`` — ``level`` is
a messages method name (``"success"`` / ``"error"`` / ``"warning"``). A leading
``"error"`` flash means nothing was changed.

THREE-EMAIL-STORE NOTE: these helpers operate only on allauth ``EmailAddress``.
They never touch ``Member._pre_signup_email`` or ``MemberEmail`` staging rows.
See docs/superpowers/specs/2026-04-07-user-email-aliases-design.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from allauth.account.models import EmailAddress

Flashes = list[tuple[str, str]]


def add_alias(user: Any, email: str) -> Flashes:
    """Create a verified, non-primary alias for ``user``. ``email`` must already be validated.

    ``user`` is typed ``Any`` to match the domain's ``AddEmailAliasForm``: callers
    pass ``member.user`` (``User | None``) after a ``user_id`` guard, and a linked
    member always has a user here.
    """
    from allauth.account.models import EmailAddress

    EmailAddress.objects.create(user=user, email=email, verified=True, primary=False)
    return [("success", f"Added alias '{email}'.")]


def remove_alias(alias: EmailAddress) -> Flashes:
    """Delete an alias unless it's the user's only one.

    If the removed row was primary and a verified email remains, the lowest-pk
    verified row is promoted to primary; if none remains, a loud warning is
    flashed (the user can no longer log in until one is verified).
    """
    from allauth.account.models import EmailAddress

    user = alias.user
    if EmailAddress.objects.filter(user=user).count() == 1:
        return [
            (
                "error",
                f"Cannot remove '{alias.email}' — it's the only email on this account. "
                "Removing it would lock the member out.",
            )
        ]

    was_primary = alias.primary
    alias_email = alias.email
    alias.delete()

    flashes: Flashes = []
    if was_primary:
        next_verified = EmailAddress.objects.filter(user=user, verified=True).order_by("pk").first()
        if next_verified is not None:
            next_verified.set_as_primary(conditional=False)
        else:
            flashes.append(
                (
                    "warning",
                    "This member has no verified emails left and cannot log in. Add and verify one immediately.",
                )
            )
    flashes.append(("success", f"Removed alias '{alias_email}'."))
    return flashes


def set_primary(alias: EmailAddress) -> Flashes:
    """Promote a verified alias to primary (demoting the old one and syncing ``User.email``)."""
    if not alias.verified:
        return [("error", f"Cannot set '{alias.email}' as primary — it isn't verified yet.")]
    alias.set_as_primary(conditional=False)
    return [("success", f"'{alias.email}' is now the primary email.")]


def toggle_verified(alias: EmailAddress) -> Flashes:
    """Flip the verified flag, warning loudly if it just un-verified the primary email."""
    alias.verified = not alias.verified
    alias.save(update_fields=["verified"])

    if not alias.verified and alias.primary:
        return [
            (
                "warning",
                f"'{alias.email}' is the primary email and is now un-verified. "
                "Login will still work until another email is promoted, but this is fragile.",
            )
        ]
    state = "verified" if alias.verified else "un-verified"
    return [("success", f"'{alias.email}' is now {state}.")]
