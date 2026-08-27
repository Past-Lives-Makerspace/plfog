"""Stripe webhook handlers for orientation-booking Checkouts.

Registered into the billing app's webhook dispatcher fan-in. All handlers must
be idempotent — Stripe retries failed deliveries and may also fire the same
event more than once. Both handlers self-filter on ``metadata.kind`` so they
coexist with the classes handlers on the same events.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction

from membership.models import OrientationBooking

logger = logging.getLogger(__name__)


def handle_checkout_session_completed(event: dict[str, Any]) -> None:
    """Flip a paid orientation hold to REQUESTED and fire the full request fan-out.

    Only acts on sessions tagged ``kind=orientation_booking``. Idempotent:
    re-delivery on a booking no longer ``PENDING_PAYMENT`` is a no-op (and the
    fan-out's ``period`` dedupe backstops even that). Emails only ever go out
    for money in hand — the fan-out fires HERE, never at checkout start.
    """
    from membership import orientations

    session = event["data"]["object"]
    metadata = session.get("metadata") or {}
    if metadata.get("kind") != "orientation_booking":
        return

    booking_id = metadata.get("booking_id")
    if not booking_id:
        logger.warning("checkout.session.completed: missing booking_id in orientation metadata")
        return

    if session.get("payment_status") != "paid":
        logger.info(
            "checkout.session.completed: ignoring orientation session %s with payment_status=%s",
            session.get("id"),
            session.get("payment_status"),
        )
        return

    with transaction.atomic():
        try:
            booking = (
                OrientationBooking.objects.select_for_update()
                .select_related("slot", "guild", "member")
                .get(pk=booking_id)
            )
        except OrientationBooking.DoesNotExist:
            # With Stripe-verified release paths in place, this means a hold was
            # released while Stripe reported the session unpaid — the payment is
            # findable only in Stripe and this log line; refund from the dashboard.
            logger.error(
                "checkout.session.completed: PAID orientation session %s (payment intent %s) has no "
                "booking %s — money taken with no in-app trace. Refund from the Stripe dashboard.",
                session.get("id"),
                session.get("payment_intent"),
                booking_id,
            )
            return

        if booking.status != OrientationBooking.Status.PENDING_PAYMENT:
            return  # re-delivery no-op

        amount_total = session.get("amount_total")
        booking.status = OrientationBooking.Status.REQUESTED
        booking.stripe_session_id = session.get("id", booking.stripe_session_id)
        booking.stripe_payment_id = session.get("payment_intent", "") or ""
        if isinstance(amount_total, int):
            booking.amount_paid_cents = amount_total
        booking.save(update_fields=["status", "stripe_session_id", "stripe_payment_id", "amount_paid_cents"])

    # Fan-out outside the lock, like the classes handler — SMTP never runs
    # while the row is locked, and the period dedupe absorbs re-delivery.
    orientations._fan_out_request(booking)


def handle_checkout_session_expired(event: dict[str, Any]) -> None:
    """Delete the seat-holding booking of an expired orientation Checkout.

    Stripe only fires ``checkout.session.expired`` for sessions that were never
    completed, so this path is deletion-safe by definition. The primary release
    path for abandoned checkouts (~1 h after start, per the session's
    ``expires_at``); the sweep is the belt-and-braces backstop.
    """
    from membership import orientations

    session = event["data"]["object"]
    metadata = session.get("metadata") or {}
    if metadata.get("kind") != "orientation_booking":
        return

    booking_id = metadata.get("booking_id")
    if not booking_id:
        logger.warning("checkout.session.expired: missing booking_id in orientation metadata")
        return

    with transaction.atomic():
        booking = (
            OrientationBooking.objects.select_for_update()
            .select_related("slot")
            .filter(pk=booking_id, status=OrientationBooking.Status.PENDING_PAYMENT)
            .first()
        )
        if booking is None:
            return  # already released, recovered, or never existed — idempotent no-op
        orientations._delete_hold(booking)
