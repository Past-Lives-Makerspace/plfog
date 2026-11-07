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
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, cast

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
#: Staff copy when Cancel Bill finds Stripe already took the money.
PAID_BEFORE_CANCEL = "They already paid this bill. Refund it from Payments if needed."
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


def _day_text(moment: datetime) -> str:
    """A moment's local day as the bill emails write it: "Tuesday, November 3"."""
    day = timezone.localtime(moment)
    return f"{day:%A, %B} {day.day}"


def _bill_context(booking: OrientationBooking) -> dict[str, Any]:
    """What the bill email and its reminder both say: the member, the orientation, the amount, the Pay link."""
    member = booking.member
    return {
        "user": member.user,
        "member_name": member.display_name,
        "orientation_name": booking.orientation_type.name,
        "owner_name": booking.orientation_type.owner_name,
        "oriented_on": _day_text(booking.slot.starts_at),
        "oriented_by_name": oriented_by_name(booking),
        "bill_amount": booking.bill_amount_display,
        "bill_url": bill_url(booking),
    }


def _send_bill_email(booking: OrientationBooking, *, period: str = "") -> None:
    """The member's bill: the ``billing.orientation_bill`` event, its email forced.

    ``period`` is the emit's idempotency bucket: the first send's own by default, so a retried
    send never mails twice; a resend passes a fresh one so it is not taken for that send.
    """
    from core.events.emit import emit

    emit(
        "billing.orientation_bill",
        target=booking,
        context=_bill_context(booking),
        url=reverse("hub_orientation_bill", args=[booking.pk]),
        period=period or f"orientation_bill:{booking.pk}:sent",
    )


def resend_bill(booking: OrientationBooking) -> None:
    """Send the same bill email again (#1111 part 2), while the bill is still owed.

    No activity row: nothing about the bill changes, and the email log records the send.

    Raises:
        OrientationError: When the bill is paid or cancelled.
    """
    booking.ensure_bill_payable(for_staff=True)
    # Bucketed to the minute, so a double submitted Resend sends one email.
    minute = timezone.now().strftime("%Y%m%dT%H%M")
    _send_bill_email(booking, period=f"orientation_bill:{booking.pk}:resent:{minute}")


# --- Reminders for an unpaid bill (#1120) ------------------------------------------------


def _bookings_tab_path() -> str:
    """The Orientations page's Bookings tab, Past list: where a bill's row offers Resend and Cancel."""
    return f"{reverse('hub_orientations')}?view=bookings&show=past"


def _send_reminder_email(booking: OrientationBooking, step: int) -> bool:
    """The member's reminder: the bill email with reminder wording, once per bill per step.

    Returns:
        Whether the member's email went out: delivered now, or already delivered under this
        step's period (the ledger skipped it as a duplicate). A send the email channel refused
        released its slot and reads False.
    """
    from core.events.emit import emit
    from core.events.registry import Channel

    result = emit(
        "billing.orientation_bill_reminder",
        target=booking,
        context={**_bill_context(booking), "billed_on": _day_text(cast("datetime", booking.billed_at))},
        url=reverse("hub_orientation_bill", args=[booking.pk]),
        period=f"orientation_bill:{booking.pk}:reminder:{step}",
    )
    member_email = (booking.member.user_id, Channel.EMAIL)
    return member_email in result.delivered or member_email in result.skipped_duplicates


def _tell_orientor_unpaid(booking: OrientationBooking, *, reached: bool) -> None:
    """Tell the bill's Oriented by, in the bell and by email, that it is still unpaid at the last step.

    The ``orientation_runner`` resolver finds nobody when the bill has no Oriented by or their
    member has no account, so the notice is skipped silently then. ``reached`` picks the
    sentence saying whether the member's reminder went out (the renderer has no conditionals,
    so the line arrives pre-built, like ``event.reminder``'s ``join_line``).
    """
    from core.events.emit import emit
    from membership.models import OrientationBooking

    orientor = booking.oriented_by
    emit(
        "billing.orientation_bill_unpaid",
        target=booking,
        context={
            "booking": booking,
            "orientor_name": orientor.display_name if orientor is not None else "",
            "member_name": booking.member.display_name,
            "orientation_name": booking.orientation_type.name,
            "owner_name": booking.orientation_type.owner_name,
            "bill_amount": booking.bill_amount_display,
            "billed_on": _day_text(cast("datetime", booking.billed_at)),
            "unpaid_days": str(OrientationBooking.BILL_REMINDER_DAYS[-1]),
            "reached_line": REACHED_LINE if reached else NOT_REACHED_LINE,
            "bookings_url": _absolute_url(_bookings_tab_path()),
        },
        url=_bookings_tab_path(),
        period=f"orientation_bill:{booking.pk}:unpaid_notice",
    )


@dataclass(frozen=True)
class ReminderRun:
    """What one daily pass did: reminders sent, failed (retried next run) and skipped for good."""

    sent: int
    failed: int
    skipped: int


#: The orientor notice's line when the member's last reminder went out, and when it could not.
REACHED_LINE = "We've reminded them by email."
NOT_REACHED_LINE = "We couldn't reach them by email, so they may not know about it."

#: How many days after a step's due day a refused or raised send is still retried (and fails the run).
REMINDER_RETRY_DAYS = 2


def _put_back_or_give_up(pk: int, *, step: int, previous: int, today: date, due_on: date) -> bool:
    """After a send that did not go out: put the step back for a retry while within the grace, else keep it.

    Returns:
        True when the step was put back (the bill counts as failed); False when the grace has
        run out, so the step stays recorded and one dead address cannot keep the job failing.
    """
    from membership.models import OrientationBooking

    if (today - due_on).days <= REMINDER_RETRY_DAYS:
        OrientationBooking.undo_bill_reminder(pk, step=step, previous=previous)
        return True
    logger.error(
        "Orientation bill %s: reminder %s still could not be sent %s days after it was due; giving up on it.",
        pk,
        step,
        (today - due_on).days,
    )
    return False


def _remind(pk: int, today: date) -> str:
    """Send one bill the reminder due ``today``: ``"sent"``, ``"skipped"``, ``"failed"`` or ``"not_due"``.

    The step is saved under the row lock first, so a rerun or a second tick never sends it
    again. The bill is read again just before the email; one paid or cancelled in the gap is
    skipped with the step left recorded, and so is a member with no address to email (logged
    once, since the step is kept). When the email does not go out, or the send raises, within
    :data:`REMINDER_RETRY_DAYS` of the step's due day, the step goes back so the next run tries
    again; past that it is kept and the bill is skipped. The orientor hears only after the
    member's last reminder really went out, and a failure there is logged, never the bill's.

    Raises:
        Exception: Whatever the send raised within the retry days, after the step is put back.
    """
    from core.events import resolvers
    from membership.orientations import locked_booking_queryset

    with transaction.atomic():
        locked = locked_booking_queryset().get(pk=pk)
        step = locked.bill_reminder_due(today)
        if not step:
            return "not_due"
        previous = locked.bill_reminder_step
        locked.record_bill_reminder(step)
    booking = bills().get(pk=pk)
    if not booking.bill_is_unpaid:
        return "skipped"
    if not resolvers.single_user({"user": booking.member.user}):
        logger.warning("Orientation bill %s: the member has no email address, so reminder %s is skipped.", pk, step)
        _tell_orientor_if_last(booking, step, reached=False)
        return "skipped"
    due_on = booking.bill_reminder_due_on(step)
    try:
        delivered = _send_reminder_email(booking, step)
    except Exception:
        if _put_back_or_give_up(pk, step=step, previous=previous, today=today, due_on=due_on):
            raise
        logger.exception("Orientation bill %s: reminder %s raised past its retry days.", pk, step)
        _tell_orientor_if_last(booking, step, reached=False)
        return "skipped"
    if not delivered:
        if _put_back_or_give_up(pk, step=step, previous=previous, today=today, due_on=due_on):
            logger.warning("Orientation bill %s: reminder %s was not delivered; the next run tries again.", pk, step)
            return "failed"
        _tell_orientor_if_last(booking, step, reached=False)
        return "skipped"
    _tell_orientor_if_last(booking, step, reached=True)
    return "sent"


def _tell_orientor_if_last(booking: OrientationBooking, step: int, *, reached: bool) -> None:
    """On the last step, once it is kept, tell the orientor; a failure here is logged, never the bill's.

    ``reached`` says whether the member's reminder went out, so the notice never claims an email
    that did not happen.
    """
    from membership.models import OrientationBooking

    if not OrientationBooking.is_last_bill_reminder(step):
        return
    try:
        _tell_orientor_unpaid(booking, reached=reached)
    except Exception:
        logger.exception("Orientation bill %s: could not tell the orientor it is still unpaid.", booking.pk)


def send_due_reminders() -> ReminderRun:
    """Send each unpaid bill the reminder due today, at most one per bill: the daily job (#1120).

    Reminders go 7, 14 and 30 local days after ``billed_at``
    (:attr:`OrientationBooking.BILL_REMINDER_DAYS`); a bill that missed steps gets only the
    latest one due, counted as that step. Each bill decides under its row lock
    (:func:`membership.orientations.locked_booking_queryset`, see :func:`_remind`); each emit's
    period is per bill per step as a second guard. The last step also tells the orientor.
    Resend Bill never moves the step. One bill failing is logged and never stops the rest.

    Returns:
        How many reminders went out, failed (retried on the next run) and were skipped.
    """
    from membership.models import OrientationBooking

    today = timezone.localdate()
    sent = failed = skipped = 0
    for pk in list(OrientationBooking.objects.bills_awaiting_reminder(today).values_list("pk", flat=True)):
        try:
            outcome = _remind(pk, today)
        except Exception:
            logger.exception("Orientation bill %s: the reminder failed; the next run tries again.", pk)
            outcome = "failed"
        sent += outcome == "sent"
        failed += outcome == "failed"
        skipped += outcome == "skipped"
    return ReminderRun(sent=sent, failed=failed, skipped=skipped)


def cancel_bill(booking: OrientationBooking, *, actor: User) -> OrientationBooking:
    """Cancel an unpaid bill (#1111 part 2): nothing is owed, the member stays oriented.

    Under the booking row lock, so a payment landing at the same moment wins or loses cleanly
    (a paid bill is refunded, never cancelled). Logs one ``ORIENTATION_BILL_CANCELLED`` row
    naming ``actor``. After the commit the bill's open Checkout is expired best effort, so a
    Stripe tab left open cannot pay it; should one pay anyway, the webhook alerts the Billing
    Administrators. The member is not emailed: the bill's page reads cancelled.

    A late webhook must not let a paid bill be cancelled: when a Checkout was opened, Stripe
    is asked first (:func:`reconcile_landed_checkout`), and a payment it reports is stamped
    and the cancel refused. Stripe unreachable does not block the cancel; the webhook's alert
    is the backstop.

    Raises:
        OrientationError: When the bill is already paid or cancelled, or Stripe says it was just paid.
    """
    from membership.models import OrientationError
    from membership.orientations import locked_booking_queryset

    if booking.stripe_session_id and reconcile_landed_checkout(booking) == "paid":
        raise OrientationError(PAID_BEFORE_CANCEL)
    with transaction.atomic():
        locked = locked_booking_queryset().get(pk=booking.pk)
        locked.cancel_bill(by=actor)
        SiteActivity.log(
            SiteActivity.Kind.ORIENTATION_BILL_CANCELLED,
            actor=actor,
            target=locked,
            payload={
                "orientation_type": locked.orientation_type.name,
                "owner": locked.orientation_type.owner_name,
                "amount_cents": locked.bill_amount_cents,
            },
        )
    _expire_session_best_effort(locked)
    return locked


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
    from membership.models import OrientationBooking, OrientationError
    from membership.orientations import locked_booking_queryset

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
    # A Cancel Bill that landed during the Stripe call must not leave a live page to pay.
    with transaction.atomic():
        still_owed = locked_booking_queryset().get(pk=booking.pk).bill_is_unpaid
    if not still_owed:
        _expire_session_best_effort(booking)
        raise OrientationError(OrientationBooking.BILL_SETTLED_FOR_MEMBER)
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


def paid_after_cancel(booking: OrientationBooking) -> bool:
    """Whether a cancelled bill's last Checkout was paid anyway: the member's return page then says so.

    False when the bill is not cancelled, has no session, or Stripe cannot be asked.
    """
    from billing import stripe_utils

    if booking.bill_state != "cancelled" or not booking.stripe_session_id:
        return False
    try:
        session = stripe_utils.retrieve_checkout_session(session_id=booking.stripe_session_id)
    except Exception:
        logger.exception("Return page: could not check session for cancelled orientation bill %s.", booking.pk)
        return False
    return bool(session["payment_status"] == "paid")


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
