"""The shared refund service — one lifecycle owner for every refundable source.

Per the cross-spec refund-engine contract, this module owns Stripe refund
issuing, ledger-row (:class:`billing.models.PaymentRefund`) lifecycle, receipt
emission, failure alerts, and retry — for class registrations now and
orientation bookings when the paid-orientations spec lands. Source models stay
thin: each implements the small :class:`RefundableSource` protocol and a
one-line ``issue_refund`` delegate.

Race guard (deliberate, documented tradeoff): ``issue_refund`` holds a
``select_for_update`` lock on the SOURCE row across one short Stripe call, and
stamps ``stripe_refund_id`` + final status before commit. The webhook handlers
lock the same source row first, so a ``charge.refunded`` for our own refund
serializes behind the issuing transaction and finds the already-stamped row —
no duplicate row, no duplicate receipt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import stripe
from django.db import transaction
from django.utils import timezone

from billing import stripe_utils
from billing.exceptions import (
    AlreadyRefundedError,
    InvalidRefundAmountError,
    RefundError,
    RefundNotPossibleError,
)
from billing.models import PaymentRefund

if TYPE_CHECKING:
    from django.contrib.auth.models import User


class RefundableSource(Protocol):
    """Structural contract a source model implements to be refundable — no base class.

    ``refund_receipt_context()`` returns the documented keys the service reads:
    ``item_title`` (what was paid for), ``recipient_email`` / ``recipient_name``
    (receipt addressing), ``payer_name`` (admin-facing display name),
    ``member`` (linked Member or ``None``), ``manage_url`` (absolute payer-facing
    manage link), and ``in_app_url`` (bell-row link path).
    """

    pk: int

    @property
    def refund_payment_intent_id(self) -> str: ...

    @property
    def refundable_cents(self) -> int: ...

    def refund_receipt_context(self) -> dict[str, Any]: ...

    def on_fully_refunded(self, reason: str, actor: User | None) -> None: ...


def source_field_name(source: Any) -> str:
    """The :class:`PaymentRefund` FK field name that points at ``source``.

    Raises:
        TypeError: If ``source`` is not a refundable source model instance.
    """
    from classes.models import Registration
    from membership.models import OrientationBooking

    if isinstance(source, Registration):
        return "registration"
    if isinstance(source, OrientationBooking):
        return "orientation_booking"
    raise TypeError(f"Not a refundable source: {type(source).__name__}")


def _locked(source: RefundableSource) -> RefundableSource:
    """Re-fetch ``source`` under ``select_for_update`` — call inside a transaction."""
    return type(source)._default_manager.select_for_update().get(pk=source.pk)  # type: ignore[attr-defined]


def _check_refundable(source: RefundableSource, amount_cents: int | None) -> None:
    """Guard a refund request. Refundability IS the guard — deliberately no status-list check.

    A registration whose covering refund later failed may sit at status REFUNDED
    with money to send back; a status guard would make it permanently
    unretryable. PENDING/WAITLISTED rows never took money, so they fail the
    refundable check naturally.
    """
    if not source.refund_payment_intent_id:
        raise RefundNotPossibleError("No Stripe payment on file.")
    refundable = source.refundable_cents
    if refundable <= 0:
        # Negative happens when a Stripe-dashboard over-refund was reconciled in;
        # either way there is no refundable remainder.
        raise AlreadyRefundedError("Nothing left to refund. The payment is already fully or over refunded.")
    if amount_cents is not None and not 0 < amount_cents <= refundable:
        raise InvalidRefundAmountError(f"Refund amount must be between 1 and {refundable} cents.")


def issue_refund(
    source: RefundableSource,
    *,
    amount_cents: int | None = None,
    reason: str = "",
    actor: User | None = None,
) -> PaymentRefund:
    """Issue a Stripe refund for ``source`` — full when ``amount_cents`` is ``None``.

    The one entry point the thin per-source ``issue_refund`` delegates call.
    Creates the ledger row, calls Stripe under the source-row lock, and stamps
    the outcome before commit. On success the succeeded-transition side effects
    fire (receipt email; full-refund bookkeeping via ``on_fully_refunded``).

    Raises:
        RefundNotPossibleError: No Stripe payment on file.
        AlreadyRefundedError: Nothing left to refund.
        InvalidRefundAmountError: Amount is zero, negative, or over the remainder.
        RefundError: Stripe rejected the refund — the FAILED row remains as the
            audit record and the Retry anchor.
    """
    error: RefundError | None = None
    with transaction.atomic():
        locked = _locked(source)
        _check_refundable(locked, amount_cents)
        refund = PaymentRefund.objects.create(
            **{source_field_name(locked): locked},
            amount_cents=amount_cents if amount_cents is not None else locked.refundable_cents,
            status=PaymentRefund.Status.PENDING,
            source=PaymentRefund.Source.IN_APP,
            reason=reason,
            initiated_by=actor,
        )
        error = _call_stripe(refund, locked)
    if error is not None:
        raise error
    return refund


def retry_refund(refund: PaymentRefund, *, actor: User | None = None) -> PaymentRefund:
    """Retry a FAILED refund — same ledger row, bumped attempt, fresh idempotency key.

    The fresh ``-a{attempt}`` key suffix is by design: Stripe must not replay the
    failed attempt's cached error. Same row, not a new one — the ledger shows one
    refund with N attempts, which is the truth.

    Raises:
        RefundError: The row is not FAILED, or Stripe rejected the retry.
    """
    error: RefundError | None = None
    with transaction.atomic():
        _locked(refund.source_object)
        refund.refresh_from_db()
        if refund.status != PaymentRefund.Status.FAILED:
            raise RefundError("Only a failed refund can be retried.")
        refund.attempt += 1
        refund.status = PaymentRefund.Status.PENDING
        refund.failure_reason = ""
        refund.settled_at = None
        if actor is not None:
            refund.initiated_by = actor
        refund.save(update_fields=["attempt", "status", "failure_reason", "settled_at", "initiated_by"])
        error = _call_stripe(refund, refund.source_object)
    if error is not None:
        raise error
    return refund


def reconcile_dashboard_refund(
    source: RefundableSource,
    *,
    stripe_refund_id: str,
    amount_cents: int,
    stripe_status: str,
) -> PaymentRefund:
    """Upsert one Stripe refund seen on a ``charge.refunded`` event — idempotent.

    Caller must hold the source-row lock (the webhook handler's transaction).
    A refund we issued in app is found by ``stripe_refund_id`` (stamped before
    the issuing transaction committed) — no duplicate row. An unknown id is a
    refund made by hand in the Stripe dashboard: a new row, ``initiated_by``
    ``None``. Any row transitioning into SUCCEEDED goes through the one
    succeeded-transition path; rows already SUCCEEDED are left untouched.
    """
    refund = PaymentRefund.objects.filter(stripe_refund_id=stripe_refund_id).first()
    if refund is None:
        refund = PaymentRefund.objects.create(
            **{source_field_name(source): source},
            stripe_refund_id=stripe_refund_id,
            amount_cents=amount_cents,
            status=PaymentRefund.Status.PENDING,
            source=PaymentRefund.Source.STRIPE_DASHBOARD,
            initiated_by=None,
        )
    if stripe_status == "succeeded":
        _mark_succeeded(refund)
    return refund


def apply_refund_update(refund: PaymentRefund, *, stripe_status: str, failure_reason: str = "") -> None:
    """Apply a ``refund.updated`` event to a known ledger row — source-neutral.

    ``failed`` flips the row to FAILED and alerts the Billing Administrators.
    If the source had been auto-marked fully refunded by this refund, the seat
    and waitlist are NOT silently unwound — the alert plus the panel's Retry
    action is the recovery path. ``succeeded`` on a PENDING row runs the
    succeeded-transition side effects; anything else is a no-op.
    """
    alert = False
    with transaction.atomic():
        _locked(refund.source_object)
        refund.refresh_from_db()
        if stripe_status == "failed" and refund.status != PaymentRefund.Status.FAILED:
            _mark_failed(refund, failure_reason)
            alert = True
        elif stripe_status == "succeeded" and refund.status == PaymentRefund.Status.PENDING:
            _mark_succeeded(refund)
    if alert:
        _emit_refund_failed_alert(refund)


def fail_pending_refund(refund: PaymentRefund, *, failure_reason: str) -> bool:
    """Flip a stuck PENDING row to FAILED with the full failure bookkeeping.

    The stale-refund sweep's entry point: routes through the same ``_mark_failed``
    path as every other failure (activity row included) and alerts the Billing
    Administrators, so a process-death refund is as loud as a Stripe one. Returns
    ``False`` (untouched) when the row is no longer PENDING under the lock.
    """
    with transaction.atomic():
        _locked(refund.source_object)
        refund.refresh_from_db()
        if refund.status != PaymentRefund.Status.PENDING:
            return False
        _mark_failed(refund, failure_reason)
    _emit_refund_failed_alert(refund)
    return True


def _call_stripe(refund: PaymentRefund, source: RefundableSource) -> RefundError | None:
    """Call Stripe for ``refund`` and stamp the outcome on the row.

    Returns the :class:`RefundError` to raise AFTER the enclosing transaction
    commits (raising inside it would roll back the FAILED audit row), or ``None``
    on success. The idempotency key is per-refund-row and per-attempt:
    ``pay-refund-{pk}-a{attempt}``.
    """
    try:
        result = stripe_utils.create_refund(
            payment_intent_id=source.refund_payment_intent_id,
            amount_cents=refund.amount_cents,
            idempotency_key=f"pay-refund-{refund.pk}-a{refund.attempt}",
        )
    except stripe.StripeError as exc:
        message = getattr(exc, "user_message", None) or str(exc)
        _mark_failed(refund, message)
        return RefundError(message)
    refund.stripe_refund_id = result["id"]
    refund.save(update_fields=["stripe_refund_id"])
    if result["status"] == "succeeded":
        _mark_succeeded(refund)
    elif result["status"] == "failed":
        message = "Stripe reported the refund as failed."
        _mark_failed(refund, message)
        return RefundError(message)
    # Any other status ("pending", "requires_action") leaves the row PENDING;
    # the refund.updated webhook owns its fate.
    return None


def _mark_succeeded(refund: PaymentRefund) -> None:
    """The succeeded transition — side effects fire on entering SUCCEEDED, exactly once.

    Wherever the transition comes from (service success path, webhook upsert, a
    webhook flipping an old PENDING row), the same effects run: stamp the row,
    email the payer the receipt for the ACTUAL refunded amount, and — when the
    source is now fully refunded — run its bookkeeping (``on_fully_refunded``).
    A row already SUCCEEDED fires nothing.

    The receipt EMAIL is deferred to ``transaction.on_commit`` so SMTP fan-out
    never runs while the caller holds the source-row lock, and a rollback can't
    have already sent mail. The DB bookkeeping (row stamp, ``on_fully_refunded``,
    activity) stays inside the transaction — it must commit atomically with the
    stamp, and a crash between commit and callback would otherwise lose the
    seat-free with no retry anchor.
    """
    if refund.status == PaymentRefund.Status.SUCCEEDED:
        return
    refund.status = PaymentRefund.Status.SUCCEEDED
    refund.settled_at = timezone.now()
    refund.save(update_fields=["status", "settled_at"])
    source = refund.source_object
    transaction.on_commit(lambda: _emit_refund_receipt(refund, source))
    if source.refundable_cents <= 0:
        # For a Retry that re-succeeds on an already-REFUNDED registration this
        # runs again but is harmless: no status transition, and waitlist
        # promotion is guarded by previously_held_a_spot.
        source.on_fully_refunded(refund.reason, refund.initiated_by)
    else:
        registration = refund.registration
        if registration is not None:
            from classes import activity
            from classes.models import CmsActivity

            activity.log(
                CmsActivity.Kind.REGISTRATION_PARTIAL_REFUND,
                class_offering=registration.class_offering,
                registration=registration,
                actor=refund.initiated_by,
                payload={"amount_cents": refund.amount_cents},
            )


def _mark_failed(refund: PaymentRefund, failure_reason: str) -> None:
    """Stamp the row FAILED — the audit record and the Retry anchor."""
    refund.status = PaymentRefund.Status.FAILED
    refund.failure_reason = failure_reason[:500]
    refund.settled_at = timezone.now()
    refund.save(update_fields=["status", "failure_reason", "settled_at"])
    registration = refund.registration
    if registration is not None:
        from classes import activity
        from classes.models import CmsActivity

        activity.log(
            CmsActivity.Kind.REGISTRATION_REFUND_FAILED,
            class_offering=registration.class_offering,
            registration=registration,
            actor=refund.initiated_by,
            payload={"failure_reason": refund.failure_reason},
        )


def _emit_refund_receipt(refund: PaymentRefund, source: RefundableSource) -> None:
    """Email the payer the refund receipt — unique period per refund row.

    ``period`` is ``pay-refund:{pk}`` so a second partial actually delivers
    (a per-source period would dedupe it away). The receipt always emails the
    address on the source row, so guest payers with no linked member are
    reached too.
    """
    from core.events.emit import emit

    ctx = source.refund_receipt_context()
    emit(
        "refund_issued",
        actor=refund.initiated_by,
        target=source,  # type: ignore[arg-type]
        context={
            "member": ctx["member"],
            "member_name": ctx["recipient_name"],
            "item_title": ctx["item_title"],
            "amount": f"${refund.amount_cents / 100:.2f}",
            "registration_url": ctx["manage_url"],
        },
        url=ctx["in_app_url"],
        email_to=ctx["recipient_email"],
        period=f"pay-refund:{refund.pk}",
    )


def _emit_refund_failed_alert(refund: PaymentRefund) -> None:
    """Alert the Billing Administrators that an async refund failure needs a retry.

    The payer already holds a receipt for money that never arrived (the receipt
    fires on the succeeded transition, and a late failure can follow it), so the
    copy tells the admin to contact them after retrying.
    """
    from core.events.emit import emit

    source = refund.source_object
    ctx = source.refund_receipt_context()
    if refund.registration_id is not None:
        from django.urls import reverse

        from core.urls_util import book_absolute_url

        admin_url = book_absolute_url(reverse("classes:admin_registration_detail", args=[refund.registration_id]))
    else:
        # Orientation bookings have no admin detail surface yet — the companion
        # paid-orientations spec supplies one; until then the manage URL stands in.
        admin_url = ctx["manage_url"]
    emit(
        "refund_failed",
        actor=None,
        target=source,  # type: ignore[arg-type]
        context={
            "payer_name": ctx["payer_name"],
            "item_title": ctx["item_title"],
            "amount": f"${refund.amount_cents / 100:.2f}",
            "failure_reason": refund.failure_reason or "Stripe did not give a reason.",
            "admin_url": admin_url,
        },
        url="/billing/admin/dashboard/",
        period=f"pay-refund:{refund.pk}:failed-a{refund.attempt}",
    )
