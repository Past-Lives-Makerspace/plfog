"""A Tour Guide's Google Calendar, both ways (#951): tours written to it, busy times read from it.

**Writes.** :func:`queue_booking_sync` hangs off :data:`tours.signals.tour_booking_changed` (sent after
commit) and only marks the booking's :class:`~tours.models.TourCalendarSync` row ``due``: nothing
talks to Google inside the request that saved a booking. The ``sync_tour_calendars`` job (every
tick) runs :func:`sync_due_bookings`, which brings each due row to what its booking says now
(:func:`sync_booking`): an event on the guide's chosen calendar with the current times, title
"Past Lives tour: <visitor> (<party size>)" and the visitor's contact in the description; none for
a cancelled tour; and an event left on another guide's calendar (a reassignment) or on a calendar
the guide stopped using is deleted first. Event ids are ours (:func:`event_id_for`), so a create
that reached Google but not our database, or a reconnect after a disconnect, updates the event in
place rather than adding a second. A failure leaves the row due with a growing wait
(:data:`RETRY_BASE` doubling up to :data:`RETRY_CAP`); the booking never waits on Google.

**Reads.** Each connected guide's busy times over the booking horizon are cached on their
connection (:func:`read_busy`): every event on their main calendar and their chosen one that blocks
time, except our own tour events (they carry :data:`EVENT_MARKER`), so a guide's other seats at a
tour's time stay offered. The job refreshes them every tick, and a booking page or the kiosk
refreshes any older than :data:`BUSY_FRESH` on load (:func:`refresh_stale_busy`, a few seconds at
most, never an error page). :mod:`tours.availability` hides a visitor's tour that overlaps a cached
span. A tour already booked is never taken away: the guide gets a bell instead
(:func:`alert_busy_conflicts`), once per booking and start.

**Broken.** When Google refuses the token (revoked, expired, a permission unticked), the
connection turns broken (:func:`mark_broken`): its tokens are cleared, the guide gets one email and
bell, their busy times stop counting (their hours offer as if unconnected) and nothing is written
until they reconnect from My Hours.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time as clock
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from tours import google
from tours.models import (
    TOUR_ADDRESS,
    FacilityTourSettings,
    TourBooking,
    TourCalendarConnection,
    TourCalendarSync,
    TourHours,
)

if TYPE_CHECKING:
    from membership.models import Member

logger = logging.getLogger(__name__)

#: Busy times older than this are read again when a booking page or the kiosk loads.
BUSY_FRESH = timedelta(minutes=15)
#: The job reads again anything older than this, so a tick that runs a little early still reads.
JOB_REFRESH_AFTER = timedelta(minutes=10)
#: After a failed read on a page load, page loads leave Google alone this long.
ON_DEMAND_RETRY = timedelta(minutes=5)
#: The most a page load spends reading busy times, and on any one call.
ON_DEMAND_BUDGET_SECONDS = 4.0
ON_DEMAND_CALL_TIMEOUT = 3.0
#: An access token this close to expiring is refreshed first.
TOKEN_MARGIN = timedelta(minutes=2)
#: The wait after a failed write: 15 minutes, doubling each time, at most 6 hours.
RETRY_BASE = timedelta(minutes=15)
RETRY_CAP = timedelta(hours=6)
#: A write still failing a day after its tour ended is given up.
GIVE_UP_AFTER_END = timedelta(days=1)
#: The job fails (and alerts the Webmasters, once a day) when a write has failed this many times
#: in a row, or a connected calendar's busy times are older than :data:`STALE_ALERT`.
STUCK_ATTEMPTS = 4
STALE_ALERT = timedelta(hours=1)
#: Rows one run writes at most.
SYNC_BATCH = 200
#: The private extended property on every tour event we write: how the busy read skips them.
EVENT_MARKER = "plfogTour"

BUSY_EVENT = "tour.calendar_busy"
BROKEN_EVENT = "tour.calendar_broken"
BUSY_TITLE = "Your calendar now shows you busy during a booked tour"

_DUE: dict[str, Any] = {"due": True, "attempts": 0, "next_attempt_at": None, "last_error": ""}


class _Waiting(Exception):
    """The row needs a connection that is broken: it stays due until the guide reconnects."""


# --- Connecting -----------------------------------------------------------------------------------


def connect_from_code(guide: Member, code: str, *, now: datetime | None = None) -> TourCalendarConnection:
    """Finish the Connect Google Calendar flow: store the tokens and calendars, and queue the guide's tours.

    A reconnect updates the guide's row in place: it is connected again, a calendar they picked is
    kept while they still own it, and every upcoming tour of theirs is queued so the job writes it.
    Connecting another Google account forgets where the old account's events were (they are out of
    reach now).

    Raises:
        google.GoogleMissingScopeError: a permission was unticked on the consent screen.
        google.GoogleError: Google refused the code or did not answer.
    """
    now = now or timezone.now()
    tokens = google.exchange_code(code)
    calendars = google.list_calendars(tokens.access_token)
    primary = next((calendar for calendar in calendars if calendar.primary), None)
    email = primary.id if primary is not None else ""
    with transaction.atomic():
        connection = TourCalendarConnection.objects.select_for_update().filter(guide=guide).first()
        if connection is None:
            connection = TourCalendarConnection(guide=guide)
        elif connection.google_email and connection.google_email != email:
            TourCalendarSync.objects.filter(connection=connection).update(connection=None, calendar_id="", event_id="")
        if connection.calendar_id != "primary" and connection.calendar_id not in {cal.id for cal in calendars}:
            connection.calendar_id = "primary"
        connection.google_email = email
        connection.refresh_token = tokens.refresh_token
        connection.access_token = tokens.access_token
        connection.access_token_expires_at = tokens.expires_at
        connection.calendars = [calendar.as_json() for calendar in calendars]
        connection.status = TourCalendarConnection.Status.CONNECTED
        connection.broken_at = None
        connection.broken_reason = ""
        connection.busy_spans = []
        connection.busy_checked_at = None
        connection.busy_attempted_at = None
        connection.connected_at = now
        connection.save()
        queue_upcoming(guide, now=now)
    return connection


def choose_calendar(connection: TourCalendarConnection, calendar_id: str, *, now: datetime | None = None) -> None:
    """Write tours to ``calendar_id`` from now on (one the picker offered); every upcoming tour moves there."""
    connection.calendar_id = calendar_id
    connection.busy_spans = []
    connection.busy_checked_at = None
    connection.busy_attempted_at = None
    connection.save(update_fields=["calendar_id", "busy_spans", "busy_checked_at", "busy_attempted_at", "updated_at"])
    queue_upcoming(connection.guide, now=now or timezone.now())


def disconnect(connection: TourCalendarConnection) -> bool:
    """Revoke the token at Google and delete the connection; tour events already written stay put.

    Returns:
        Whether Google confirmed the revoke. Either way the tokens are gone from our database.
    """
    token = connection.refresh_token or connection.access_token
    revoked = True
    if token:
        try:
            google.revoke(token, timeout=ON_DEMAND_CALL_TIMEOUT)
        except google.GoogleError as exc:
            logger.warning("Could not revoke %s's Google token: %s", connection.guide, exc)
            revoked = False
    # Out of reach once the token is gone: forget where those events were. A row still due (a
    # reassignment waiting to be written on the new guide's calendar) stays due.
    TourCalendarSync.objects.filter(connection=connection).update(connection=None, calendar_id="", event_id="")
    connection.delete()
    return revoked


def queue_upcoming(guide: Member, *, now: datetime) -> int:
    """Mark every booked tour of the guide's that has not started due for writing; returns how many."""
    bookings = list(
        TourBooking.objects.filter(guide=guide, status=TourBooking.Status.BOOKED, starts_at__gt=now).values_list(
            "pk", flat=True
        )
    )
    for booking_id in bookings:
        TourCalendarSync.objects.update_or_create(booking_id=booking_id, defaults=_DUE)
    return len(bookings)


def queue_booking_sync(sender: Any, *, booking: TourBooking, **kwargs: Any) -> None:
    """``tour_booking_changed`` receiver: mark the booking due when Google has anything to learn.

    That is when its guide has a calendar connected, or an event of it is on some calendar already
    (a cancel, or a move away from a connected guide). Never calls Google.
    """
    has_event = TourCalendarSync.objects.filter(booking=booking).exclude(event_id="").exists()
    if not has_event and not TourCalendarConnection.objects.filter(guide_id=booking.guide_id).exists():
        return
    TourCalendarSync.objects.update_or_create(booking=booking, defaults=_DUE)


# --- Tokens and broken connections ------------------------------------------------------------------


def access_token(connection: TourCalendarConnection, *, timeout: float) -> str:
    """A working access token: the stored one while it lasts, else a fresh one from the refresh token.

    Raises:
        google.GoogleAuthError: no refresh token (cleared, or unreadable after a key change) or Google refused it.
    """
    expires_at = connection.access_token_expires_at
    if connection.access_token and expires_at is not None and expires_at > timezone.now() + TOKEN_MARGIN:
        return connection.access_token
    if not connection.refresh_token:
        raise google.GoogleAuthError("No refresh token is stored.")
    tokens = google.refresh(connection.refresh_token, timeout=timeout)
    connection.access_token = tokens.access_token
    connection.access_token_expires_at = tokens.expires_at
    fields = ["access_token", "access_token_expires_at", "updated_at"]
    if tokens.refresh_token:
        connection.refresh_token = tokens.refresh_token
        fields.append("refresh_token")
    connection.save(update_fields=fields)
    return connection.access_token


def mark_broken(connection: TourCalendarConnection, reason: str) -> bool:
    """Turn the connection broken and tell the guide, once: a second call changes nothing.

    Returns:
        Whether this call broke it (and so sent the email and bell).
    """
    now = timezone.now()
    changed = TourCalendarConnection.objects.filter(
        pk=connection.pk, status=TourCalendarConnection.Status.CONNECTED
    ).update(
        status=TourCalendarConnection.Status.BROKEN,
        broken_at=now,
        broken_reason=reason[:300],
        refresh_token="",
        access_token="",
        access_token_expires_at=None,
        updated_at=now,
    )
    connection.refresh_from_db()
    if changed:
        logger.info("%s's Google Calendar connection broke: %s", connection.guide, reason)
        tell_guide_broken(connection)
    return bool(changed)


def hours_page_url() -> str:
    """The My Hours tab, absolute, for the email."""
    return f"{settings.MEMBER_BASE_URL.rstrip('/')}{reverse('tours:hours')}"


def tell_guide_broken(connection: TourCalendarConnection) -> None:
    """Email and bell the guide that their calendar needs reconnecting (once per breakage)."""
    from core.events.senders import emit_with_email_shell

    account = connection.google_email or "your Google account"
    broken_at = connection.broken_at or timezone.now()
    emit_with_email_shell(
        BROKEN_EVENT,
        target=connection,
        context={"guide": connection.guide},
        subject="Reconnect your Google Calendar for tours",
        text_template="tours/emails/guide_calendar_broken.txt",
        html_template="tours/emails/guide_calendar_broken.html",
        template_context=broken_context(connection),
        in_app_title="Reconnect your Google Calendar",
        in_app_body=(
            f"Google stopped letting us use {account}. Your tour hours are offered as if no calendar "
            "were connected until you reconnect it on My Hours."
        ),
        url=reverse("tours:hours"),
        period=f"tour_calendar:{connection.pk}:broken:{broken_at:%Y%m%dT%H%M%S}",
    )


def broken_context(connection: TourCalendarConnection) -> dict[str, Any]:
    """What the reconnect email renders against."""
    return {"google_email": connection.google_email, "hours_url": hours_page_url()}


# --- Writing events ---------------------------------------------------------------------------------


def event_id_for(booking: TourBooking) -> str:
    """Our id for the booking's Google event: the same for its whole life, on every calendar.

    Google wants 5 to 1024 characters from base32hex (0 to 9, a to v). The site's own suffix keeps a
    development copy's booking 12 from ever matching production's on a guide's calendar.
    """
    digest = hashlib.sha256(settings.MEMBER_BASE_URL.encode()).digest()
    site = base64.b32hexencode(digest).decode().lower()[:8]
    return f"pltour{booking.pk}s{site}"


def event_body(booking: TourBooking) -> dict[str, Any]:
    """The Google event for a booking: who, how many, how to reach them, marked as ours."""
    from tours.notify import tours_page_url

    lines = [f"{booking.visitor_name}, {booking.party_label}", f"Email: {booking.visitor_email}"]
    if booking.visitor_phone:
        lines.append(f"Phone: {booking.visitor_phone}")
    lines += ["", f"Tours page: {tours_page_url()}"]
    return {
        "summary": f"Past Lives tour: {booking.visitor_name} ({booking.party_size})",
        "description": "\n".join(lines),
        "location": TOUR_ADDRESS,
        "start": {"dateTime": booking.starts_at.astimezone(UTC).isoformat()},
        "end": {"dateTime": booking.ends_at.astimezone(UTC).isoformat()},
        "status": "confirmed",
        "transparency": "opaque",
        "extendedProperties": {"private": {EVENT_MARKER: "1", "plfogBooking": str(booking.pk)}},
    }


def _write_event(connection: TourCalendarConnection, row: TourCalendarSync, booking: TourBooking) -> str:
    """Create or update the booking's event on the connection's chosen calendar; returns its id."""
    token = access_token(connection, timeout=google.DEFAULT_TIMEOUT)
    body = event_body(booking)
    if row.event_id:
        try:
            google.update_event(token, connection.calendar_id, row.event_id, body)
        except google.GoogleNotFoundError:
            pass
        else:
            return row.event_id
    event_id = event_id_for(booking)
    try:
        return google.insert_event(token, connection.calendar_id, {"id": event_id, **body})
    except google.GoogleConflictError:
        # Ours already (a lost answer, a reconnect, a tour moving back to this calendar): update it in place.
        google.update_event(token, connection.calendar_id, event_id, body)
        return event_id


def _guide_connection(booking: TourBooking) -> TourCalendarConnection | None:
    if booking.guide_id is None:
        return None
    return TourCalendarConnection.objects.filter(guide_id=booking.guide_id).first()


def sync_booking(row: TourCalendarSync, *, now: datetime) -> None:
    """Bring one booking's Google event to what the booking says now, then mark the row done.

    Raises:
        _Waiting: a connection it needs is broken (marked so here when Google just refused it).
        google.GoogleError: Google failed; the caller records it and tries again later.
    """
    booking = row.booking
    connection = _guide_connection(booking)
    if connection is not None and connection.is_broken:
        raise _Waiting
    want = booking.status != TourBooking.Status.CANCELLED and connection is not None
    in_place = (
        connection is not None and row.connection_id == connection.pk and row.calendar_id == connection.calendar_id
    )
    if row.event_id and not (want and in_place):
        holder = row.connection
        if holder is not None:
            if holder.is_broken:
                raise _Waiting
            try:
                google.delete_event(access_token(holder, timeout=google.DEFAULT_TIMEOUT), row.calendar_id, row.event_id)
            except google.GoogleAuthError as exc:
                mark_broken(holder, str(exc))
                raise _Waiting from exc
        row.connection, row.calendar_id, row.event_id = None, "", ""
        row.save(update_fields=["connection", "calendar_id", "event_id"])
    if want and connection is not None:
        try:
            row.event_id = _write_event(connection, row, booking)
        except google.GoogleAuthError as exc:
            mark_broken(connection, str(exc))
            raise _Waiting from exc
        row.connection, row.calendar_id = connection, connection.calendar_id
    row.due, row.attempts, row.next_attempt_at, row.last_error, row.synced_at = False, 0, None, "", now
    row.save()


@dataclass
class SyncCounts:
    """What one pass over the due rows did."""

    done: int = 0
    waiting: int = 0
    failed: int = 0
    given_up: int = 0


def _record_failure(row: TourCalendarSync, exc: Exception, now: datetime) -> None:
    row.attempts += 1
    row.last_error = (str(exc) or type(exc).__name__)[:300]
    row.next_attempt_at = now + min(RETRY_BASE * 2 ** (row.attempts - 1), RETRY_CAP)
    row.save(update_fields=["attempts", "last_error", "next_attempt_at"])


def sync_due_bookings(now: datetime) -> SyncCounts:
    """Write every due row whose wait is over, soonest tour first, at most :data:`SYNC_BATCH`."""
    counts = SyncCounts()
    rows = (
        TourCalendarSync.objects.filter(due=True)
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .select_related("booking", "connection")
        .order_by("booking__starts_at", "pk")[:SYNC_BATCH]
    )
    for row in rows:
        if row.booking.ends_at < now - GIVE_UP_AFTER_END:
            row.due = False
            row.last_error = row.last_error or "The tour was over before Google was updated."
            row.save(update_fields=["due", "last_error"])
            counts.given_up += 1
            continue
        try:
            sync_booking(row, now=now)
        except _Waiting:
            counts.waiting += 1
        except google.GoogleError as exc:
            logger.warning("Google Calendar write for tour booking %s failed: %s", row.booking_id, exc)
            _record_failure(row, exc, now)
            counts.failed += 1
        except Exception as exc:
            # One bad row must not stop the rest; the stuck check makes a repeat failure loud.
            logger.exception("Google Calendar write for tour booking %s failed", row.booking_id)
            _record_failure(row, exc, now)
            counts.failed += 1
        else:
            counts.done += 1
    return counts


# --- Reading busy times -----------------------------------------------------------------------------


def busy_window(now: datetime) -> tuple[datetime, datetime]:
    """From now to the end of the last day visitors may book (the horizon, Tour Settings)."""
    last_day = timezone.localdate(now) + timedelta(days=FacilityTourSettings.load().booking_horizon_days)
    return now, timezone.make_aware(datetime.combine(last_day + timedelta(days=1), time.min))


def _instant(value: Any) -> datetime | None:
    """An event's start or end in UTC: a timed one as given, an all day one from local midnight."""
    if not isinstance(value, dict):
        return None
    if value.get("dateTime"):
        moment = datetime.fromisoformat(str(value["dateTime"]))
        if timezone.is_naive(moment):
            moment = timezone.make_aware(moment)
        return moment.astimezone(UTC)
    if value.get("date"):
        day = date.fromisoformat(str(value["date"]))
        return timezone.make_aware(datetime.combine(day, time.min)).astimezone(UTC)
    return None


def busy_span(event: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """The time an event blocks, or None when it blocks none.

    None for a cancelled event, one shown as free (``transparent``), a working location or
    birthday, one the guide declined, and our own tour events, which must not hide the other seats
    of the tour they stand for.
    """
    if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
        return None
    if event.get("eventType") in ("workingLocation", "birthday"):
        return None
    private = (event.get("extendedProperties") or {}).get("private") or {}
    if private.get(EVENT_MARKER):
        return None
    for attendee in event.get("attendees") or []:
        if attendee.get("self") and attendee.get("responseStatus") == "declined":
            return None
    start, end = _instant(event.get("start")), _instant(event.get("end"))
    if start is None or end is None or end <= start:
        return None
    return start, end


def merge_spans(spans: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Spans sorted, with overlapping and touching ones joined."""
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def overlaps(start: datetime, end: datetime, spans: list[tuple[datetime, datetime]]) -> bool:
    """Whether ``start`` to ``end`` shares any time with one of ``spans`` (touching ends do not count)."""
    return any(span_start < end and start < span_end for span_start, span_end in spans)


def _refresh_calendar_list(connection: TourCalendarConnection, token: str, *, timeout: float) -> None:
    calendars = google.list_calendars(token, timeout=timeout)
    connection.calendars = [calendar.as_json() for calendar in calendars]
    primary = next((calendar for calendar in calendars if calendar.primary), None)
    if primary is not None:
        connection.google_email = primary.id
    connection.save(update_fields=["calendars", "google_email", "updated_at"])


def read_busy(
    connection: TourCalendarConnection, *, now: datetime, timeout: float, with_calendars: bool
) -> list[tuple[datetime, datetime]]:
    """Read the guide's busy times over the horizon from their main and chosen calendars, and cache them.

    ``with_calendars`` also refreshes the picker's list of calendars (the job does; a page load does not).

    Raises:
        google.GoogleError: as the calls raise it.
    """
    token = access_token(connection, timeout=timeout)
    if with_calendars:
        _refresh_calendar_list(connection, token, timeout=timeout)
    start, end = busy_window(now)
    spans: list[tuple[datetime, datetime]] = []
    for calendar_id in dict.fromkeys(["primary", connection.calendar_id]):
        for event in google.list_events(token, calendar_id, start, end, timeout=timeout):
            span = busy_span(event)
            if span is not None:
                spans.append(span)
    merged = merge_spans(spans)
    connection.busy_spans = [[span_start.isoformat(), span_end.isoformat()] for span_start, span_end in merged]
    connection.busy_checked_at = now
    connection.save(update_fields=["busy_spans", "busy_checked_at", "updated_at"])
    return merged


def refresh_busy(
    connection: TourCalendarConnection,
    *,
    now: datetime,
    timeout: float = google.DEFAULT_TIMEOUT,
    with_calendars: bool = True,
) -> bool:
    """:func:`read_busy`, keeping the cache as it was when Google fails, and breaking the connection when it refuses.

    Returns:
        Whether the busy times were read.
    """
    connection.busy_attempted_at = now
    TourCalendarConnection.objects.filter(pk=connection.pk).update(busy_attempted_at=now)
    try:
        read_busy(connection, now=now, timeout=timeout, with_calendars=with_calendars)
    except google.GoogleAuthError as exc:
        mark_broken(connection, str(exc))
        return False
    except google.GoogleError as exc:
        logger.warning("Could not read %s's Google Calendar busy times: %s", connection.guide, exc)
        return False
    return True


def refresh_stale_busy(*, now: datetime | None = None) -> None:
    """On a booking page or kiosk load: read again the busy times older than :data:`BUSY_FRESH`.

    Only for connected guides who have tour hours, skipping one whose read failed in the last
    :data:`ON_DEMAND_RETRY`, and stopping after :data:`ON_DEMAND_BUDGET_SECONDS`. A Google failure
    keeps the cached times (or none); this never raises a Google error to the page.
    """
    if not google.is_configured():
        return
    now = now or timezone.now()
    stale = (
        TourCalendarConnection.objects.healthy()
        .filter(Q(busy_checked_at__isnull=True) | Q(busy_checked_at__lt=now - BUSY_FRESH))
        .filter(Q(busy_attempted_at__isnull=True) | Q(busy_attempted_at__lt=now - ON_DEMAND_RETRY))
        .filter(guide__in=TourHours.objects.values("guide_id"))
        .select_related("guide")
    )
    deadline = clock.monotonic() + ON_DEMAND_BUDGET_SECONDS
    for connection in stale:
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            break
        refresh_busy(connection, now=now, timeout=min(ON_DEMAND_CALL_TIMEOUT, remaining), with_calendars=False)


def tell_guide_busy(booking: TourBooking) -> None:
    """Bell the guide that their calendar shows them busy during one of their booked tours (once per start)."""
    from core.events.emit import emit
    from tours.notify import short_when

    emit(
        BUSY_EVENT,
        target=booking,
        context={"guide": booking.guide},
        title=BUSY_TITLE,
        body=(
            f"{booking.visitor_name}, {booking.party_label}, {short_when(booking.starts_at)}. The tour is still "
            "booked: move it or cancel it on the Tours page if you cannot give it."
        ),
        url=reverse("tours:upcoming"),
        period=f"tour:{booking.pk}:calendar_busy:{booking.starts_at.astimezone(UTC):%Y%m%dT%H%MZ}",
    )


def alert_busy_conflicts(now: datetime) -> int:
    """Bell each guide whose cached busy times cover one of their booked tours that has not started.

    The tour stays booked. The bell's period is the booking and its start, so it rings once per
    booking, and again only after the tour moves to another time that is also busy.

    Returns:
        How many booked tours overlap a busy time.
    """
    found = 0
    connections = TourCalendarConnection.objects.healthy().filter(busy_checked_at__isnull=False)
    for connection in connections:
        spans = connection.busy_intervals()
        if not spans:
            continue
        bookings = TourBooking.objects.filter(
            guide_id=connection.guide_id,
            status=TourBooking.Status.BOOKED,
            starts_at__gt=now,
            starts_at__lt=spans[-1][1],
        ).select_related("guide")
        for booking in bookings:
            if overlaps(booking.starts_at, booking.ends_at, spans):
                tell_guide_busy(booking)
                found += 1
    return found


# --- The job ----------------------------------------------------------------------------------------


@dataclass
class SyncReport:
    """One run of ``sync_tour_calendars``."""

    read: int = 0
    read_failed: int = 0
    conflicts: int = 0
    writes: SyncCounts = field(default_factory=SyncCounts)
    stuck: list[str] = field(default_factory=list)


def stuck_problems(now: datetime) -> list[str]:
    """What has been failing long enough that a person should look: the job fails on these."""
    problems: list[str] = []
    failing = TourCalendarSync.objects.filter(due=True, attempts__gte=STUCK_ATTEMPTS).count()
    if failing:
        problems.append(f"{failing} tour event write(s) failed {STUCK_ATTEMPTS} or more times in a row")
    old = now - STALE_ALERT
    stale = (
        TourCalendarConnection.objects.healthy()
        .filter(connected_at__lt=old)
        .filter(Q(busy_checked_at__isnull=True) | Q(busy_checked_at__lt=old))
        .count()
    )
    if stale:
        problems.append(f"{stale} connected calendar(s) have not been read for over an hour")
    return problems


def run(now: datetime | None = None) -> SyncReport:
    """The ``sync_tour_calendars`` job: read busy times, bell any clash with a booked tour, write due events."""
    now = now or timezone.now()
    report = SyncReport()
    to_read = TourCalendarConnection.objects.healthy().filter(
        Q(busy_checked_at__isnull=True) | Q(busy_checked_at__lt=now - JOB_REFRESH_AFTER)
    )
    for connection in to_read.select_related("guide"):
        if refresh_busy(connection, now=now):
            report.read += 1
        else:
            report.read_failed += 1
    report.conflicts = alert_busy_conflicts(now)
    report.writes = sync_due_bookings(now)
    report.stuck = stuck_problems(now)
    return report
