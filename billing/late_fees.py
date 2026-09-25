"""The late cancellation fee after the cancel: charge, Stripe Checkout, paid, block (#456, part 2).

Part 1 (:mod:`membership.late_cancel`) decides whether a cancel is late and what it costs.
This module owns what happens next: :func:`charge_if_late` writes the
:class:`~billing.models.LateCancellationFee` inside the cancel's own transaction,
:func:`start_fee_checkout` mints the hosted Stripe Checkout the member pays on,
:func:`mark_paid` is the single "money is in hand" transition (the webhook, the return page
and the Pay button all funnel through it, race-safe), and :func:`unpaid_fee_for` is the one
input to the block until paid. Stripe is reached only through :mod:`billing.stripe_utils`.

The shape mirrors the orientation checkout in :mod:`membership.orientations`: a signed
token authorises the return and cancelled landings, ``metadata["kind"]`` tags the session
so the billing fan-in reaches the right handler, and every emit carries a unique ``period``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString

from billing.models import LateCancellationFee
from core.models import SiteActivity

if TYPE_CHECKING:
    from membership.models import EquipmentReservation, Member, OrientationBooking

logger = logging.getLogger(__name__)

#: The ``metadata["kind"]`` every fee Checkout carries; the webhook handlers self-filter on it.
CHECKOUT_KIND = "late_cancel_fee"
_CHECKOUT_SALT = "late-fee-checkout"
_CHECKOUT_MAX_AGE = 30 * 24 * 3600  # 30 days, like the orientation token: outlives any session by a wide margin


def _absolute_url(path: str) -> str:
    """Turn a relative hub path into an absolute URL using the member-site base."""
    base = settings.MEMBER_BASE_URL.rstrip("/")
    return f"{base}{path}"


def make_checkout_token(fee: LateCancellationFee) -> str:
    """Sign a token authorising the Checkout return and cancelled pages for one fee."""
    return signing.dumps({"fee": fee.pk}, salt=_CHECKOUT_SALT)


def read_checkout_token(token: str) -> LateCancellationFee:
    """Decode a checkout token to its fee.

    Raises:
        signing.BadSignature: If the token is invalid or expired.
        LateCancellationFee.DoesNotExist: If the fee no longer exists.
    """
    data = signing.loads(token, salt=_CHECKOUT_SALT, max_age=_CHECKOUT_MAX_AGE)
    return LateCancellationFee.objects.select_related("member").get(pk=data["fee"])


def pay_url(fee: LateCancellationFee) -> str:
    """The absolute URL of the fee's own page (the fee and its Pay button); what emails link."""
    return _absolute_url(reverse("hub_late_fee_detail", args=[fee.pk]))


def pay_line(fee: LateCancellationFee) -> str:
    """The cancellation email's fee sentence, carrying the Pay link.

    One sentence for both email bodies, so the copy-mode placeholder can be "" when no fee
    applies. The URL is followed by a space, never punctuation, so mail clients linkify it
    cleanly.
    """
    return (
        f"A {fee.amount_display} late cancellation fee applies to this cancellation. "
        f"Pay it at {pay_url(fee)} and you'll get a receipt once it's paid."
    )


def pay_html(fee: LateCancellationFee) -> SafeString:
    """The HTML twin of :func:`pay_line`: the same sentence with a real link, for an HTML email body.

    Built with ``format_html`` so the amount and the URL are escaped and the result is a
    ``SafeString`` the copy renderer inserts unescaped.
    """
    return format_html(
        "A {} late cancellation fee applies to this cancellation. "
        "<a href=\"{}\">Pay the late fee</a> and you'll get a receipt once it's paid.",
        fee.amount_display,
        pay_url(fee),
    )


def _starts_at(target: OrientationBooking | EquipmentReservation) -> datetime:
    from membership.models import EquipmentReservation

    if isinstance(target, EquipmentReservation):
        return target.starts_at
    return target.slot.starts_at


def charge_if_late(
    target: OrientationBooking | EquipmentReservation, *, now: datetime | None = None
) -> LateCancellationFee | None:
    """Create the fee for a late self cancel of ``target``, or return the one it already has.

    Reads :func:`membership.late_cancel.policy_for`; returns ``None`` unless a fee applies
    (the site switch on and the owner charging one) and the cancel is inside the window
    at ``now``. Otherwise ``get_or_create`` on the OneToOne: a second call returns the
    existing row, never a second fee, and the OneToOne makes the database the last word
    under a real race. Meant to run inside the cancel's own ``transaction.atomic()`` so a
    cancel that fails to commit charges nothing.

    Logs ``LATE_FEE_CHARGED`` once, when the row is created.
    """
    from membership.late_cancel import policy_for
    from membership.models import EquipmentReservation

    policy = policy_for(target)
    if not policy.applies or not policy.is_late(_starts_at(target), now=now):
        return None
    lookup: dict[str, Any] = (
        {"reservation": target} if isinstance(target, EquipmentReservation) else {"orientation_booking": target}
    )
    fee, created = LateCancellationFee.objects.get_or_create(
        **lookup, defaults={"member": target.member, "amount_cents": policy.fee_cents}
    )
    if created:
        SiteActivity.log(
            SiteActivity.Kind.LATE_FEE_CHARGED,
            actor=target.member.user,
            target=fee,
            payload={"amount_cents": fee.amount_cents, "item": fee.item_label},
        )
    return fee


def _expire_session_best_effort(fee: LateCancellationFee) -> None:
    """Expire the fee's previous Checkout Session so two live pages can never both pay one fee.

    Best-effort by design: the session may already be expired or Stripe may be down, and
    either way the fresh mint proceeds (the webhook's double-payment alert is the backstop).
    """
    from billing import stripe_utils

    if not fee.stripe_session_id:
        return
    try:
        stripe_utils.expire_checkout_session(session_id=fee.stripe_session_id)
    except Exception:
        logger.info(
            "Could not expire the previous Checkout session for late cancellation fee %s (best effort).", fee.pk
        )


def start_fee_checkout(fee: LateCancellationFee) -> str:
    """Mint a fresh Stripe Checkout Session for an UNPAID fee and return its hosted URL.

    Every call mints a new session, after expiring the previous one best-effort so a Pay
    click never leaves two live pages for one fee. The attempt counter moves BEFORE the
    Stripe call and is part of the idempotency key, so a Pay click after an expired
    session, or after a failed mint (Stripe replays the first answer for a key, failures
    included), always gets a live page. Nothing is held while the session lives, so
    Stripe's default 24 hour lifetime, the longest it allows, is left in place.

    Raises:
        ValueError: If the fee is not UNPAID; there is nothing to pay.
    """
    from billing import stripe_utils

    if fee.status != LateCancellationFee.Status.UNPAID:
        raise ValueError(f"Late cancellation fee {fee.pk} is {fee.status}, not unpaid; nothing to pay.")
    _expire_session_best_effort(fee)
    fee.checkout_attempts += 1
    fee.save(update_fields=["checkout_attempts"])
    token = make_checkout_token(fee)
    session = stripe_utils.create_checkout_session(
        amount_cents=fee.amount_cents,
        product_name=f"Late cancellation fee: {fee.item_label}",
        customer_email=fee.member.primary_email,
        success_url=_absolute_url(reverse("hub_late_fee_return", args=[token])),
        cancel_url=_absolute_url(reverse("hub_late_fee_checkout_cancelled", args=[token])),
        metadata={"kind": CHECKOUT_KIND, "fee_id": str(fee.pk)},
        idempotency_key=f"late-fee-{fee.pk}-{fee.checkout_attempts}",
    )
    fee.stripe_session_id = session["id"]
    fee.save(update_fields=["stripe_session_id"])
    return session["url"]


def mark_paid(fee: LateCancellationFee, *, payment_intent: str, session_id: str, amount_total: int | None) -> str:
    """Flip an UNPAID fee to PAID once, stamp the Stripe ids, log it and send the receipt.

    THE single "money is in hand" transition: the webhook handler, the return page's
    synchronous reconcile and any later recovery all funnel through here. Race-safe: the
    row is re-fetched under ``select_for_update`` and only a still-UNPAID fee flips, so a
    webhook racing the landing page pays exactly once (one activity row, one receipt).
    ``session_id`` backfills a fee whose session id never got saved. ``amount_total`` is
    what Stripe collected; a mismatch against the fee is logged, never silently absorbed.

    Returns:
        ``"paid"`` when this call flipped the fee; ``"already"`` when it was no longer
        UNPAID (a re-delivery, or resolved another way); ``"gone"`` when the row no
        longer exists. The receipt goes out only on ``"paid"``, outside the lock.
    """
    with transaction.atomic():
        locked = LateCancellationFee.objects.select_for_update().filter(pk=fee.pk).first()
        if locked is None:
            return "gone"
        if locked.status != LateCancellationFee.Status.UNPAID:
            return "already"
        if amount_total is not None and amount_total != locked.amount_cents:
            logger.warning(
                "Late cancellation fee %s: Stripe collected %s cents against a %s cent fee.",
                locked.pk,
                amount_total,
                locked.amount_cents,
            )
        paid_at = timezone.now()
        locked.status = LateCancellationFee.Status.PAID
        locked.stripe_payment_id = payment_intent
        if session_id and not locked.stripe_session_id:
            locked.stripe_session_id = session_id
        locked.paid_at = paid_at
        locked.save(update_fields=["status", "stripe_payment_id", "stripe_session_id", "paid_at"])
        SiteActivity.log(
            SiteActivity.Kind.LATE_FEE_PAID,
            actor=locked.member.user,
            target=locked,
            payload={"amount_cents": locked.amount_cents, "item": locked.item_label},
        )
    _send_receipt(locked, paid_at=paid_at)
    return "paid"


def _send_receipt(fee: LateCancellationFee, *, paid_at: datetime) -> None:
    """The member's receipt: the ``billing.late_fee_paid`` event, once per fee."""
    from core.events.emit import emit

    member = fee.member
    local = timezone.localtime(paid_at)
    emit(
        "billing.late_fee_paid",
        actor=member.user,
        target=fee,
        context={
            "user": member.user,
            "member_name": member.display_name,
            "fee_amount": fee.amount_display,
            "fee_item": fee.item_label,
            "paid_on": f"{local:%A, %B} {local.day}",
            "fee_url": pay_url(fee),
        },
        url=reverse("hub_late_fee_detail", args=[fee.pk]),
        period=f"late_fee:{fee.pk}:paid",
    )


def reconcile_landed_checkout(fee: LateCancellationFee) -> str:
    """Verify with Stripe and mark paid when the member lands on the success page.

    The webhook is the primary path, but it can lag, and on an environment with no
    webhook endpoint it never arrives; the landing asks Stripe directly and funnels a
    paid session through :func:`mark_paid`, so "Paid. Thank you." renders at once.

    Returns:
        :func:`mark_paid`'s outcome when Stripe says paid; ``"pending"`` when the session
        is not paid yet, or there is no session id to check; ``"unknown"`` when Stripe is
        unreachable (the member refreshes in a moment).
    """
    from billing import stripe_utils

    if not fee.stripe_session_id:
        return "pending"
    try:
        session = stripe_utils.retrieve_checkout_session(session_id=fee.stripe_session_id)
    except Exception:
        logger.exception("Landing reconcile: could not verify session for late cancellation fee %s.", fee.pk)
        return "unknown"
    if session["payment_status"] != "paid":
        return "pending"
    return mark_paid(
        fee, payment_intent=session["payment_intent"], session_id=session["id"], amount_total=session["amount_total"]
    )


def unpaid_fee_for(member: Member) -> LateCancellationFee | None:
    """The member's oldest UNPAID fee, or ``None``: the one input to the block until paid."""
    return (
        LateCancellationFee.objects.filter(member=member, status=LateCancellationFee.Status.UNPAID)
        .order_by("created_at", "pk")
        .first()
    )
