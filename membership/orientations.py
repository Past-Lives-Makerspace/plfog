"""Orientation orchestration: iCal invites, transactional emails, activity + notifications.

The models hold state and guards; this module wires the side effects around each
lifecycle transition (book → confirm / decline / cancel). Every member-facing
email carries an ``.ics`` so Google/Outlook can track the appointment, and each
transition logs ``SiteActivity`` and fires an in-app notification.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import icalendar
from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from core import email as core_email
from core import notifications
from core.models import SiteActivity

if TYPE_CHECKING:
    from membership.models import Member, OrientationBooking, OrientationSlot


def _absolute_url(path: str) -> str:
    """Turn a relative hub path into an absolute URL using the member-site base."""
    base = settings.MEMBER_BASE_URL.rstrip("/")
    return f"{base}{path}"


def build_ics(booking: OrientationBooking, *, method: str, status: str) -> bytes:
    """Build a single-VEVENT iCalendar invite for an orientation booking.

    Args:
        booking: The orientation booking to describe.
        method: iCalendar METHOD — "REQUEST" for create/update, "CANCEL" to retract.
        status: VEVENT STATUS — "TENTATIVE", "CONFIRMED", or "CANCELLED".

    Returns:
        The serialized iCalendar bytes (suitable as an email attachment).
    """
    slot = booking.slot
    cal = icalendar.Calendar()
    cal.add("prodid", "-//Past Lives Makerspace//Orientations//EN")
    cal.add("version", "2.0")
    cal.add("method", method)
    event = icalendar.Event()
    event.add("uid", f"orientation-{booking.pk}@pastlives")
    event.add("summary", f"Orientation — {booking.guild.name}")
    event.add("dtstart", slot.starts_at)
    event.add("dtend", slot.ends_at)
    event.add("dtstamp", timezone.now())
    event.add("status", status)
    if slot.location:
        event.add("location", slot.location)
    event.add("description", f"Orientation for {booking.guild.name} at Past Lives Makerspace.")
    cal.add_component(event)
    return cal.to_ical()


def _context(booking: OrientationBooking, **extra: Any) -> dict[str, Any]:
    member = booking.member
    return {
        "booking": booking,
        "slot": booking.slot,
        "guild": booking.guild,
        "greeting_name": member.display_name,
        "guild_url": _absolute_url(reverse("hub_guild_detail", args=[booking.guild_id])),
        **extra,
    }


def _send_member_email(
    booking: OrientationBooking, *, subject: str, template: str, trigger_kind: str, ics: tuple[str, bytes, str] | None
) -> None:
    ctx = _context(booking)
    text_body = render_to_string(f"membership/emails/{template}.txt", ctx)
    html_body = render_to_string(f"membership/emails/{template}.html", ctx)
    core_email.send(
        to=booking.member.primary_email,
        subject=subject,
        trigger_kind=trigger_kind,
        text_body=text_body,
        html_body=html_body,
        best_effort=True,
        attachments=[ics] if ics is not None else None,
    )


def _ics(booking: OrientationBooking, *, method: str, status: str) -> tuple[str, bytes, str]:
    return ("orientation.ics", build_ics(booking, method=method, status=status), "text/calendar")


def _notify_member(booking: OrientationBooking, *, title: str, body: str) -> None:
    user = booking.member.user
    if user is not None:
        notifications.dispatch(
            "orientation_update",
            [user],
            title=title,
            body=body,
            url=reverse("hub_guild_detail", args=[booking.guild_id]),
        )


def request_orientation(slot: OrientationSlot, member: Member, *, note: str = "") -> OrientationBooking:
    """Book a slot (REQUESTED) and fan out the request emails, activity, and lead notification.

    Raises:
        OrientationError: Propagated from ``slot.book`` when the slot can't be booked.
    """
    booking = slot.book(member, note=note)
    _send_member_email(
        booking,
        subject=f"Orientation request received — {booking.guild.name}",
        template="orientation_request",
        trigger_kind="orientations.request",
        ics=_ics(booking, method="REQUEST", status="TENTATIVE"),
    )
    _send_lead_request_email(booking)
    SiteActivity.log(SiteActivity.Kind.ORIENTATION_REQUESTED, actor=member.user, target=booking)
    lead = booking.guild.guild_lead
    if lead is not None and lead.user is not None:
        notifications.dispatch(
            "orientation_requested",
            [lead.user],
            title="New orientation request",
            body=f"{member.display_name} requested an orientation for {booking.guild.name}.",
            url=reverse("hub_orientation_respond", args=[booking.pk]),
        )
    return booking


def _send_lead_request_email(booking: OrientationBooking) -> None:
    lead = booking.guild.guild_lead
    if lead is None:
        return
    ctx = _context(booking, respond_url=_absolute_url(reverse("hub_orientation_respond", args=[booking.pk])))
    text_body = render_to_string("membership/emails/orientation_lead_request.txt", ctx)
    html_body = render_to_string("membership/emails/orientation_lead_request.html", ctx)
    core_email.send(
        to=lead.primary_email,
        subject=f"New orientation request — {booking.guild.name}",
        trigger_kind="orientations.lead_request",
        text_body=text_body,
        html_body=html_body,
        best_effort=True,
    )


def confirm_orientation(booking: OrientationBooking, *, oriented_by: Member | None = None) -> None:
    """Confirm a request: update state, email the member a CONFIRMED invite, log + notify."""
    booking.confirm(oriented_by=oriented_by)
    _send_member_email(
        booking,
        subject=f"Orientation confirmed — {booking.guild.name}",
        template="orientation_confirmed",
        trigger_kind="orientations.confirmed",
        ics=_ics(booking, method="REQUEST", status="CONFIRMED"),
    )
    actor = booking.oriented_by.user if booking.oriented_by is not None else None
    SiteActivity.log(SiteActivity.Kind.ORIENTATION_CONFIRMED, actor=actor, target=booking)
    _notify_member(
        booking,
        title="Orientation confirmed",
        body=f"Your orientation for {booking.guild.name} is confirmed.",
    )


def decline_orientation(booking: OrientationBooking, *, note: str = "") -> None:
    """Decline a request: update state, email the member, log + notify."""
    booking.decline(note=note)
    _send_member_email(
        booking,
        subject=f"About your orientation request — {booking.guild.name}",
        template="orientation_declined",
        trigger_kind="orientations.declined",
        ics=None,
    )
    SiteActivity.log(SiteActivity.Kind.ORIENTATION_DECLINED, actor=None, target=booking)
    _notify_member(
        booking,
        title="Orientation not confirmed",
        body=f"Your orientation request for {booking.guild.name} couldn't be confirmed.",
    )


def cancel_orientation(booking: OrientationBooking, *, actor_label: str) -> None:
    """Cancel a booking: update state, email the member, notify the lead, log."""
    booking.cancel()
    _send_member_email(
        booking,
        subject=f"Orientation cancelled — {booking.guild.name}",
        template="orientation_cancelled",
        trigger_kind="orientations.cancelled",
        ics=_ics(booking, method="CANCEL", status="CANCELLED"),
    )
    SiteActivity.log(SiteActivity.Kind.ORIENTATION_CANCELLED, actor=None, target=booking)
    lead = booking.guild.guild_lead
    if lead is not None and lead.user is not None:
        notifications.dispatch(
            "orientation_requested",
            [lead.user],
            title="Orientation cancelled",
            body=f"{actor_label} cancelled the orientation for {booking.guild.name}.",
            url=reverse("hub_orientation_respond", args=[booking.pk]),
        )
    _notify_member(
        booking,
        title="Orientation cancelled",
        body=f"The orientation for {booking.guild.name} was cancelled.",
    )


def cancel_slot(slot: OrientationSlot, *, reason: str = "") -> None:
    """Cancel a slot and run the full cancel fan-out for each of its active bookings."""
    active = list(slot.bookings.active())
    slot.is_cancelled = True
    slot.cancelled_reason = reason
    slot.save(update_fields=["is_cancelled", "cancelled_reason"])
    for booking in active:
        cancel_orientation(booking, actor_label="the guild")
