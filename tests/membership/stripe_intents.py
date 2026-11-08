"""Fake Stripe PaymentIntents for the orientation card hold specs (#1128).

``intent(...)`` is the dict :func:`billing.stripe_utils.retrieve_payment_intent` (and its capture
and cancel siblings) return. ``charged_at_once`` patches the read to a PaymentIntent that charged
at once, the checkout every spec written before #1128 describes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

READ = "billing.stripe_utils.retrieve_payment_intent"
CAPTURE = "billing.stripe_utils.capture_payment_intent"
CANCEL = "billing.stripe_utils.cancel_payment_intent"


def intent(
    status: str = "succeeded", amount: int = 1500, pi: str = "pi_hold", capture_before: int | None = None
) -> dict[str, Any]:
    """A PaymentIntent dict in ``status``: held amounts are capturable, charged ones received.

    ``capture_before`` is Stripe's capture deadline for the card hold, a unix timestamp, or ``None``.
    """
    return {
        "id": pi,
        "status": status,
        "amount": amount,
        "amount_capturable": amount if status == "requires_capture" else 0,
        "amount_received": amount if status == "succeeded" else 0,
        "capture_before": capture_before,
    }


@contextmanager
def charged_at_once(amount: int = 1500) -> Iterator[MagicMock]:
    """Every PaymentIntent read answers "charged ``amount``": a checkout opened before #1128."""
    with patch(READ, return_value=intent("succeeded", amount)) as read:
        yield read
