"""Stripe webhook event handlers. Each handler is idempotent."""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from billing.models import Tab, TabCharge
from billing.notifications import notify_admin_charge_failed, send_receipt

logger = logging.getLogger(__name__)


def handle_setup_intent_succeeded(event: dict[str, Any]) -> None:
    """Attach the payment method from a completed SetupIntent to the Tab."""
    setup_intent = event["data"]["object"]
    customer_id = setup_intent["customer"]
    payment_method_id = setup_intent["payment_method"]

    try:
        tab = Tab.objects.get(stripe_customer_id=customer_id)
    except Tab.DoesNotExist:
        logger.warning("setup_intent.succeeded: no tab for customer %s", customer_id)
        return

    # Idempotent: skip if already set to this payment method
    if tab.stripe_payment_method_id == payment_method_id:
        return

    from billing.stripe_utils import retrieve_payment_method

    pm = retrieve_payment_method(payment_method_id=payment_method_id)
    tab.stripe_payment_method_id = pm["id"]
    tab.payment_method_last4 = pm["last4"]
    tab.payment_method_brand = pm["brand"]
    tab.save(
        update_fields=[
            "stripe_payment_method_id",
            "payment_method_last4",
            "payment_method_brand",
            "updated_at",
        ]
    )


def handle_payment_intent_succeeded(event: dict[str, Any]) -> None:
    """Mark TabCharge as SUCCEEDED — this is the canonical success path."""
    payment_intent = event["data"]["object"]
    pi_id = payment_intent["id"]

    try:
        charge = TabCharge.objects.get(stripe_payment_intent_id=pi_id)
    except TabCharge.DoesNotExist:
        logger.warning("payment_intent.succeeded: no charge for PI %s", pi_id)
        return

    # Idempotent: skip if already succeeded
    if charge.status == TabCharge.Status.SUCCEEDED:
        return

    charge.status = TabCharge.Status.SUCCEEDED
    charge.charged_at = timezone.now()

    # Extract receipt URL from charges
    charges_data = payment_intent["charges"]["data"]
    if charges_data:
        charge.stripe_charge_id = charges_data[0]["id"]
        charge.stripe_receipt_url = charges_data[0]["receipt_url"] or ""

    charge.save()
    send_receipt(charge)


def handle_payment_intent_failed(event: dict[str, Any]) -> None:
    """Update TabCharge on payment failure."""
    payment_intent = event["data"]["object"]
    pi_id = payment_intent["id"]

    try:
        charge = TabCharge.objects.get(stripe_payment_intent_id=pi_id)
    except TabCharge.DoesNotExist:
        logger.warning("payment_intent.payment_failed: no charge for PI %s", pi_id)
        return

    # Idempotent: skip if already failed
    if charge.status == TabCharge.Status.FAILED:
        return

    last_error = payment_intent.get("last_payment_error") or {}
    charge.status = TabCharge.Status.FAILED
    charge.failure_reason = last_error.get("message", "Payment failed")
    charge.save(update_fields=["status", "failure_reason"])
    notify_admin_charge_failed(charge)


def handle_payment_method_detached(event: dict[str, Any]) -> None:
    """Clear payment method on Tab when card is detached in Stripe."""
    pm = event["data"]["object"]
    pm_id = pm["id"]

    try:
        tab = Tab.objects.get(stripe_payment_method_id=pm_id)
    except Tab.DoesNotExist:
        return  # Not our payment method

    tab.stripe_payment_method_id = ""
    tab.payment_method_last4 = ""
    tab.payment_method_brand = ""
    tab.save(
        update_fields=[
            "stripe_payment_method_id",
            "payment_method_last4",
            "payment_method_brand",
            "updated_at",
        ]
    )


def handle_payment_method_updated(event: dict[str, Any]) -> None:
    """Update card details on Tab when Stripe auto-updates a card."""
    pm = event["data"]["object"]
    pm_id = pm["id"]

    try:
        tab = Tab.objects.get(stripe_payment_method_id=pm_id)
    except Tab.DoesNotExist:
        return

    card = pm.get("card")
    if card:
        tab.payment_method_last4 = card["last4"]
        tab.payment_method_brand = card["brand"]
        tab.save(update_fields=["payment_method_last4", "payment_method_brand", "updated_at"])


def handle_charge_dispute_created(event: dict[str, Any]) -> None:
    """Log and notify admins about a chargeback."""
    dispute = event["data"]["object"]
    charge_id = dispute["charge"]
    logger.warning("Charge dispute created for charge %s. Amount: %s", charge_id, dispute["amount"])


# ── Late cancellation fee Checkouts (#456) ─────────────────────────────────────


def _flat_html(text: str) -> str:
    """Wrap a plain-text alert body in the branded email shell (the classes alert idiom)."""
    from django.utils.html import escape

    from core.events.templates import wrap_email_html

    blocks = [block for block in text.split("\n\n") if block.strip()]
    fragment = "".join("<p>" + escape(block).replace("\n", "<br>") + "</p>" for block in blocks)
    return wrap_email_html(fragment)


def _send_orphan_fee_alert(session: dict[str, Any], *, reason: str) -> None:
    """Email the Billing Administrators about a paid fee session with no fee to mark paid.

    Mirrors the orientation orphan alert: money moved with no in-app home is never
    allowed to be silent. The refund has to happen from the Stripe dashboard, so the
    alert links the payment directly.
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
        f"A paid late cancellation fee Checkout landed with no fee to mark paid.\n\n"
        f"{reason}\n\n"
        f"The member paid {amount} and the app has nothing to show for it. "
        f"Refund the payment from the Stripe dashboard.\n\n"
        f"Stripe payment: {stripe_url}\n"
        f"Checkout session: {session.get('id', '')}\n"
        f"Customer email: {session.get('customer_email') or session.get('customer_details', {}).get('email', '')}"
    )
    core_email.send(
        to=recipients,
        subject="Orphaned late cancellation fee payment needs a manual refund",
        trigger_kind="billing.late_fee_orphan_payment",
        text_body=body,
        html_body=_flat_html(body),
        best_effort=True,
    )


def handle_late_fee_checkout_completed(event: dict[str, Any]) -> None:
    """Mark a late cancellation fee paid via the one shared :func:`billing.late_fees.mark_paid`.

    Only acts on sessions tagged ``kind=late_cancel_fee``. Idempotent and race-safe:
    ``mark_paid`` re-checks the row under ``select_for_update``, so a re-delivery or a
    landing page racing the webhook pays at most once, and the receipt goes out only for
    money in hand. A paid session whose fee is missing, or whose fee left UNPAID some
    other way before the money landed, is money with no in-app home: logged at ERROR and
    alerted to the Billing Administrators for a Stripe dashboard refund.
    """
    from billing import late_fees
    from billing.models import LateCancellationFee

    session = event["data"]["object"]
    metadata = session.get("metadata") or {}
    if metadata.get("kind") != late_fees.CHECKOUT_KIND:
        return
    fee_id = metadata.get("fee_id")
    if not fee_id:
        logger.warning("checkout.session.completed: missing fee_id in late fee metadata")
        return
    if session.get("payment_status") != "paid":
        logger.info(
            "checkout.session.completed: ignoring late fee session %s with payment_status=%s",
            session.get("id"),
            session.get("payment_status"),
        )
        return
    fee = LateCancellationFee.objects.select_related("member").filter(pk=fee_id).first()
    if fee is None:
        logger.error(
            "checkout.session.completed: PAID late fee session %s (payment intent %s) has no fee %s. "
            "Money taken with no in-app trace. Refund from the Stripe dashboard.",
            session.get("id"),
            session.get("payment_intent"),
            fee_id,
        )
        _send_orphan_fee_alert(session, reason=f"Late cancellation fee {fee_id} no longer exists.")
        return
    amount_total = session.get("amount_total")
    outcome = late_fees.mark_paid(
        fee,
        payment_intent=session.get("payment_intent", "") or "",
        session_id=session.get("id", "") or "",
        amount_total=amount_total if isinstance(amount_total, int) else None,
    )
    if outcome == "already":
        fresh = LateCancellationFee.objects.filter(pk=fee.pk).first()
        if fresh is not None and fresh.status == LateCancellationFee.Status.PAID:
            return  # a re-delivery of a payment already recorded: nothing to do
        logger.error(
            "checkout.session.completed: PAID late fee session %s (payment intent %s) landed on fee %s "
            "which is %s, not paid. Refund from the Stripe dashboard.",
            session.get("id"),
            session.get("payment_intent"),
            fee_id,
            fresh.status if fresh is not None else "gone",
        )
        _send_orphan_fee_alert(
            session, reason=f"Late cancellation fee {fee_id} was resolved before the payment landed."
        )


def handle_late_fee_checkout_expired(event: dict[str, Any]) -> None:
    """An expired fee Checkout changes nothing: the fee stays UNPAID and Pay mints a fresh session."""
    from billing import late_fees

    session = event["data"]["object"]
    metadata = session.get("metadata") or {}
    if metadata.get("kind") != late_fees.CHECKOUT_KIND:
        return
    logger.info(
        "checkout.session.expired: late fee session %s for fee %s expired; the fee stays unpaid.",
        session.get("id"),
        metadata.get("fee_id"),
    )
