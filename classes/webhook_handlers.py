"""Stripe webhook handlers for the Classes app.

Registered into the billing app's webhook dispatcher. All handlers must be
idempotent — Stripe retries failed deliveries and may also fire the same
event more than once.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from classes.emails import (
    emit_instructor_new_registration,
    send_admin_registration_notification,
    send_class_welcome_email,
    send_registration_confirmation,
)
from classes.models import DiscountCode, Registration

logger = logging.getLogger(__name__)


def handle_checkout_session_completed(event: dict[str, Any]) -> None:
    """Confirm a class registration whose Stripe Checkout Session completed.

    We only act on sessions tagged ``kind=class_registration`` in their
    metadata so we don't collide with future Checkout uses (e.g. Tab top-ups).
    Idempotent — re-delivery on an already-confirmed registration is a no-op.
    """
    session = event["data"]["object"]
    metadata = session.get("metadata") or {}
    if metadata.get("kind") == "class_payment_link":
        _handle_class_payment_link(session)
        return
    if metadata.get("kind") != "class_registration":
        return

    registration_id = metadata.get("registration_id")
    if not registration_id:
        logger.warning("checkout.session.completed: missing registration_id in metadata")
        return

    if session.get("payment_status") != "paid":
        logger.info(
            "checkout.session.completed: ignoring session %s with payment_status=%s",
            session.get("id"),
            session.get("payment_status"),
        )
        return

    with transaction.atomic():
        try:
            registration = Registration.objects.select_for_update().get(pk=registration_id)
        except Registration.DoesNotExist:
            logger.warning("checkout.session.completed: no registration %s", registration_id)
            return

        if registration.status == Registration.Status.CONFIRMED:
            return  # already handled

        # Intentionally leave ``_acting_user`` unset: this is an automated Stripe
        # event with no human actor, so the audit feed correctly records "System".
        registration.status = Registration.Status.CONFIRMED
        registration.confirmed_at = timezone.now()
        registration.stripe_session_id = session.get("id", registration.stripe_session_id)
        registration.stripe_payment_id = session.get("payment_intent", "") or ""
        amount_total = session.get("amount_total")
        if isinstance(amount_total, int):
            registration.amount_paid_cents = amount_total
        registration.save(
            update_fields=[
                "status",
                "confirmed_at",
                "stripe_session_id",
                "stripe_payment_id",
                "amount_paid_cents",
            ]
        )
        if registration.discount_code_id:
            DiscountCode.objects.filter(pk=registration.discount_code_id).update(use_count=F("use_count") + 1)
            from classes import activity
            from classes.models import CmsActivity

            activity.log(
                CmsActivity.Kind.DISCOUNT_CODE_REDEEMED,
                class_offering=registration.class_offering,
                registration=registration,
                payload={"code": registration.discount_code.code},  # type: ignore[union-attr]  # discount_code_id guard ensures non-None
            )

    send_registration_confirmation(registration)
    send_class_welcome_email(registration)
    emit_instructor_new_registration(registration)
    send_admin_registration_notification(registration)
    from classes.services.mailchimp_subscribe import subscribe_registration

    # Subscribe BEFORE account creation: derive_tags decides `first-time-student`
    # by asking whether this email is already a known member, and
    # ensure_account_for_registration is what makes it one. The profile opt-in
    # stamp is mirrored afterwards, inside that call.
    subscribe_registration(registration)
    from core.services.guest_account import ensure_account_for_registration

    ensure_account_for_registration(registration)


def _handle_class_payment_link(session: dict[str, Any]) -> None:
    """Record a balance payment made through the promoted-registration pay page.

    The main handler early-returns on CONFIRMED rows, so balance payments get
    their own path. Ordered checks (idempotent — Stripe re-delivers):

    1. ``stripe_payment_id`` already equals this session's ``payment_intent`` →
       re-delivery of an already-recorded payment (including a duplicate one) — return.
    2. Row already settled (``balance_due_cents == 0``) — staff hit Mark as Paid
       (or an earlier link payment landed) while this Checkout was in flight. The
       studio has now collected twice: do NOT overwrite ``amount_paid_cents``
       (the ledger keeps the first settlement on purpose), send no receipt.
       Record ``stripe_payment_id`` (so check 1 makes re-deliveries idempotent),
       log a DUPLICATE_PAYMENT activity row, and alert the admins that a refund
       is owed.
    3. Normal path: record the payment (Checkout charged the full balance — no
       partials possible), bump the stored discount code's use count once, and
       send the standard confirmation as the paid-in-full receipt (its
       ``reg:{pk}:confirmation`` period has never fired for a promoted row, so it
       delivers exactly once across retries).
    """
    from classes import activity
    from classes.emails import send_duplicate_payment_alert
    from classes.models import CmsActivity

    registration_id = (session.get("metadata") or {}).get("registration_id")
    if not registration_id:
        logger.warning("checkout.session.completed (class_payment_link): missing registration_id in metadata")
        return
    if session.get("payment_status") != "paid":
        logger.info(
            "checkout.session.completed (class_payment_link): ignoring session %s with payment_status=%s",
            session.get("id"),
            session.get("payment_status"),
        )
        return

    is_duplicate = False
    duplicate_amount_cents = 0
    with transaction.atomic():
        try:
            registration = Registration.objects.select_for_update().get(pk=registration_id)
        except Registration.DoesNotExist:
            logger.warning("checkout.session.completed (class_payment_link): no registration %s", registration_id)
            return

        payment_intent = session.get("payment_intent", "") or ""
        if payment_intent and registration.stripe_payment_id == payment_intent:
            return  # Stripe re-delivery of an already-recorded payment

        amount_total = session.get("amount_total")
        if registration.balance_due_cents == 0:
            # Already settled — a duplicate payment. Record it distinctly, never overwrite.
            is_duplicate = True
            duplicate_amount_cents = amount_total if isinstance(amount_total, int) else 0
            registration.stripe_payment_id = payment_intent
            registration.save(update_fields=["stripe_payment_id"])
            activity.log(
                CmsActivity.Kind.DUPLICATE_PAYMENT,
                class_offering=registration.class_offering,
                registration=registration,
                payload={
                    "payment_intent": payment_intent,
                    "amount_cents": duplicate_amount_cents,
                    "session_id": session.get("id", ""),
                },
            )
        else:
            code_not_yet_counted = registration.amount_paid_cents == 0
            if isinstance(amount_total, int):
                registration.amount_paid_cents = amount_total
            registration.stripe_session_id = session.get("id", registration.stripe_session_id)
            registration.stripe_payment_id = payment_intent
            registration.save(update_fields=["amount_paid_cents", "stripe_session_id", "stripe_payment_id"])
            if registration.discount_code_id and code_not_yet_counted:
                DiscountCode.objects.filter(pk=registration.discount_code_id).update(use_count=F("use_count") + 1)
                activity.log(
                    CmsActivity.Kind.DISCOUNT_CODE_REDEEMED,
                    class_offering=registration.class_offering,
                    registration=registration,
                    payload={"code": registration.discount_code.code},  # type: ignore[union-attr]  # discount_code_id guard ensures non-None
                )

    if is_duplicate:
        send_duplicate_payment_alert(
            registration,
            amount_cents=duplicate_amount_cents,
            payment_intent=session.get("payment_intent", "") or "",
            session_id=session.get("id", ""),
        )
    else:
        send_registration_confirmation(registration)


def _refundable_source_for_payment_intent(payment_intent_id: str) -> Registration | None:
    """Resolve a Stripe PaymentIntent id to a refundable source row, across BOTH sources.

    Registrations are matched on ``stripe_payment_id``. Orientation bookings are
    the documented lookup seam: the paid-orientations companion spec adds their
    payment-intent field, and this resolver grows that second lookup then (the
    return type widens alongside it). ``None`` means the payment is not a
    refundable source we know — e.g. a Tab charge (reconciliation deferred) or
    an unknown payment.
    """
    return Registration.objects.filter(stripe_payment_id=payment_intent_id).first()


def handle_charge_refunded(event: dict[str, Any]) -> None:
    """Reconcile a ``charge.refunded`` event into the PaymentRefund ledger.

    Fires for refunds we issued in app AND for refunds made by hand in the
    Stripe dashboard. Idempotent: refunds upsert by ``stripe_refund_id``, and the
    source-row lock serializes this handler behind an in-flight ``issue_refund``
    (whose row is stamped before its transaction commits), so our own refund is
    never duplicated. A dashboard refund transitioning into SUCCEEDED flips the
    local record, frees the seat, and emails the payer exactly like an in-app one.
    """
    from billing import refunds as refunds_service

    charge = event["data"]["object"]
    payment_intent_id = charge.get("payment_intent") or ""
    source = _refundable_source_for_payment_intent(payment_intent_id) if payment_intent_id else None
    if source is None:
        logger.warning(
            "charge.refunded: payment intent %s is not a refundable source we know "
            "(a Tab charge or unknown payment) — skipping reconciliation.",
            payment_intent_id or "<missing>",
        )
        return

    refund_items = (charge.get("refunds") or {}).get("data") or []
    if not refund_items:
        # Since Stripe API 2022-11-15 the Charge payload no longer embeds its
        # refunds list by default (and we don't pin api_version), so fetch the
        # refunds explicitly. The embedded list still wins when present (older
        # pinned API versions).
        from billing import stripe_utils

        refund_items = stripe_utils.list_refunds_for_payment_intent(payment_intent_id=payment_intent_id)
    if not refund_items:
        if charge.get("amount_refunded"):
            logger.warning(
                "charge.refunded: charge on payment intent %s reports amount_refunded=%s "
                "but no refunds could be reconciled (embedded list absent and the fetch "
                "returned none) — the ledger did NOT record this refund.",
                payment_intent_id,
                charge.get("amount_refunded"),
            )
        return
    with transaction.atomic():
        locked = Registration.objects.select_for_update().get(pk=source.pk)
        for item in refund_items:
            refunds_service.reconcile_dashboard_refund(
                locked,
                stripe_refund_id=item["id"],
                amount_cents=item["amount"],
                stripe_status=item["status"],
            )


def handle_refund_updated(event: dict[str, Any]) -> None:
    """Apply a ``refund.updated`` event to the ledger — late failures and late successes.

    Lookup is by ``stripe_refund_id`` across the whole ledger (source-neutral —
    the row knows its side). A ``failed`` status flips the row and alerts the
    Billing Administrators; ``succeeded`` on a PENDING row runs the
    succeeded-transition side effects. Unknown ids log loudly and return.
    """
    from billing import refunds as refunds_service
    from billing.models import PaymentRefund

    refund_object = event["data"]["object"]
    stripe_refund_id = refund_object.get("id") or ""
    refund = PaymentRefund.objects.filter(stripe_refund_id=stripe_refund_id).first()
    if refund is None:
        logger.warning("refund.updated: unknown stripe refund id %s — skipping.", stripe_refund_id or "<missing>")
        return
    refunds_service.apply_refund_update(
        refund,
        stripe_status=refund_object.get("status") or "",
        failure_reason=refund_object.get("failure_reason") or "",
    )
