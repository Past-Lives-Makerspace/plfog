"""Equipment reservation service — reserve, cancel notifications, the ``.ics`` builder.

Mirrors :mod:`membership.orientations`: fat-model guards live on the models
(:meth:`Equipment.ensure_reservable`, :meth:`EquipmentReservation.cancel`); this
module owns the transaction + lock choreography and the notification fan-out.
All three PR 2 events go through the spine (``emit()`` + seeded copy); the emit
context supplies every placeholder the copy uses.
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
    equipment = reservation.equipment
    return {
        "member_name": reservation.member.display_name,
        "equipment_name": equipment.name,
        "reservation_when": when_display(reservation),
        "equipment_url": _absolute_url(reverse("hub_equipment_detail", args=[equipment.slug])),
    }


def reserve(
    equipment: Equipment,
    member: Member,
    starts_at: datetime,
    duration_minutes: int,
    *,
    purpose: str = "",
) -> EquipmentReservation:
    """Make an instant, self-confirmed reservation, safely under concurrency.

    Everything re-validates INSIDE ``transaction.atomic()`` with ``select_for_update``
    on the Equipment row — the same lock object every competing booking takes — so
    two members can never hold overlapping confirmed reservations. On success the
    member gets the confirmation (with a calendar invite) and the equipment's
    managers get the awareness ping.

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
            status=EquipmentReservation.Status.CONFIRMED,
        )
    _notify_confirmed(reservation)
    _notify_managers(reservation)
    return reservation


def _notify_confirmed(reservation: EquipmentReservation) -> None:
    """Tell the member their reservation is set — forced email with the ``.ics`` attached."""
    from core.events.emit import emit
    from core.events.registry import Channel

    member = reservation.member
    ics = ("reservation.ics", build_ics(reservation, method="REQUEST", status="CONFIRMED"), "text/calendar")
    emit(
        "equipment.reservation_confirmed",
        actor=member.user,
        target=reservation,
        context={"user": member.user, **_placeholder_context(reservation)},
        url=reverse("hub_equipment_detail", args=[reservation.equipment.slug]),
        attachments={Channel.EMAIL: [ics]},
        period=f"reservation:{reservation.pk}:confirmed",
    )


def _notify_managers(reservation: EquipmentReservation) -> None:
    """Awareness ping to the equipment's managers — no approval exists, so email defaults off."""
    from core.events.emit import emit

    emit(
        "equipment.reservation_made",
        actor=reservation.member.user,
        target=reservation,
        context={"equipment": reservation.equipment, **_placeholder_context(reservation)},
        url=reverse("hub_equipment_detail", args=[reservation.equipment.slug]),
        period=f"reservation:{reservation.pk}:made",
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
