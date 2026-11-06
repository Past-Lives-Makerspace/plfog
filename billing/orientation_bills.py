"""A bill for an orientation that already happened: send, Stripe Checkout, paid (#1111).

An orientor records a past orientation from the Bookings tab and ticks Send a bill. The
member is oriented at once, on a completed :class:`~membership.models.OrientationBooking`
on a one-off CUSTOM slot on that day, and gets one email naming the orientation, the day,
who ran it and the amount, with a link to the bill's own page. That page's Pay button mints
a Stripe Checkout tagged ``kind=orientation_bill``; the webhook, the return page and any
later recovery all funnel through :func:`mark_paid`, which stamps ``amount_paid_cents``,
``stripe_payment_id`` and ``paid_at``. From then on the bill is a paid booking to every
finance reader, dated by when it was paid.

The shape mirrors :mod:`billing.late_fees` by name. A billed orientation never goes near
``request_orientation``, ``confirm_orientation`` or ``complete_orientation``, which email,
ping orientors and post to Discord; and its Checkout kind is its own, so the booking webhook
never reads a bill payment as an orphan. The booking model does the state transitions.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.models import SiteActivity

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.db.models import QuerySet

    from membership.models import Member, OrientationBooking, OrientationType

logger = logging.getLogger(__name__)

#: The ``metadata["kind"]`` every bill Checkout carries; the webhook handler self-filters on it.
CHECKOUT_KIND = "orientation_bill"
_CHECKOUT_SALT = "orientation-bill-checkout"
_CHECKOUT_MAX_AGE = 30 * 24 * 3600  # 30 days, like the late fee token: outlives any session by a wide margin
#: Where the billed slot sits on its day: midday, or earlier when that has not come yet.
_BILLED_HOUR = time(12)


def _absolute_url(path: str) -> str:
    """Turn a relative hub path into an absolute URL using the member-site base."""
    base = settings.MEMBER_BASE_URL.rstrip("/")
    return f"{base}{path}"


def make_checkout_token(booking: OrientationBooking) -> str:
    """Sign a token authorising the Checkout return and cancelled pages for one bill."""
    return signing.dumps({"bill": booking.pk}, salt=_CHECKOUT_SALT)


def read_checkout_token(token: str) -> OrientationBooking:
    """Decode a checkout token to its billed booking.

    Raises:
        signing.BadSignature: If the token is invalid or expired.
        OrientationBooking.DoesNotExist: If the booking no longer exists or is not a bill.
    """
    data = signing.loads(token, salt=_CHECKOUT_SALT, max_age=_CHECKOUT_MAX_AGE)
    return bills().get(pk=data["bill"])


def bills() -> QuerySet[OrientationBooking]:
    """Every billed booking with what its page reads loaded."""
    from membership.models import OrientationBooking

    return OrientationBooking.objects.filter(billed_at__isnull=False).select_related(
        "member__user", "oriented_by", "slot", "orientation_type__guild", "orientation_type__equipment"
    )


def bill_url(booking: OrientationBooking) -> str:
    """The absolute URL of the bill's own page (what it is for and its Pay button); what the email links."""
    return _absolute_url(reverse("hub_orientation_bill", args=[booking.pk]))


def item_label(booking: OrientationBooking) -> str:
    """What the bill is for, as Stripe and the pages name it: "Lathe orientation, Wood Shop"."""
    orientation_type = booking.orientation_type
    return f"{orientation_type.name} orientation, {orientation_type.owner_name}"


def oriented_by_name(booking: OrientationBooking) -> str:
    """Who ran the orientation, by name, else the owner it belongs to."""
    if booking.oriented_by is not None:
        return booking.oriented_by.display_name
    return booking.orientation_type.owner_name


def bill_refusal(member: Member, orientation_type: OrientationType) -> str:
    """Why ``member`` cannot be billed for ``orientation_type``, or "" when they can.

    A member with no login account cannot open the bill's page to pay it. One with a live
    booking for the type already holds the one live row the type allows
    (``uq_orientationbooking_active_per_type``).
    """
    from membership.models import OrientationBooking

    if member.user_id is None:
        return f"{member.display_name} has no account yet, so they can't pay online."
    if OrientationBooking.objects.seat_holding().filter(member=member, orientation_type=orientation_type).exists():
        return f"{member.display_name} already has a booking for this orientation."
    return ""


def _billed_starts_at(orientation_type: OrientationType, day: date) -> datetime:
    """When the billed slot starts: midday on ``day``, or early enough that it has already ended.

    The slot should lie in the past, so it never shows as upcoming on a calendar or a list, but
    it never starts before ``day`` itself: just after midnight today it starts at midnight and
    may still be running, which beats filing the orientation under yesterday.
    """
    start_of_day = timezone.make_aware(datetime.combine(day, time.min))
    midday = timezone.make_aware(datetime.combine(day, _BILLED_HOUR))
    latest = timezone.now() - timedelta(minutes=orientation_type.duration_minutes)
    return max(start_of_day, min(midday, latest))


def send_bill(
    member: Member,
    orientation_type: OrientationType,
    *,
    completed_on: date,
    oriented_by: Member | None,
    amount_cents: int,
    sent_by: User,
    note: str = "",
) -> OrientationBooking:
    """Record a past orientation as completed and send the member one bill for it.

    In one transaction: the one-off CUSTOM slot on ``completed_on`` (one seat, run by
    ``oriented_by``), the confirmed and completed booking asking ``amount_cents``
    (:meth:`OrientationBooking.create_bill`), and one ``ORIENTATION_BILL_SENT`` activity row
    with ``sent_by`` as the actor and ``note`` in its payload. After it commits, the bill
    email. Nothing else is emailed, pinged or posted.

    Raises:
        OrientationError: When :func:`bill_refusal` refuses the member, or the amount is not billable.
        IntegrityError: When a booking for the type landed meanwhile (a double click).
    """
    from membership.models import OrientationBooking, OrientationError
    from membership.orientations import _create_custom_slot

    refusal = bill_refusal(member, orientation_type)
    if refusal:
        raise OrientationError(refusal)
    OrientationBooking.ensure_bill_amount(amount_cents)
    with transaction.atomic():
        slot = _create_custom_slot(
            orientation_type, _billed_starts_at(orientation_type, completed_on), orienter=oriented_by
        )
        booking = OrientationBooking.create_bill(
            slot=slot, member=member, oriented_by=oriented_by, amount_cents=amount_cents
        )
        SiteActivity.log(
            SiteActivity.Kind.ORIENTATION_BILL_SENT,
            actor=sent_by,
            target=booking,
            payload={
                "orientation_type": orientation_type.name,
                "owner": orientation_type.owner_name,
                "amount_cents": amount_cents,
                "note": note,
            },
        )
    _send_bill_email(booking)
    return booking


def _send_bill_email(booking: OrientationBooking) -> None:
    """The member's bill: the ``billing.orientation_bill`` event, its email forced."""
    from core.events.emit import emit

    member = booking.member
    day = timezone.localtime(booking.slot.starts_at)
    emit(
        "billing.orientation_bill",
        target=booking,
        context={
            "user": member.user,
            "member_name": member.display_name,
            "orientation_name": booking.orientation_type.name,
            "owner_name": booking.orientation_type.owner_name,
            "oriented_on": f"{day:%A, %B} {day.day}",
            "oriented_by_name": oriented_by_name(booking),
            "bill_amount": booking.bill_amount_display,
            "bill_url": bill_url(booking),
        },
        url=reverse("hub_orientation_bill", args=[booking.pk]),
        period=f"orientation_bill:{booking.pk}:sent",
    )


def _expire_session_best_effort(booking: OrientationBooking) -> None:
    """Expire the bill's previous Checkout Session so two live pages can never both pay one bill.

    Best effort by design: the session may already be expired or Stripe may be down, and the
    fresh mint goes ahead either way (the webhook's double payment alert is the backstop).
    """
    from billing import stripe_utils

    if not booking.stripe_session_id:
        return
    try:
        stripe_utils.expire_checkout_session(session_id=booking.stripe_session_id)
    except Exception:
        logger.info("Could not expire the previous Checkout session for orientation bill %s (best effort).", booking.pk)


def start_bill_checkout(booking: OrientationBooking) -> str:
    """Mint a fresh Stripe Checkout Session for an unpaid bill and return its hosted URL.

    Every call mints a new session, after expiring the previous one best effort, like
    :func:`billing.late_fees.start_fee_checkout`: the attempt counter moves before the
    Stripe call and is part of the idempotency key, so a Pay click after an expired or
    failed session always gets a live page. Nothing is held, so Stripe's default lifetime stands.

    Raises:
        OrientationError: If the bill is paid or cancelled; there is nothing to pay.
    """
    from billing import stripe_utils

    booking.ensure_bill_payable()
    _expire_session_best_effort(booking)
    attempt = booking.next_bill_checkout_attempt()
    token = make_checkout_token(booking)
    session = stripe_utils.create_checkout_session(
        amount_cents=booking.bill_amount_cents,
        product_name=item_label(booking),
        customer_email=booking.member.primary_email,
        success_url=_absolute_url(reverse("hub_orientation_bill_return", args=[token])),
        cancel_url=_absolute_url(reverse("hub_orientation_bill_checkout_cancelled", args=[token])),
        metadata={"kind": CHECKOUT_KIND, "booking_id": str(booking.pk)},
        idempotency_key=f"orientation-bill-{booking.pk}-{attempt}",
    )
    booking.stripe_session_id = session["id"]
    booking.save(update_fields=["stripe_session_id"])
    return session["url"]


def mark_paid(booking: OrientationBooking, *, payment_intent: str, session_id: str, amount_total: int | None) -> str:
    """Stamp an unpaid bill paid once: THE single "money is in hand" transition for a bill.

    The webhook and the return page's reconcile both funnel through here. Race safe: the row
    is re-read under ``select_for_update`` and only a still unpaid bill is stamped, so a
    webhook racing the landing page pays it once. A mismatch between what Stripe collected
    and the bill is logged, never absorbed silently.

    Returns:
        ``"paid"`` when this call stamped it; ``"already"`` when it was paid or cancelled
        before; ``"gone"`` when the row no longer exists.
    """
    from membership.orientations import locked_booking_queryset

    with transaction.atomic():
        locked = locked_booking_queryset().filter(pk=booking.pk).first()
        if locked is None:
            return "gone"
        if not locked.bill_is_unpaid:
            return "already"
        if amount_total is not None and amount_total != locked.bill_amount_cents:
            logger.warning(
                "Orientation bill %s: Stripe collected %s cents against a %s cent bill.",
                locked.pk,
                amount_total,
                locked.bill_amount_cents,
            )
        locked.record_bill_payment(payment_intent=payment_intent, session_id=session_id, amount_total=amount_total)
    return "paid"


def reconcile_landed_checkout(booking: OrientationBooking) -> str:
    """Ask Stripe and mark paid when the member lands on the success page.

    The webhook is the primary path, but it can lag, and where no endpoint is set up it never
    arrives; the landing asks Stripe directly and funnels a paid session through
    :func:`mark_paid`, so "Paid" renders at once.

    Returns:
        :func:`mark_paid`'s outcome when Stripe says paid; ``"pending"`` when the session is
        not paid yet or there is no session to check; ``"unknown"`` when Stripe is unreachable.
    """
    from billing import stripe_utils

    if not booking.stripe_session_id:
        return "pending"
    try:
        session = stripe_utils.retrieve_checkout_session(session_id=booking.stripe_session_id)
    except Exception:
        logger.exception("Landing reconcile: could not verify session for orientation bill %s.", booking.pk)
        return "unknown"
    if session["payment_status"] != "paid":
        return "pending"
    return mark_paid(
        booking,
        payment_intent=session["payment_intent"],
        session_id=session["id"],
        amount_total=session["amount_total"],
    )
