"""Read-side selectors for the membership domain (query helpers, no side effects).

Kept separate from :mod:`membership.models` so cross-app callers (e.g. ``classes`` and
the Discord link flow) can share one canonical lookup without importing view code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from membership.models import Member


def member_for_verified_email(email: str) -> Member | None:
    """The single Member whose linked User owns this email as a **verified** address.

    Matches against the member's current verified ``allauth`` ``EmailAddress`` rows
    (case-insensitive), so a stale address that is no longer verified never resolves.
    ``ACCOUNT_UNIQUE_EMAIL=True`` guarantees at most one match, so a match is
    unambiguous; a pre-signup member with no linked User is never found (they have no
    ``EmailAddress``).

    Returns the matching Member, or ``None`` when no verified account owns the email.
    """
    from membership.models import Member

    return (
        Member.objects.filter(
            user__emailaddress__email__iexact=email,
            user__emailaddress__verified=True,
        )
        .distinct()
        .first()
    )
