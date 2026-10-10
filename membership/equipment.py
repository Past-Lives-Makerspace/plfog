"""Equipment reservation service — reserve, cancel notifications, the ``.ics`` builder.

Mirrors :mod:`membership.orientations`: fat-model guards live on the models
(:meth:`Equipment.ensure_reservable`, :meth:`EquipmentReservation.cancel`); this
module owns the transaction + lock choreography and the notification fan-out.
Every event goes through the spine (``emit()`` + seeded copy); the emit context
supplies every placeholder the copy uses. Equipment that needs approval (#748) makes
a request instead: the member hears it is in, the managers hear it needs them, and
the decision sends the confirmation or the decline.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import icalendar
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from billing.models import LateCancellationFee
    from membership.models import Equipment, EquipmentReservation, Member


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
    """Tell the member ``manager`` declined their request, with the reason and a way back to pick another time."""
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
            **_placeholder_context(reservation),
        },
        url=reverse("hub_equipment_detail", args=[reservation.equipment.slug]),
        period=f"reservation:{reservation.pk}:declined",
    )


def notify_manager_cancelled(reservation: EquipmentReservation) -> None:
    """Tell the member a manager cancelled their reservation, carrying the required reason."""
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
            **_placeholder_context(reservation),
        },
        url=reverse("hub_equipment_detail", args=[reservation.equipment.slug]),
        period=f"reservation:{reservation.pk}:self_cancelled",
    )
