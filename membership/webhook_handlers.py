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


def _flat_html(text: str) -> str:
    """Wrap a plain-text alert body in the branded email shell (the classes alert idiom)."""
    from django.utils.html import escape

    from core.events.templates import wrap_email_html

    blocks = [block for block in text.split("\n\n") if block.strip()]
    fragment = "".join("<p>" + escape(block).replace("\n", "<br>") + "</p>" for block in blocks)
    return wrap_email_html(fragment)


def _send_orphan_payment_alert(session: dict[str, Any], *, reason: str) -> None:
    """Email the Billing Administrators about a paid session with no live booking to credit.

    Mirrors the classes duplicate-payment alert: money moved with no in-app home
    is never allowed to be silent. The refund has to happen from the Stripe
    dashboard, so the alert links the payment directly.
    """
    from core import email as core_email
    from membership.models import AdminCapability, Member

    recipients = [
        member.primary_email
        for member in Member.objects.filter(
            admin_capabilities__capability=AdminCapability.Capability.BILLING_APPROVER
        ).distinct()
        if member.primary_email
    ]
    if not recipients:
        return
    payment_intent = session.get("payment_intent") or ""
    amount_total = session.get("amount_total")
    amount = f"${amount_total / 100:.2f}" if isinstance(amount_total, int) else "an unknown amount"
    stripe_url = (
        f"https://dashboard.stripe.com/payments/{payment_intent}"
        if payment_intent
        else "https://dashboard.stripe.com/payments"
    )
    body = (
        f"A paid orientation Checkout landed with no booking to credit.\n\n"
        f"{reason}\n\n"
        f"The member paid {amount} and has nothing in the app to show for it. "
        f"Refund the payment from the Stripe dashboard.\n\n"
        f"Stripe payment: {stripe_url}\n"
        f"Checkout session: {session.get('id', '')}\n"
        f"Customer email: {session.get('customer_email') or session.get('customer_details', {}).get('email', '')}"
    )
    core_email.send(
        to=recipients,
        subject="Orphaned orientation payment needs a manual refund",
        trigger_kind="membership.orientation_orphan_payment",
        text_body=body,
        html_body=_flat_html(body),
        best_effort=True,
    )


def handle_checkout_session_completed(event: dict[str, Any]) -> None:
    """Finalize a paid orientation hold via the one shared finalize path.

    Only acts on sessions tagged ``kind=orientation_booking``. Idempotent and
    race-safe: :func:`membership.orientations.finalize_paid_booking` re-checks
    the row under ``select_for_update``, so a re-delivery, a concurrent sweep
    tick, or a Resume click each finalize at most once. Emails only ever go out
    for money in hand — the fan-out fires HERE (inside finalize), never at
    checkout start.

    A paid session whose booking is missing, or whose booking advanced without
    ever recording a payment (no ``stripe_payment_id``), is NOT a re-delivery —
    it is money with no in-app home: logged at ERROR and alerted to the Billing
    Administrators for a Stripe-dashboard refund.
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

    booking = OrientationBooking.objects.select_related("slot", "guild", "member").filter(pk=booking_id).first()
    if booking is None:
        logger.error(
            "checkout.session.completed: PAID orientation session %s (payment intent %s) has no "
            "booking %s — money taken with no in-app trace. Refund from the Stripe dashboard.",
            session.get("id"),
            session.get("payment_intent"),
            booking_id,
        )
        _send_orphan_payment_alert(session, reason=f"Booking {booking_id} no longer exists.")
        return

    amount_total = session.get("amount_total")
    outcome = orientations.finalize_paid_booking(
        booking,
        payment_intent=session.get("payment_intent", "") or "",
        amount_total=amount_total if isinstance(amount_total, int) else None,
        session_id=session.get("id", "") or "",
    )
    if outcome == "gone":
        logger.error(
            "checkout.session.completed: PAID orientation session %s (payment intent %s) — booking %s "
            "vanished mid-finalize. Refund from the Stripe dashboard.",
            session.get("id"),
            session.get("payment_intent"),
            booking_id,
        )
        _send_orphan_payment_alert(session, reason=f"Booking {booking_id} was deleted while the payment landed.")
        return
    if outcome == "already":
        fresh = OrientationBooking.objects.filter(pk=booking.pk).first()
        if fresh is None or not fresh.stripe_payment_id:
            # The booking advanced without a payment ever being recorded — a paid
            # session landing here is NOT a webhook re-delivery. Money with no
            # refund anchor: shout.
            logger.error(
                "checkout.session.completed: PAID orientation session %s (payment intent %s) landed on "
                "booking %s which resolved without a recorded payment — no refund anchor exists. "
                "Refund from the Stripe dashboard.",
                session.get("id"),
                session.get("payment_intent"),
                booking_id,
            )
            _send_orphan_payment_alert(
                session,
                reason=f"Booking {booking_id} resolved without ever recording a payment.",
            )


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
        # The session just expired on Stripe's side — no point asking Stripe to expire it again.
        orientations._delete_hold(booking, expire_session=False)
