"""Equipment reservation service — reserve, cancel notifications, the ``.ics`` builder.

Mirrors :mod:`membership.orientations`: fat-model guards live on the models
(:meth:`Equipment.ensure_reservable`, :meth:`EquipmentReservation.cancel`); this
module owns the transaction + lock choreography and the notification fan-out.
Every event goes through the spine (``emit()`` + seeded copy); the emit context
supplies every placeholder the copy uses. Equipment that needs approval (#748) makes
a request instead: the member hears it is in, the managers hear it needs them, and
the decision sends the confirmation or the decline.

Priced equipment (#749) copies the paid orientation flow by name: the reservation is held
``PENDING_PAYMENT`` under the equipment lock, Stripe Checkout opens, and money in hand
(the webhook, the return page, Pay now or the sweep) finalizes it exactly once into the
confirmed or awaiting approval row an unpriced booking would have been. Every decline and
cancel of a paid row refunds it in full (:func:`refund_if_paid`).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import icalendar
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from django.contrib.auth.models import User
    from django.db.models import QuerySet

    from billing.models import LateCancellationFee
    from membership.models import Equipment, EquipmentReservation, Member

logger = logging.getLogger(__name__)

#: The Checkout ``metadata.kind`` the webhook handlers filter on (#749).
CHECKOUT_KIND = "equipment_reservation"
_CHECKOUT_SALT = "equipment-reservation-checkout"
_CHECKOUT_MAX_AGE = 30 * 24 * 3600  # 30 days, like the orientation token: outlives any session by a wide margin
_CHECKOUT_SESSION_LIFETIME = timedelta(hours=1)  # Stripe expires_at; abandoned checkouts die server side


def _absolute_url(path: str) -> str:
    """Turn a relative hub path into an absolute URL using the member-site base."""
    base = settings.MEMBER_BASE_URL.rstrip("/")
    return f"{base}{path}"


def _time_display(value: datetime) -> str:
    """A local wall-clock time like "2:00 PM"."""
    local = timezone.localtime(value)
    hour = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"
    return f"{hour}:{local.minute:02d} {suffix}"


def when_display(reservation: EquipmentReservation) -> str:
    """The reservation's span in member words, local time: "Saturday, September 12, 2:00 PM to 4:00 PM"."""
    local_start = timezone.localtime(reservation.starts_at)
    return f"{local_start:%A, %B} {local_start.day}, {_time_display(reservation.starts_at)} to {_time_display(reservation.ends_at)}"


def build_ics(reservation: EquipmentReservation, *, method: str, status: str) -> bytes:
    """Build a single-VEVENT iCalendar invite for an equipment reservation.

    Args:
        reservation: The reservation to describe.
        method: iCalendar METHOD — "REQUEST" for create/update, "CANCEL" to retract.
        status: VEVENT STATUS — "CONFIRMED" or "CANCELLED".

    Returns:
        The serialized iCalendar bytes (suitable as an email attachment).
    """
    equipment = reservation.equipment
    cal = icalendar.Calendar()
    cal.add("prodid", "-//Past Lives Makerspace//Equipment//EN")
    cal.add("version", "2.0")
    cal.add("method", method)
    event = icalendar.Event()
    event.add("uid", f"equipment-reservation-{reservation.pk}@pastlives")
    event.add("summary", f"{equipment.name} reservation — Past Lives Makerspace")
    event.add("dtstart", reservation.starts_at)
    event.add("dtend", reservation.ends_at)
    event.add("dtstamp", timezone.now())
    event.add("status", status)
    if equipment.location_note:
        event.add("location", equipment.location_note)
    description = f"Your {equipment.name} reservation at Past Lives Makerspace."
    if reservation.purpose:
        description += f" {reservation.purpose}"
    event.add("description", description)
    cal.add_component(event)
    return cal.to_ical()


def _placeholder_context(reservation: EquipmentReservation) -> dict[str, str]:
    """The merge-field values shared by every equipment reservation event's copy."""
    from membership.late_cancel import booking_sentence, policy_for

    equipment = reservation.equipment
    return {
        "member_name": reservation.member.display_name,
        "equipment_name": equipment.name,
        "reservation_when": when_display(reservation),
        "equipment_url": _absolute_url(reverse("hub_equipment_detail", args=[equipment.slug])),
        # The late fee sentence while one applies, "" otherwise (#456). A manager of this
        # equipment never pays it (#633), so their copy never mentions it.
        "cancellation_policy": (
            "" if reservation.member.can_manage_equipment(equipment) else booking_sentence(policy_for(reservation))
        ),
    }


def reserve(
    equipment: Equipment,
    member: Member,
    starts_at: datetime,
    duration_minutes: int,
    *,
    purpose: str = "",
) -> EquipmentReservation:
    """Make a reservation, safely under concurrency: instant, or a request when the equipment needs approval.

    Everything re-validates INSIDE ``transaction.atomic()`` with ``select_for_update``
    on the Equipment row — the same lock object every competing booking takes — so
    two members can never hold overlapping time. On instant equipment the row is
    CONFIRMED, the member gets the confirmation (with a calendar invite) and the
    managers get the awareness ping. On equipment that needs approval (#748) the row
    is PENDING_APPROVAL, holding its time; the member gets the "request in" email and
    the managers the "needs approval" one in place of the ping. A manager of the equipment
    never waits on their own approval: their booking confirms at once, the same test
    (:meth:`Member.can_manage_equipment`) that exempts them from the late fee.

    Raises:
        EquipmentError: Propagated from :meth:`Equipment.ensure_reservable` with the
            member-facing message when any check fails (including a lost race).
    """
    from membership.models import Equipment, EquipmentReservation

    with transaction.atomic():
        locked = Equipment.objects.select_for_update().get(pk=equipment.pk)
        locked.ensure_reservable(member, starts_at, duration_minutes)
        reservation = EquipmentReservation.objects.create(
            equipment=locked,
            member=member,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=duration_minutes),
            purpose=purpose.strip(),
            status=(
                EquipmentReservation.Status.PENDING_APPROVAL
                if locked.requires_approval and not member.can_manage_equipment(locked)
                else EquipmentReservation.Status.CONFIRMED
            ),
        )
    if reservation.is_awaiting_approval:
        _notify_requested(reservation)
        _notify_needs_approval(reservation)
        return reservation
    _notify_confirmed(reservation)
    _notify_managers(reservation)
    return reservation


#: A block's reason is a short label on the day timeline ("Held · reason"), not a note (#657).
BLOCK_REASON_MAX_LENGTH = 80


def block_time(
    equipment: Equipment, actor: Member, starts_at: datetime, ends_at: datetime, *, reason: str
) -> EquipmentReservation:
    """Hold [starts_at, ends_at) on ``equipment`` for a manager, safely under concurrency (#657).

    Takes the same ``select_for_update`` lock on the Equipment row as :func:`reserve`, so a
    member booking and a manager block can never both win one interval. A block is an
    :class:`EquipmentReservation` of kind BLOCK held by ``actor``, with ``reason`` as its
    purpose. It sends nothing: no confirmation, no manager ping, no Discord post.

    Raises:
        EquipmentError: When the reason is blank or too long, or from
            :meth:`Equipment.ensure_blockable` when the span may not be held.
    """
    from membership.models import Equipment, EquipmentError, EquipmentReservation

    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise EquipmentError("Please give a reason members will see on the schedule.")
    if len(cleaned_reason) > BLOCK_REASON_MAX_LENGTH:
        raise EquipmentError(f"Keep the reason to {BLOCK_REASON_MAX_LENGTH} characters.")
    with transaction.atomic():
        locked = Equipment.objects.select_for_update().get(pk=equipment.pk)
        locked.ensure_blockable(actor, starts_at, ends_at)
        return EquipmentReservation.objects.create(
            equipment=locked,
            member=actor,
            kind=EquipmentReservation.Kind.BLOCK,
            starts_at=starts_at,
            ends_at=ends_at,
            purpose=cleaned_reason,
            status=EquipmentReservation.Status.CONFIRMED,
        )


def _notify_confirmed(reservation: EquipmentReservation, *, approved_by: Member | None = None) -> None:
    """Tell the member their reservation is set — forced email with the ``.ics`` attached.

    ``approved_by`` is the manager who approved a request (#748): the email then opens with
    "{manager} approved your reservation." An instant booking passes none, and its
    ``approval_line`` is "", so its email is word for word what it always was.
    """
    from core.events.emit import emit
    from core.events.registry import Channel

    member = reservation.member
    ics = ("reservation.ics", build_ics(reservation, method="REQUEST", status="CONFIRMED"), "text/calendar")
    # The trailing space joins the sentence to "Your reservation is set." in the copy.
    approval_line = f"{approved_by.display_name} approved your reservation. " if approved_by is not None else ""
    emit(
        "equipment.reservation_confirmed",
        actor=approved_by.user if approved_by is not None else member.user,
        target=reservation,
        context={"user": member.user, "approval_line": approval_line, **_placeholder_context(reservation)},
        url=reverse("hub_equipment_detail", args=[reservation.equipment.slug]),
        attachments={Channel.EMAIL: [ics]},
        period=f"reservation:{reservation.pk}:confirmed",
    )


def _notify_managers(reservation: EquipmentReservation, *, broadcast_only: bool = False) -> None:
    """Awareness ping to the equipment's managers — no approval exists, so email defaults off.

    Also the #reservations Discord broadcast: the event is pinned to the Site
    Settings ``discord_reservations_webhook_url`` (blank = silent no-op, never the
    central notify webhook). The context deliberately carries NO ``guild`` key —
    that is what keeps ``emit``'s guild dual-route dark, so a guild-owned tool's
    booking still posts to #reservations only, never the guild's own channel.

    ``broadcast_only`` is the approval of a request (#748): the booking feed hears it once the
    row is confirmed, but the managers are not pinged, since one of them just approved it.
    An empty ``recipient_user_ids`` empties the bell, push and email fan-out and leaves the
    Discord broadcast alone.
    """
    from core.events.emit import emit

    placeholders = _placeholder_context(reservation)
    emit(
        "equipment.reservation_made",
        actor=reservation.member.user,
        target=reservation,
        context={"equipment": reservation.equipment, **placeholders},
        # ABSOLUTE url, not reverse(): in copy mode this becomes the Discord embed's
        # url, and Discord rejects relative embed URLs with a silent-looking 400
        # (post_embed logs and returns False) — the channel would never hear a thing.
        url=placeholders["equipment_url"],
        period=f"reservation:{reservation.pk}:made",
        recipient_user_ids=set() if broadcast_only else None,
    )


def _manage_reservations_url(reservation: EquipmentReservation) -> str:
    """The absolute link to the equipment's Manage > Reservations tab, where a request is decided."""
    path = reverse("hub_equipment_manage", args=[reservation.equipment.slug])
    return _absolute_url(f"{path}?tab=reservations")


def _purpose_line(purpose: str) -> str:
    """The managers' email sentence naming the purpose, "" when none was given.

    Ends in a space so it runs into the next sentence, and never doubles a full stop the
    member already typed.
    """
    cleaned = purpose.strip()
    if not cleaned:
        return ""
    return f"Purpose: {cleaned.rstrip('.!?')}. "


def _notify_requested(reservation: EquipmentReservation) -> None:
    """Tell the member their request is in and the time is held (#748). Forced email, no invite yet."""
    from core.events.emit import emit

    member = reservation.member
    emit(
        "equipment.reservation_requested",
        actor=member.user,
        target=reservation,
        context={"user": member.user, **_placeholder_context(reservation)},
        url=reverse("hub_equipment_detail", args=[reservation.equipment.slug]),
        period=f"reservation:{reservation.pk}:requested",
    )


def _notify_needs_approval(reservation: EquipmentReservation) -> None:
    """Ask the equipment's managers to approve or decline a request (#748).

    The same audience as the awareness ping (``EQUIPMENT_MANAGERS``, as narrowed by #746),
    which it replaces for a request. No Discord: a request is not booked yet, so it is not
    news for the #reservations channel until it is approved (:func:`notify_approved`). The
    button opens Manage > Reservations.
    """
    from core.events.emit import emit

    placeholders = _placeholder_context(reservation)
    manage_url = _manage_reservations_url(reservation)
    emit(
        "equipment.reservation_needs_approval",
        actor=reservation.member.user,
        target=reservation,
        context={
            "equipment": reservation.equipment,
            "purpose_line": _purpose_line(reservation.purpose),
            "manage_url": manage_url,
            **placeholders,
        },
        url=manage_url,
        period=f"reservation:{reservation.pk}:needs_approval",
    )


def notify_approved(reservation: EquipmentReservation, manager: Member) -> None:
    """A manager approved a request (#748): the member gets the confirmation, opening with who approved it.

    The #reservations booking feed hears it now, as it hears an instant booking, through the
    same ``equipment.reservation_made`` post; the managers get no ping. A request itself
    posts nothing there.
    """
    _notify_confirmed(reservation, approved_by=manager)
    _notify_managers(reservation, broadcast_only=True)


def notify_declined(reservation: EquipmentReservation, manager: Member) -> None:
    """Tell the member ``manager`` declined their request, with the reason and a way back to pick another time.

    A paid request's refund sentence (#749) rides ``refund_line``; "" for a free one.
    """
    from core.events.emit import emit

    member = reservation.member
    emit(
        "equipment.reservation_declined",
        actor=manager.user,
        target=reservation,
        context={
            "user": member.user,
            "manager_name": manager.display_name,
            "decline_reason": reservation.cancelled_reason,
            "refund_line": refund_line(reservation),
            **_placeholder_context(reservation),
        },
        url=reverse("hub_equipment_detail", args=[reservation.equipment.slug]),
        period=f"reservation:{reservation.pk}:declined",
    )


def notify_manager_cancelled(reservation: EquipmentReservation) -> None:
    """Tell the member a manager cancelled their reservation, carrying the required reason and any refund (#749)."""
    from core.events.emit import emit

    member = reservation.member
    actor = reservation.cancelled_by
    emit(
        "equipment.reservation_cancelled_by_manager",
        actor=actor.user if actor is not None else None,
        target=reservation,
        context={
            "user": member.user,
            "cancel_reason": reservation.cancelled_reason,
            "refund_line": refund_line(reservation),
            **_placeholder_context(reservation),
        },
        url=reverse("hub_equipment_detail", args=[reservation.equipment.slug]),
        period=f"reservation:{reservation.pk}:manager_cancelled",
    )


def notify_self_cancelled(reservation: EquipmentReservation, fee: LateCancellationFee | None) -> None:
    """Confirm to the member that they cancelled, naming the late fee and its Pay link when one applies (#456).

    ``late_fee_line`` is the whole fee sentence for the text body and the bell row, and
    ``late_fee_html`` the same sentence with a real link for the HTML body; both are ""
    when the cancel was free, so the copy needs no conditional and a free cancel's email
    says nothing about fees.
    """
    from billing.late_fees import pay_html, pay_line
    from core.events.emit import emit

    member = reservation.member
    emit(
        "equipment.reservation_cancelled",
        actor=member.user,
        target=reservation,
        context={
            "user": member.user,
            "late_fee_line": pay_line(fee) if fee is not None else "",
            "late_fee_html": pay_html(fee) if fee is not None else "",
            "refund_line": refund_line(reservation),
            **_placeholder_context(reservation),
        },
        url=reverse("hub_equipment_detail", args=[reservation.equipment.slug]),
        period=f"reservation:{reservation.pk}:self_cancelled",
    )


# ── Priced reservations (#749): Stripe Checkout orchestration, copied from the orientation flow ──


def make_checkout_token(reservation: EquipmentReservation) -> str:
    """Sign a token authorizing the Checkout return and cancelled pages for one reservation."""
    return signing.dumps({"reservation": reservation.pk}, salt=_CHECKOUT_SALT)


def read_checkout_token(token: str) -> EquipmentReservation:
    """Decode a checkout token to its reservation.

    Raises:
        signing.BadSignature: If the token is invalid or expired.
        EquipmentReservation.DoesNotExist: If the reservation no longer exists (a released hold).
    """
    from membership.models import EquipmentReservation

    data = signing.loads(token, salt=_CHECKOUT_SALT, max_age=_CHECKOUT_MAX_AGE)
    return EquipmentReservation.objects.select_related("equipment", "member").get(pk=data["reservation"])


def _checkout_urls(reservation: EquipmentReservation) -> tuple[str, str]:
    """The Checkout ``success_url`` and ``cancel_url`` for one hold."""
    token = make_checkout_token(reservation)
    slug = reservation.equipment.slug
    return (
        _absolute_url(reverse("hub_equipment_checkout_return", args=[slug, token])),
        _absolute_url(reverse("hub_equipment_checkout_cancelled", args=[slug, token])),
    )


def start_reservation_checkout(
    equipment: Equipment,
    member: Member,
    starts_at: datetime,
    duration_minutes: int,
    *,
    purpose: str = "",
    amount_cents: int | None = None,
) -> str:
    """Hold the time ``PENDING_PAYMENT`` and open a Stripe Checkout for it; return the hosted Checkout URL.

    The charge is :meth:`Equipment.checkout_amount_cents`, read under the same
    ``select_for_update`` lock :func:`reserve` takes, after :meth:`Equipment.ensure_reservable`
    passes, so a lost race or a bad donation never reaches Stripe. The hold sends nothing:
    nothing has happened until the money lands (:func:`finalize_paid_reservation`). The lock
    covers only the guard and the hold, never the Stripe round trip. A Stripe failure deletes
    the hold and re-raises.

    Raises:
        EquipmentError: From the guards, the amount check, or when the amount comes to $0.
    """
    from billing import stripe_utils
    from membership.models import Equipment, EquipmentError, EquipmentReservation

    with transaction.atomic():
        locked = Equipment.objects.select_for_update().get(pk=equipment.pk)
        locked.ensure_reservable(member, starts_at, duration_minutes)
        charge_cents = locked.checkout_amount_cents(duration_minutes, amount_cents)
        if charge_cents <= 0:
            raise EquipmentError("Reserving this time doesn't charge anything.")
        # amount_paid_cents stays 0 until money is in hand; finalize stamps Stripe's amount_total.
        reservation = EquipmentReservation.objects.create(
            equipment=locked,
            member=member,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=duration_minutes),
            purpose=purpose.strip(),
            status=EquipmentReservation.Status.PENDING_PAYMENT,
        )
    success_url, cancel_url = _checkout_urls(reservation)
    try:
        session = stripe_utils.create_checkout_session(
            amount_cents=charge_cents,
            product_name=f"{locked.name} reservation, {when_display(reservation)}",
            customer_email=member.primary_email,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"kind": CHECKOUT_KIND, "reservation_id": str(reservation.pk)},
            # Stripe replays the first answer for a key, so a retried start never mints a second session.
            idempotency_key=f"reservation-checkout-{reservation.pk}",
            expires_at=int((timezone.now() + _CHECKOUT_SESSION_LIFETIME).timestamp()),
        )
    except Exception:
        _delete_hold(reservation, expire_session=False)
        raise
    reservation.stripe_session_id = session["id"]
    reservation.save(update_fields=["stripe_session_id"])
    return session["url"]


def locked_reservation_queryset() -> QuerySet[EquipmentReservation]:
    """The row locked reservation queryset every finalize path reads through.

    ``of=("self",)`` locks only the reservation row: the atomic block writes its columns and
    nothing else, and it keeps the clause valid should a joined relation ever become nullable
    (Postgres refuses FOR UPDATE on the nullable side of an outer join).
    """
    from membership.models import EquipmentReservation

    return EquipmentReservation.objects.select_for_update(of=("self",)).select_related("equipment", "member").all()


def finalize_paid_reservation(
    reservation: EquipmentReservation, *, payment_intent: str, amount_total: int | None, session_id: str = ""
) -> str:
    """Flip a ``PENDING_PAYMENT`` hold to the row an unpriced booking would be, with its emails.

    THE single "money is in hand" transition: the webhook, the return page, Pay now and the
    sweep all funnel through here, and it is safe to race. The row is re-fetched under
    ``select_for_update`` and only a still unpaid hold flips, so it finalizes exactly once and
    can never revive a released hold. It lands CONFIRMED (the confirmation and the managers'
    ping with the #reservations post), or PENDING_APPROVAL when the equipment needs approval
    and the member does not manage it (the request emails, #748). ``amount_total`` is
    canonical; ``session_id`` backfills a hold whose session id never got saved.

    Returns:
        ``"finalized"`` when this call flipped the hold; ``"already"`` when the row is no longer
        awaiting payment; ``"gone"`` when the row no longer exists.
    """
    from membership.models import EquipmentReservation

    with transaction.atomic():
        locked = locked_reservation_queryset().filter(pk=reservation.pk).first()
        if locked is None:
            return "gone"
        if locked.status != EquipmentReservation.Status.PENDING_PAYMENT:
            return "already"
        needs_approval = locked.equipment.requires_approval and not locked.member.can_manage_equipment(locked.equipment)
        locked.status = (
            EquipmentReservation.Status.PENDING_APPROVAL if needs_approval else EquipmentReservation.Status.CONFIRMED
        )
        locked.stripe_payment_id = payment_intent
        if session_id and not locked.stripe_session_id:
            locked.stripe_session_id = session_id
        if amount_total is not None:
            locked.amount_paid_cents = amount_total
        locked.save(update_fields=["status", "stripe_payment_id", "stripe_session_id", "amount_paid_cents"])
    if needs_approval:
        _notify_requested(locked)
        _notify_needs_approval(locked)
    else:
        _notify_confirmed(locked)
        _notify_managers(locked)
    return "finalized"


def _expire_session_best_effort(reservation: EquipmentReservation) -> None:
    """Expire the hold's Checkout Session so an open Stripe tab can't pay a released hold. Best effort."""
    from billing import stripe_utils

    if not reservation.stripe_session_id:
        return
    try:
        stripe_utils.expire_checkout_session(session_id=reservation.stripe_session_id)
    except Exception:
        logger.info("Could not expire Checkout session for reservation hold %s (best effort).", reservation.pk)


def _delete_hold(reservation: EquipmentReservation, *, expire_session: bool = True) -> None:
    """Delete an unpaid hold, never CANCELLED: nothing was sent about it, so nothing should remember it.

    The delete is status guarded, so a row a concurrent webhook just finalized is never
    destroyed, and a row already gone is a quiet no-op. Deleting frees the time.
    """
    from membership.models import EquipmentReservation

    if expire_session:
        _expire_session_best_effort(reservation)
    EquipmentReservation.objects.filter(pk=reservation.pk, status=EquipmentReservation.Status.PENDING_PAYMENT).delete()


def release_hold_if_unpaid(reservation: EquipmentReservation) -> str:
    """Release an unpaid hold, but only after asking Stripe, since a paid one's webhook may lag.

    Returns ``"released"`` (unpaid: deleted, its session expired best effort), ``"paid"`` (kept
    and finalized, the sweep's recovery) or ``"unknown"`` (Stripe unreachable: kept). A hold
    with no session id (a crash between create and save) is released outright.
    """
    from billing import stripe_utils

    if not reservation.stripe_session_id:
        _delete_hold(reservation, expire_session=False)
        return "released"
    try:
        session = stripe_utils.retrieve_checkout_session(session_id=reservation.stripe_session_id)
    except Exception:
        logger.exception("Could not verify Checkout session for reservation hold %s; keeping it.", reservation.pk)
        return "unknown"
    if session["payment_status"] == "paid":
        finalize_paid_reservation(
            reservation, payment_intent=session["payment_intent"], amount_total=session["amount_total"]
        )
        return "paid"
    _delete_hold(reservation)
    return "released"


def reconcile_landed_checkout(reservation: EquipmentReservation) -> str:
    """Ask Stripe and finalize a paid hold when the member lands on the return page.

    The webhook can lag, or never come where no endpoint is set up; this makes the return
    page show the confirmation on its first render.

    Returns:
        :func:`finalize_paid_reservation`'s outcome when Stripe says paid; ``"pending"`` when it
        is not paid yet (or there is no session to ask about); ``"unknown"`` when Stripe is
        unreachable.
    """
    from billing import stripe_utils

    if not reservation.stripe_session_id:
        return "pending"
    try:
        session = stripe_utils.retrieve_checkout_session(session_id=reservation.stripe_session_id)
    except Exception:
        logger.exception("Landing reconcile: could not verify session for reservation hold %s.", reservation.pk)
        return "unknown"
    if session["payment_status"] != "paid":
        return "pending"
    return finalize_paid_reservation(
        reservation, payment_intent=session["payment_intent"], amount_total=session["amount_total"]
    )


def resume_checkout(reservation: EquipmentReservation) -> tuple[str, str]:
    """Pay now on an unpaid hold: the same live Checkout, never a dead one.

    Returns:
        ``("open", url)`` while the session is open; ``("paid", "")`` when it was already paid
        (finalized here); ``("released", "")`` when it expired (the hold is released and the
        member picks a time again); ``("unknown", "")`` when Stripe is unreachable.
    """
    from billing import stripe_utils

    if not reservation.stripe_session_id:
        _delete_hold(reservation, expire_session=False)
        return ("released", "")
    try:
        session = stripe_utils.retrieve_checkout_session(session_id=reservation.stripe_session_id)
    except Exception:
        logger.exception("Pay now: could not retrieve Checkout session for reservation %s.", reservation.pk)
        return ("unknown", "")
    if session["payment_status"] == "paid":
        finalize_paid_reservation(
            reservation, payment_intent=session["payment_intent"], amount_total=session["amount_total"]
        )
        return ("paid", "")
    if session["status"] == "open" and session["url"]:
        return ("open", session["url"])
    _delete_hold(reservation, expire_session=False)
    return ("released", "")


def expire_payment_holds(*, now: datetime | None = None) -> tuple[int, int]:
    """Sweep unpaid holds older than two hours, asking Stripe first, never on age alone.

    For each: paid means finalize (the sweep IS the lost webhook recovery); unpaid or expired
    means release the hold and free its time; Stripe unreachable means skip and retry next
    tick. Idempotent.

    Returns:
        ``(released, recovered)`` counts.
    """
    from billing import stripe_utils
    from membership.models import EquipmentReservation

    cutoff = (now or timezone.now()) - EquipmentReservation.HOLD_SWEEP_AGE
    stale = EquipmentReservation.objects.filter(
        status=EquipmentReservation.Status.PENDING_PAYMENT, created_at__lt=cutoff
    ).select_related("equipment", "member")
    released = 0
    recovered = 0
    for reservation in stale:
        if not reservation.stripe_session_id:
            _delete_hold(reservation, expire_session=False)
            released += 1
            continue
        try:
            session = stripe_utils.retrieve_checkout_session(session_id=reservation.stripe_session_id)
        except Exception:
            logger.exception(
                "Hold sweep: could not verify session for reservation %s; retrying next tick.", reservation.pk
            )
            continue
        if session["payment_status"] == "paid":
            outcome = finalize_paid_reservation(
                reservation, payment_intent=session["payment_intent"], amount_total=session["amount_total"]
            )
            if outcome == "finalized":
                recovered += 1
        else:
            _delete_hold(reservation)
            released += 1
    return released, recovered


def refund_if_paid(reservation: EquipmentReservation, *, actor: User | None) -> str:
    """Refund a paid reservation in full on decline or cancel; flag, never block, on failure (#749).

    The state change is already saved when this runs. A refund Stripe refuses is logged and
    leaves the reservation at ``refund_state == "failed"`` for the Payments panel's Retry; the
    member's email still goes out. A free or already refunded row never touches the engine.

    Returns:
        ``"refunded"``, ``"failed"``, or ``""`` when there was nothing to refund.
    """
    from billing.exceptions import RefundError

    if reservation.amount_paid_cents <= 0 or reservation.refund_state != "none":
        return ""
    try:
        reservation.issue_refund(actor=actor)
    except RefundError:
        logger.exception(
            "Automatic refund failed for equipment reservation %s; flagged for retry in the Payments panel.",
            reservation.pk,
        )
        return "failed"
    return "refunded"


def refund_line(reservation: EquipmentReservation) -> str:
    """The emails' refund sentence for what :func:`refund_if_paid` just did, or "" when it did nothing.

    Ends in a space, like ``approval_line``, so it runs into whatever follows it in the copy.
    """
    if reservation.refund_outcome == "refunded":
        return f"Your {reservation.paid_display} has been refunded to your card. It can take 5 to 10 days to show. "
    if reservation.refund_outcome == "failed":
        return f"Your {reservation.paid_display} refund is being processed. "
    return ""
