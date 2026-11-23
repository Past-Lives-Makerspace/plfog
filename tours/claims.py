"""Tours find their visitor's account by verified email (Morlock, 2026-11-22).

Receivers for allauth's sign in, signup and email confirmed signals, connected in
:meth:`tours.apps.ToursConfig.ready`. Each hands the account to :func:`tours.booking.claim_tours`,
and none may stop a sign in: a failure is logged and the person signs in regardless.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _claim(user: Any) -> None:
    from tours import booking

    try:
        booking.claim_tours(user)
    except Exception:
        logger.exception("Could not link tours to user %s", getattr(user, "pk", None))


def on_sign_in(sender: Any, request: Any = None, user: Any = None, **kwargs: Any) -> None:
    """``user_logged_in`` and ``user_signed_up``: claim the tours under the account's verified emails."""
    if user is not None:
        _claim(user)


def on_email_confirmed(sender: Any, request: Any = None, email_address: Any = None, **kwargs: Any) -> None:
    """``email_confirmed``: a newly verified address may have tours under it."""
    if email_address is not None:
        _claim(email_address.user)
