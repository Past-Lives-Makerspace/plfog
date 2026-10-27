"""The facility tour emails and notices (#948): the visitor's, with a calendar invite, and the guide's.

Every send goes through the notification spine with its email rendered from a structural shell
in ``templates/tours/emails/`` (:func:`core.events.senders.emit_with_email_shell`):

- ``tour.visitor_update``: the visitor's confirmation, moved, cancelled and reminder emails, by
  address (``email_to``) since a visitor usually has no account. Each carries an ``.ics`` with
  the booking's stable UID and its SEQUENCE, and the Reschedule and Cancel links.
- ``tour.booked``: the guide's "New tour booked" (email, bell and push).
- ``tour.visitor_changed``: the guide hears every move and cancel a visitor makes, and every one a
  staff member other than themselves makes (#949).
- ``tour.checked_in``: the visitor checked in at the front desk kiosk (#950), bell and push only
  ("Your 4:00 tour is here").

A staff move or cancel (#949) passes ``actor`` (the User who acted) and ``staff_message``: the
visitor's email carries the message, and only the guides who are not ``actor`` are told, with
copy naming who acted. Every guide link opens the Tours page's Upcoming tab.

The reminder goes 24 hours before through the scheduled occurrences walker
(:func:`tour_reminder_occurrences`, the ``send_tour_reminders`` job), once per start.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import icalendar
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from core.events.channels import Message
from core.events.emit import emit
from core.events.registry import Channel
from core.events.scheduler import ScheduledOccurrence
from core.events.scheduling import DEFAULT_WINDOW
from core.events.senders import email_shell_message, emit_with_email_shell
from tours import signals
from tours.models import TOUR_ADDRESS, FacilityTourSettings, TourBooking

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from membership.models import Member

VISITOR_EVENT = "tour.visitor_update"
GUIDE_BOOKED_EVENT = "tour.booked"
GUIDE_CHANGED_EVENT = "tour.visitor_changed"
GUIDE_CHECKED_IN_EVENT = "tour.checked_in"
#: How long before a tour its reminder goes out.
REMINDER_BEFORE = timedelta(hours=24)


def tours_url(path: str) -> str:
    """An absolute link on the tours site (``TOURS_BASE_URL``), for emails."""
    return f"{settings.TOURS_BASE_URL.rstrip('/')}{path}"


def visitor_links(booking: TourBooking) -> dict[str, str]:
    """The visitor's absolute links: the booking page, Reschedule, Cancel, and booking another tour."""
    token = booking.self_serve_token
    return {
        "manage_url": tours_url(reverse("tours_public:manage", args=[token])),
        "reschedule_url": tours_url(reverse("tours_public:reschedule", args=[token])),
        "cancel_url": tours_url(reverse("tours_public:cancel", args=[token])),
        "book_url": tours_url("/"),
    }


def when_label(moment: datetime) -> str:
    """``Tuesday, November 3 at 4:00 PM``, in the makerspace's time."""
    local = timezone.localtime(moment)
    return f"{local:%A, %B} {local.day} at {local:%-I:%M %p}"


def short_when(moment: datetime) -> str:
    """``Tue, Nov 3 at 4:00 PM``, for subjects and bell rows."""
    local = timezone.localtime(moment)
    return f"{local:%a, %b} {local.day} at {local:%-I:%M %p}"


def guide_first_name(guide: Member | None) -> str:
    """The first word of the guide's name, or "" with no guide."""
    if guide is None:
        return ""
    name = guide.display_name.strip()
    return name.split()[0] if name else ""


#: Who acted, when the account carries no name and no email.
UNNAMED_STAFF = "Past Lives staff"


def person_name(user: User) -> str:
    """Who acted, as guides read it: the member's name, else the account's, else its email, never empty."""
    member: Member | None = getattr(user, "member", None)
    if member is not None and member.display_name.strip():
        return member.display_name.strip()
    return user.get_full_name().strip() or user.email.strip() or UNNAMED_STAFF


def note_label(actor: User | None) -> str:
    """The heading over a staff member's note in the visitor's email: ``A note from Gina``.

    Their first name when the member or account has one, else ``A note from Past Lives`` (never
    an email address, never an empty name). Empty with no actor, where no note is shown.
    """
    if actor is None:
        return ""
    member: Member | None = getattr(actor, "member", None)
    name = member.display_name if member is not None else actor.get_full_name()
    first = (name.split() or [""])[0]
    return f"A note from {first or 'Past Lives'}"


def tours_page_url() -> str:
    """The Tours page's Upcoming tab, absolute, for the guide's emails."""
    return f"{settings.MEMBER_BASE_URL.rstrip('/')}{reverse('tours:upcoming')}"


def _people(booking: TourBooking) -> str:
    """``1 person`` or ``3 people``."""
    return "1 person" if booking.party_size == 1 else f"{booking.party_size} people"


def build_ics(booking: TourBooking, *, cancelled: bool = False) -> bytes:
    """A single event calendar invite for the booking, like ``membership.orientations.build_ics``.

    The UID is the booking's for its whole life (:attr:`TourBooking.ics_uid`) and SEQUENCE is
    :attr:`TourBooking.sequence`, which every move and cancel raises, so a calendar that holds
    the event updates it in place rather than adding a second one.

    Args:
        booking: the booking to describe.
        cancelled: write a ``METHOD:CANCEL`` / ``STATUS:CANCELLED`` retraction.

    Returns:
        The serialized iCalendar bytes.
    """
    cal = icalendar.Calendar()
    cal.add("prodid", "-//Past Lives Makerspace//Tours//EN")
    cal.add("version", "2.0")
    cal.add("method", "CANCEL" if cancelled else "REQUEST")
    event = icalendar.Event()
    event.add("uid", booking.ics_uid)
    event.add("summary", "Past Lives Makerspace tour")
    # In UTC, so every calendar reads the same instant without needing a time zone block.
    event.add("dtstart", booking.starts_at.astimezone(UTC))
    event.add("dtend", booking.ends_at.astimezone(UTC))
    event.add("dtstamp", timezone.now())
    event.add("sequence", booking.sequence)
    event.add("status", "CANCELLED" if cancelled else "CONFIRMED")
    event.add("location", TOUR_ADDRESS)
    event.add(
        "description",
        f"A tour of Past Lives Makerspace for {_people(booking)}. "
        f"To reschedule or cancel: {visitor_links(booking)['manage_url']}",
    )
    cal.add_component(event)
    return bytes(cal.to_ical())


def _ics_attachment(booking: TourBooking, *, cancelled: bool = False) -> tuple[str, bytes, str]:
    return ("tour.ics", build_ics(booking, cancelled=cancelled), "text/calendar")


def support_email() -> str:
    """Where visitors write when they cannot change a tour online (Site Settings' support email)."""
    from core.models import SiteConfiguration

    return SiteConfiguration.load().org_support_email or "info@pastlives.space"


def visitor_context(booking: TourBooking, **extra: Any) -> dict[str, Any]:
    """What every visitor email renders against."""
    return {
        "booking": booking,
        "greeting_name": booking.visitor_first_name,
        "when": when_label(booking.starts_at),
        "address": TOUR_ADDRESS,
        "guide_name": guide_first_name(booking.guide),
        "support_email": support_email(),
        "cancel_cutoff_hours": FacilityTourSettings.load().cancel_cutoff_hours,
        **visitor_links(booking),
        **extra,
    }


#: The visitor's emails: template name and subject, by what happened.
_VISITOR_EMAILS: dict[str, tuple[str, str]] = {
    signals.BOOKED: ("visitor_confirmed", "Your Past Lives tour is booked: {when}"),
    signals.MOVED: ("visitor_moved", "Your Past Lives tour moved to {when}"),
    signals.CANCELLED: ("visitor_cancelled", "Your Past Lives tour on {when} is cancelled"),
}


def send_visitor_email(booking: TourBooking, change: str, **extra: Any) -> None:
    """Email the visitor that their tour was booked, moved or cancelled, with the calendar invite."""
    template, subject = _VISITOR_EMAILS[change]
    cancelled = change == signals.CANCELLED
    emit_with_email_shell(
        VISITOR_EVENT,
        target=booking,
        context={"user": None},
        subject=subject.format(when=short_when(booking.starts_at)),
        text_template=f"tours/emails/{template}.txt",
        html_template=f"tours/emails/{template}.html",
        template_context=visitor_context(booking, **extra),
        attachments=[_ics_attachment(booking, cancelled=cancelled)],
        email_to=booking.visitor_email,
        period=f"tour:{booking.pk}:{change}:{booking.sequence}",
        recipient_user_ids=set(),
    )


def guide_context(booking: TourBooking, **extra: Any) -> dict[str, Any]:
    """What the guide's emails render against."""
    return {
        "booking": booking,
        "when": when_label(booking.starts_at),
        "people": _people(booking),
        "tours_page_url": tours_page_url(),
        **extra,
    }


def tell_guide_booked(booking: TourBooking, guide: Member | None, *, actor_name: str = "") -> None:
    """Tell ``guide`` (or the admins when there is none) a tour of theirs was booked.

    With ``actor_name`` (#949) a staff member moved someone else's tour to this guide.
    """
    when = short_when(booking.starts_at)
    if actor_name:
        title, subject = "Tour moved to you", f"Tour moved to you: {when}"
        body = f"{actor_name} moved {booking.visitor_name}'s tour, {_people(booking)}, to you: {when}."
    else:
        title, subject = "New tour booked", f"New tour booked: {when}"
        body = f"{booking.visitor_name}, {_people(booking)}, {when}."
    emit_with_email_shell(
        GUIDE_BOOKED_EVENT,
        target=booking,
        context={"guide": guide},
        subject=subject,
        text_template="tours/emails/guide_booked.txt",
        html_template="tours/emails/guide_booked.html",
        template_context=guide_context(booking, actor_name=actor_name),
        in_app_title=title,
        in_app_body=body,
        url=reverse("tours:upcoming"),
        period=f"tour:{booking.pk}:booked:{guide.pk if guide else 0}:{booking.sequence}",
    )


def tell_guide_changed(
    booking: TourBooking,
    guide: Member | None,
    change: str,
    *,
    previous_starts_at: datetime | None = None,
    actor_name: str = "",
    staff_message: str = "",
) -> None:
    """Tell ``guide`` the tour was moved (from ``previous_starts_at``) or cancelled.

    By the visitor, or with ``actor_name`` by a staff member (#949), whose ``staff_message`` to the
    visitor the guide sees too.
    """
    when = short_when(previous_starts_at or booking.starts_at)
    stays_with_guide = guide is not None and booking.guide_id == guide.pk
    who = actor_name or booking.visitor_name
    whose = f"{booking.visitor_name}'s" if actor_name else "their"
    if change == signals.CANCELLED:
        title, body = "Tour cancelled", f"{who} cancelled {whose} tour on {when}."
        subject = f"Tour cancelled: {when}"
    else:
        new_when = short_when(booking.starts_at)
        title, subject = "Tour moved", f"Tour moved: {when} to {new_when}"
        if stays_with_guide or not actor_name:
            body = f"{who} moved {whose} tour on {when} to {new_when}."
        else:
            body = f"{who} moved {whose} tour on {when} to {guide_first_name(booking.guide)}."
    emit_with_email_shell(
        GUIDE_CHANGED_EVENT,
        target=booking,
        context={"guide": guide},
        subject=subject,
        text_template="tours/emails/guide_changed.txt",
        html_template="tours/emails/guide_changed.html",
        template_context=guide_context(
            booking,
            change=change,
            previous_when=when_label(previous_starts_at) if previous_starts_at else "",
            stays_with_guide=stays_with_guide,
            actor_name=actor_name,
            staff_message=staff_message,
            new_guide_name=guide_first_name(booking.guide),
        ),
        in_app_title=title,
        in_app_body=body,
        url=reverse("tours:upcoming"),
        period=f"tour:{booking.pk}:{change}:{guide.pk if guide else 0}:{booking.sequence}",
    )


def announce(
    booking: TourBooking,
    change: str,
    *,
    previous_guide: Member | None = None,
    previous_starts_at: datetime | None = None,
    actor: User | None = None,
    staff_message: str = "",
) -> None:
    """Every email and notice for a booking just booked, moved or cancelled (after commit).

    A visitor's move to another guide's tour tells the old guide it moved away and the new guide
    it was booked; a move within one guide's tours tells that guide once. A staff change
    (``actor``, #949) is told the same way to every guide involved except ``actor``.
    """
    send_visitor_email(
        booking,
        change,
        previous_when=when_label(previous_starts_at) if previous_starts_at else "",
        staff_message=staff_message,
        by_staff=actor is not None,
        note_label=note_label(actor),
    )
    if actor is not None:
        _tell_guides_staff_changed(booking, change, actor, previous_guide, previous_starts_at, staff_message)
        return
    if change == signals.BOOKED:
        tell_guide_booked(booking, booking.guide)
        return
    if change == signals.CANCELLED:
        tell_guide_changed(booking, booking.guide, change)
        return
    tell_guide_changed(booking, previous_guide, change, previous_starts_at=previous_starts_at)
    if previous_guide is None or booking.guide_id != previous_guide.pk:
        tell_guide_booked(booking, booking.guide)


def _tell_guides_staff_changed(
    booking: TourBooking,
    change: str,
    actor: User,
    previous_guide: Member | None,
    previous_starts_at: datetime | None,
    staff_message: str,
) -> None:
    """Tell the guides of a staff move or cancel (#949), never the one who made it.

    A tour with no guide tells nobody: the staff member who acted is the one looking after it.
    """
    name = person_name(actor)

    def someone_else(guide: Member | None) -> bool:
        return guide is not None and guide.user_id != actor.pk

    if change == signals.CANCELLED:
        if someone_else(booking.guide):
            tell_guide_changed(booking, booking.guide, change, actor_name=name, staff_message=staff_message)
        return
    if someone_else(previous_guide):
        tell_guide_changed(
            booking,
            previous_guide,
            change,
            previous_starts_at=previous_starts_at,
            actor_name=name,
            staff_message=staff_message,
        )
    reassigned = previous_guide is None or booking.guide_id != previous_guide.pk
    if reassigned and someone_else(booking.guide):
        tell_guide_booked(booking, booking.guide, actor_name=name)


def tell_guide_checked_in(booking: TourBooking) -> None:
    """Ping the tour's guide (or the admins when it has none) that the visitor is at the front desk (#950).

    Bell and push only, "Your 4:00 tour is here", or for the admins of a tour with no guide "A 4:00
    tour is here (no guide)". The period is the booking, so a second check in never pings twice.
    """
    local = timezone.localtime(booking.starts_at)
    title = f"Your {local:%-I:%M} tour is here" if booking.guide else f"A {local:%-I:%M} tour is here (no guide)"
    emit(
        GUIDE_CHECKED_IN_EVENT,
        target=booking,
        context={"guide": booking.guide},
        title=title,
        body=f"{booking.visitor_name}, {_people(booking)}, checked in at the front desk.",
        url=reverse("tours:upcoming"),
        period=f"tour:{booking.pk}:checked_in",
    )


def build_reminder_occurrence(booking: TourBooking) -> ScheduledOccurrence:
    """The visitor's reminder, 24 hours before the tour, as a scheduled occurrence.

    Keyed on the booking and its start, so it goes once per start: a moved tour gets a
    reminder for its new time, and a re-run of the tick never sends a second.
    """
    message: Message = email_shell_message(
        event_key=VISITOR_EVENT,
        subject=f"Reminder: your Past Lives tour is {short_when(booking.starts_at)}",
        text_template="tours/emails/visitor_reminder.txt",
        html_template="tours/emails/visitor_reminder.html",
        template_context=visitor_context(booking),
    )
    return ScheduledOccurrence(
        event_key=VISITOR_EVENT,
        anchor=booking.starts_at,
        offset=-REMINDER_BEFORE,
        context={"user": None},
        period=f"tour:{booking.pk}:reminder:{booking.starts_at:%Y%m%dT%H%MZ}",
        target=booking,
        messages={Channel.EMAIL: message},
        attachments={Channel.EMAIL: [_ics_attachment(booking)]},
        email_to=booking.visitor_email,
    )


def tour_reminder_occurrences(now: datetime) -> Iterator[ScheduledOccurrence]:
    """This tick's reminder candidates: booked tours starting 24 hours from now, give or take a tick.

    Cancelled tours are never candidates. The walker (:func:`core.events.scheduler.run_due`)
    fires only the ones whose fire time falls in this tick's window.
    """
    first = now + REMINDER_BEFORE
    bookings = TourBooking.objects.filter(
        status=TourBooking.Status.BOOKED,
        starts_at__gte=first,
        starts_at__lt=first + DEFAULT_WINDOW,
    ).select_related("guide")
    for booking in bookings:
        yield build_reminder_occurrence(booking)
