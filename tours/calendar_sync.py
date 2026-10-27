"""A Tour Guide's Google Calendar, both ways (#951): tours written to it, busy times read from it.

Nothing here runs inside a visitor's request: every Google call is made by the
``sync_tour_calendars`` job (every tick, :func:`run`), the Connect flow and Disconnect.

**Writes.** :func:`queue_booking_sync` hangs off :data:`tours.signals.tour_booking_changed` (sent after
commit) and only marks the booking's :class:`~tours.models.TourCalendarSync` row due, raising its
``generation``. :func:`sync_due_bookings` claims each due row (``claimed_until``, so two overlapping
runs never write one row), reads the booking fresh and brings Google to it (:func:`sync_booking`):
an event on the guide's chosen calendar with the current times, title "Past Lives tour: <visitor>
(<party size>)" and the visitor's contact in the description; none for a cancelled tour; and an
event left on another guide's calendar (a reassignment) or on a calendar the guide stopped using is
deleted first. Due is cleared only while the row still holds the generation read, so a move that
commits while the job talks to Google is written on the next run, never lost. Event ids are ours
(:func:`event_id_for`), so a create that reached Google but not our database, or a reconnect,
updates the event in place. A failure leaves the row due with a growing wait (:data:`RETRY_BASE`
doubling up to :data:`RETRY_CAP`); the booking never waits on Google.

**Reads.** Every tick the job reads each connected guide's busy times over the booking horizon
(:func:`read_busy`, at most :data:`BUSY_DEADLINE_SECONDS` a guide): every event on their main
calendar and their chosen one that blocks time, except our own tour events (they carry
:data:`EVENT_MARKER`), so a guide's other seats at a tour's time stay offered. They are cached on
the connection, at most about 15 minutes old, and :mod:`tours.availability` reads only the cache.
A tour already booked is never taken away: the guide gets a bell instead
(:func:`alert_busy_conflicts`), once per booking and start. A chosen calendar that is gone (deleted,
or no longer theirs) falls back to the main one, with one bell (:func:`chosen_calendar_gone`).

**Broken.** A 401 gets one fresh access token first (:func:`_with_token`). When Google still refuses
(revoked, expired, a permission unticked), the connection turns broken (:func:`mark_broken`): its
tokens are cleared, the guide gets one email and bell, their busy times stop counting (their hours
offer as if unconnected) and nothing is written to it until they reconnect from My Hours.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import math
import time as clock
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from tours import google
from tours.models import (
    TOUR_ADDRESS,
    FacilityTourSettings,
    TourBooking,
    TourCalendarCleanup,
    TourCalendarConnection,
    TourCalendarSync,
)

if TYPE_CHECKING:
    from membership.models import Member

logger = logging.getLogger(__name__)
T = TypeVar("T")

#: The most the job spends reading one guide's busy times, and writing one booking's event.
BUSY_DEADLINE_SECONDS = 30.0
WRITE_DEADLINE_SECONDS = 30.0
#: The whole run's budget: a scheduled tick (inline, ahead of the other jobs), and Run now (inside a
#: web request under gunicorn's 30 seconds). What is left waits for the next tick.
JOB_BUDGET_SECONDS = 120.0
MANUAL_BUDGET_SECONDS = 20.0
#: Consecutive timeouts or server errors after which the run stops calling Google (the breaker).
BREAKER_FAILURES = 3
#: Disconnect's patience for Google's revoke, inside the guide's request.
REVOKE_TIMEOUT = 5.0
#: How long a run holds a row it is writing; a run that died lets go after this.
CLAIM_FOR = timedelta(minutes=10)
#: A write that met a delete of its own event in flight tries again after this (the next tick).
IN_FLIGHT_RETRY = timedelta(minutes=1)
#: A row waiting on a broken calendar is looked at again after this (a reconnect queues it at once).
WAIT_RECHECK = timedelta(hours=1)
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
GONE_EVENT = "tour.calendar_gone"
UNREAD_EVENT = "tour.calendar_unread"
UNREAD_TITLE = "We could not read your Google Calendar"
BUSY_TITLE = "Your calendar now shows you busy during a booked tour"


class _Waiting(Exception):
    """The row needs a connection that is broken: it stays due until the guide reconnects."""


class _DeadlineSpent(google.GoogleError):
    """One guide's read or one booking's write ran out of its own time: a failure for it, never for the
    run's breaker (a calendar that is merely big is not Google failing)."""


class _DeleteInFlight(Exception):
    """A run is deleting the very event this row is about to write: write it on a later run instead."""


class _Paused(Exception):
    """The run's budget is spent or its breaker is open: what is left waits for the next tick, uncounted."""


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
            TourCalendarCleanup.objects.filter(connection=connection).delete()
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
        connection.connected_at = now
        connection.save()
        queue_upcoming(guide, now=now)
    return connection


def choose_calendar(connection: TourCalendarConnection, calendar_id: str, *, now: datetime | None = None) -> None:
    """Write tours to ``calendar_id`` from now on (one the picker offered); every upcoming tour moves there."""
    connection.calendar_id = calendar_id
    connection.busy_spans = []
    connection.busy_checked_at = None
    connection.save(update_fields=["calendar_id", "busy_spans", "busy_checked_at", "updated_at"])
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
            google.revoke(token, timeout=REVOKE_TIMEOUT)
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
        mark_due(booking_id)
    return len(bookings)


def mark_due(booking_id: int) -> None:
    """Queue the booking for the job: due, no wait, and a new ``generation`` so a run already writing
    it keeps it due when it finishes."""
    queued = TourCalendarSync.objects.filter(booking_id=booking_id).update(
        due=True, attempts=0, next_attempt_at=None, last_error="", generation=F("generation") + 1
    )
    if not queued:
        TourCalendarSync.objects.get_or_create(booking_id=booking_id)


def queue_cleanup(connection: TourCalendarConnection | None, calendar_id: str, event_id: str) -> None:
    """Queue the delete of an event no booking points at any more (:class:`~tours.models.TourCalendarCleanup`)."""
    if connection is None or not event_id:
        return
    TourCalendarCleanup.objects.get_or_create(connection=connection, calendar_id=calendar_id, event_id=event_id)


def forget_deleted_booking(sender: Any, *, instance: TourBooking, **kwargs: Any) -> None:
    """``pre_delete`` receiver for a booking: queue its Google event's delete before the row goes.

    Normal flows cancel a booking and never delete one; this covers the Django admin and a shell.
    """
    row = TourCalendarSync.objects.filter(booking=instance).select_related("connection").first()
    if row is not None:
        queue_cleanup(row.connection, row.calendar_id, row.event_id)


def queue_booking_sync(sender: Any, *, booking: TourBooking, **kwargs: Any) -> None:
    """``tour_booking_changed`` receiver: mark the booking due when Google has anything to learn.

    That is when its guide has a calendar connected, or an event of it is on some calendar already
    (a cancel, or a move away from a connected guide). Never calls Google.
    """
    has_event = TourCalendarSync.objects.filter(booking=booking).exclude(event_id="").exists()
    if not has_event and not TourCalendarConnection.objects.filter(guide_id=booking.guide_id).exists():
        return
    mark_due(booking.pk)


# --- Tokens and broken connections ------------------------------------------------------------------


def access_token(connection: TourCalendarConnection, *, timeout: float, fresh: bool = False) -> str:
    """A working access token: the stored one while it lasts (unless ``fresh``), else a new one from the
    refresh token.

    Raises:
        google.GoogleAuthError: no refresh token (cleared, or unreadable after a key change) or Google refused it.
    """
    expires_at = connection.access_token_expires_at
    lasting = expires_at is not None and expires_at > timezone.now() + TOKEN_MARGIN
    if connection.access_token and lasting and not fresh:
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


class RunGuard:
    """One run's limits: a total budget, and a breaker that opens after :data:`BREAKER_FAILURES`
    timeouts or server errors in a row (an auth refusal, a 404 or a 409 is an answer and resets it).
    """

    def __init__(self, seconds: float = math.inf) -> None:
        self.ends = clock.monotonic() + seconds
        self.failures = 0

    @property
    def why_stopped(self) -> str:
        """Why the run stops calling Google, or "" while it may go on."""
        if self.failures >= BREAKER_FAILURES:
            return f"Google failed {self.failures} times in a row"
        if clock.monotonic() >= self.ends:
            return "the run's time was spent"
        return ""

    def answered(self) -> None:
        self.failures = 0

    def failed(self) -> None:
        self.failures += 1


class Deadline:
    """A time limit for one guide's read or one booking's write, inside the run's :class:`RunGuard`.

    Each call, and each page of a list, gets what is left, at most Google's own timeout.
    """

    def __init__(self, seconds: float, guard: RunGuard | None = None) -> None:
        self.ends = clock.monotonic() + seconds
        self.guard = guard or RunGuard()

    def timeout(self) -> float:
        """The next request's timeout.

        Raises:
            _Paused: the run's budget is spent or its breaker is open.
            google.GoogleError: this deadline is spent.
        """
        if self.guard.why_stopped:
            raise _Paused(self.guard.why_stopped)
        left = min(self.ends, self.guard.ends) - clock.monotonic()
        if left <= 0:
            raise _DeadlineSpent("Google took too long to answer.")
        return min(google.DEFAULT_TIMEOUT, left)


def _with_token(
    connection: TourCalendarConnection, call: Callable[[str, Callable[[], float]], T], deadline: Deadline
) -> T:
    """``call(token, timeout)`` with the connection's token; on a 401, once more with a fresh token.

    ``timeout`` is the deadline's own :meth:`Deadline.timeout`, asked before every request and page.
    Only a refresh Google refuses, or a 401 on a token it has just issued, reaches the caller as a
    :class:`google.GoogleAuthError`. A timeout or server error counts towards the run's breaker.
    """
    guard = deadline.guard
    try:
        try:
            token = access_token(connection, timeout=deadline.timeout())
            result = call(token, deadline.timeout)
        except google.GoogleUnauthorizedError:
            token = access_token(connection, timeout=deadline.timeout(), fresh=True)
            result = call(token, deadline.timeout)
    except (google.GoogleAuthError, _DeadlineSpent):
        raise
    except (google.GoogleNotFoundError, google.GoogleConflictError):
        guard.answered()
        raise
    except google.GoogleError:
        guard.failed()
        raise
    guard.answered()
    return result


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


def chosen_calendar_gone(connection: TourCalendarConnection, lost: str = "") -> None:
    """The guide's chosen calendar is gone (deleted, or no longer theirs): write to their main one.

    Every upcoming tour of theirs is queued again so it lands there, and the guide gets one bell per
    calendar lost (``lost`` is its name, when the list that named it was just read again). The main
    calendar itself never goes this way.
    """
    lost = lost or connection.calendar_label
    lost_id = connection.calendar_id
    connection.calendar_id = "primary"
    connection.save(update_fields=["calendar_id", "updated_at"])
    queue_upcoming(connection.guide, now=timezone.now())
    from core.events.emit import emit

    emit(
        GONE_EVENT,
        target=connection,
        context={"guide": connection.guide},
        title="Your tours calendar is gone",
        body=(
            f"Google no longer lets us write to {lost}, so your tours go to your main calendar now. "
            "Pick another calendar on My Hours if you like."
        ),
        url=reverse("tours:hours"),
        period=f"tour_calendar:{connection.pk}:gone:{lost_id}",
    )


def _write_event(
    connection: TourCalendarConnection, event_id: str, booking: TourBooking, deadline: Deadline, held: list[int]
) -> str:
    """Create or update the booking's event on the connection's chosen calendar; returns its id.

    A chosen calendar that answers "not found" to a create is gone: the main calendar takes over
    (:func:`chosen_calendar_gone`), a queued delete of the event there is claimed like the first
    target's (:func:`_hold_queued_delete`), and the event is written there.
    """
    body = event_body(booking)
    if event_id:
        try:
            _with_token(
                connection,
                lambda token, timeout: google.update_event(
                    token, connection.calendar_id, event_id, body, timeout=timeout()
                ),
                deadline,
            )
        except google.GoogleNotFoundError:
            pass
        else:
            return event_id
    ours = event_id_for(booking)
    try:
        return _insert(connection, ours, body, deadline)
    except google.GoogleNotFoundError:
        if connection.calendar_id == "primary":
            raise
        chosen_calendar_gone(connection)
        _hold_queued_delete(connection, ours, held)
        return _insert(connection, ours, body, deadline)


def _insert(connection: TourCalendarConnection, event_id: str, body: dict[str, Any], deadline: Deadline) -> str:
    """Insert with our id; one already there (a lost answer, a reconnect, a tour moving back) is updated."""
    calendar_id = connection.calendar_id
    try:
        return _with_token(
            connection,
            lambda token, timeout: google.insert_event(token, calendar_id, {"id": event_id, **body}, timeout=timeout()),
            deadline,
        )
    except google.GoogleConflictError:
        _with_token(
            connection,
            lambda token, timeout: google.update_event(token, calendar_id, event_id, body, timeout=timeout()),
            deadline,
        )
        return event_id


def _guide_connection(booking: TourBooking) -> TourCalendarConnection | None:
    if booking.guide_id is None:
        return None
    return TourCalendarConnection.objects.filter(guide_id=booking.guide_id).first()


def _place(
    row: TourCalendarSync,
    connection: TourCalendarConnection | None,
    calendar_id: str,
    event_id: str,
    *,
    held: Sequence[int] = (),
) -> None:
    """Record where the event is now, touching nothing else on the row (due and generation stay as queued).

    A row gone meanwhile (its booking deleted while the run was writing) leaves the event just
    written with no booking: its delete is queued. ``held`` are the queued deletes this write claimed.
    """
    row.connection, row.calendar_id, row.event_id = connection, calendar_id, event_id
    placed = TourCalendarSync.objects.filter(pk=row.pk).update(
        connection=connection, calendar_id=calendar_id, event_id=event_id
    )
    if not placed:
        queue_cleanup(connection, calendar_id, event_id)
    elif connection is not None and event_id:
        # A tour back on a calendar it left (a reassignment there and back): its old delete must not run.
        # One a run is deleting right now may still land after this write, so the booking is queued
        # again and the next run puts the event back.
        stale = TourCalendarCleanup.objects.filter(connection=connection, calendar_id=calendar_id, event_id=event_id)
        if stale.exclude(pk__in=held).filter(claimed_until__gte=timezone.now()).exists():
            mark_due(row.booking_id)
        unclaimed = Q(claimed_until__isnull=True) | Q(claimed_until__lt=timezone.now())
        stale.filter(unclaimed | Q(pk__in=held)).delete()


def _remove_old_event(row: TourCalendarSync, connection: TourCalendarConnection | None, deadline: Deadline) -> None:
    """Delete the event from the calendar holding it, or forget it when that calendar is out of reach.

    A broken holder that is not the booking's guide (a reassignment away from them) cannot be reached
    now: its delete is queued for when they reconnect (:func:`queue_cleanup`) and the new guide's
    calendar is still written. The guide's own broken calendar comes back with a reconnect, so the
    row waits for it.

    Raises:
        _Waiting: the event is on the guide's own broken calendar.
    """
    holder = row.connection
    own = holder is not None and connection is not None and holder.pk == connection.pk
    if holder is not None and holder.is_broken and own:
        raise _Waiting
    calendar_id, event_id = row.calendar_id, row.event_id
    if holder is not None and holder.is_broken:
        queue_cleanup(holder, calendar_id, event_id)
    elif holder is not None:
        try:
            _with_token(
                holder,
                lambda token, timeout: google.delete_event(token, calendar_id, event_id, timeout=timeout()),
                deadline,
            )
        except google.GoogleAuthError as exc:
            mark_broken(holder, str(exc))
            if own:
                raise _Waiting from exc
            queue_cleanup(holder, calendar_id, event_id)
    _place(row, None, "", "")


def _hold_queued_delete(connection: TourCalendarConnection, event_id: str, held: list[int]) -> None:
    """Before writing an event to the connection's chosen calendar, claim a queued delete of it for this write.

    The claim (not a delete) keeps the queued delete alive should the write fail: a tour cancelled
    before the retry still gets its old event removed. A successful write drops it (:func:`_place`),
    and the caller releases the claim whatever happened. ``held`` collects the claimed rows.

    Raises:
        _DeleteInFlight: another run is running that delete right now; nothing was written.
    """
    pending = TourCalendarCleanup.objects.filter(
        connection=connection, calendar_id=connection.calendar_id, event_id=event_id
    ).first()
    if pending is None:
        return
    if not _claim_cleanup(pending.pk):
        raise _DeleteInFlight
    held.append(pending.pk)


def sync_booking(row: TourCalendarSync, *, now: datetime, guard: RunGuard | None = None) -> None:
    """Bring one booking's Google event to what the booking says now (``row.booking`` read fresh).

    Leaves ``due`` alone: :func:`sync_due_bookings` clears it only if nothing was queued meanwhile.

    Raises:
        _Waiting: the guide's own calendar is broken (marked so here when Google just refused it).
        _DeleteInFlight: a run is deleting this event right now; nothing was written.
        _Paused: the run's budget is spent or its breaker is open.
        google.GoogleError: Google failed; the caller records it and tries again later.
    """
    booking = row.booking
    connection = _guide_connection(booking)
    deadline = Deadline(WRITE_DEADLINE_SECONDS, guard)
    want = booking.status != TourBooking.Status.CANCELLED and connection is not None
    in_place = (
        connection is not None and row.connection_id == connection.pk and row.calendar_id == connection.calendar_id
    )
    if row.event_id and not (want and in_place):
        _remove_old_event(row, connection, deadline)
    if not want or connection is None:
        return
    if connection.is_broken:
        raise _Waiting
    held: list[int] = []
    try:
        _hold_queued_delete(connection, row.event_id or event_id_for(booking), held)
        try:
            event_id = _write_event(connection, row.event_id, booking, deadline, held)
        except google.GoogleAuthError as exc:
            mark_broken(connection, str(exc))
            raise _Waiting from exc
        _place(row, connection, connection.calendar_id, event_id, held=held)
    finally:
        # A failed write leaves the queued delete as it was, for a cancel or a move before the retry.
        TourCalendarCleanup.objects.filter(pk__in=held).update(claimed_until=None)


@dataclass
class SyncCounts:
    """What one pass over the due rows did."""

    done: int = 0
    waiting: int = 0
    failed: int = 0
    given_up: int = 0
    deferred: int = 0


def _settle(row: TourCalendarSync, generation: int, **fields: Any) -> bool:
    """Write ``fields`` only while nothing was queued since the run read ``generation``; whether it did."""
    return bool(TourCalendarSync.objects.filter(pk=row.pk, generation=generation).update(**fields))


def _record_failure(row: TourCalendarSync, generation: int, exc: Exception, now: datetime) -> None:
    attempts = row.attempts + 1
    _settle(
        row,
        generation,
        attempts=attempts,
        last_error=(str(exc) or type(exc).__name__)[:300],
        next_attempt_at=now + min(RETRY_BASE * 2 ** (attempts - 1), RETRY_CAP),
    )


def _claim(pk: int) -> TourCalendarSync | None:
    """Take the row for this run, read fresh with its booking, or None when another run holds it.

    On the real clock, not the run's start: a run that has gone on for a while still holds what it claims.
    """
    now = timezone.now()
    free = Q(claimed_until__isnull=True) | Q(claimed_until__lt=now)
    if not TourCalendarSync.objects.filter(free, pk=pk, due=True).update(claimed_until=now + CLAIM_FOR):
        return None
    # Deleted with its booking between the claim and this read: nothing to write.
    return TourCalendarSync.objects.select_related("booking", "connection").filter(pk=pk).first()


def _sync_one(row: TourCalendarSync, now: datetime, counts: SyncCounts, guard: RunGuard) -> None:
    """Write one claimed row and settle it.

    Raises:
        _Paused: the run stops here; the row stays due as it was, uncounted.
    """
    generation = row.generation
    if row.booking.ends_at < now - GIVE_UP_AFTER_END:
        logger.warning("Gave up writing tour booking %s to Google Calendar: %s", row.booking_id, row.last_error)
        _settle(row, generation, due=False, last_error=row.last_error or "The tour was over before Google was updated.")
        counts.given_up += 1
        return
    try:
        sync_booking(row, now=now, guard=guard)
    except _Paused:
        raise
    except _DeleteInFlight:
        _settle(row, generation, next_attempt_at=now + IN_FLIGHT_RETRY)
        counts.deferred += 1
    except _Waiting:
        _settle(
            row,
            generation,
            next_attempt_at=now + WAIT_RECHECK,
            last_error="Waiting for the guide to reconnect their Google Calendar.",
        )
        counts.waiting += 1
    except google.GoogleError as exc:
        logger.warning("Google Calendar write for tour booking %s failed: %s", row.booking_id, exc)
        _record_failure(row, generation, exc, now)
        counts.failed += 1
    except Exception as exc:
        # One bad row must not stop the rest; the stuck check makes a repeat failure loud.
        logger.exception("Google Calendar write for tour booking %s failed", row.booking_id)
        _record_failure(row, generation, exc, now)
        counts.failed += 1
    else:
        _settle(row, generation, due=False, attempts=0, next_attempt_at=None, last_error="", synced_at=now)
        counts.done += 1


def sync_due_bookings(now: datetime, guard: RunGuard | None = None) -> SyncCounts:
    """Write every due row whose wait is over, soonest tour first, at most :data:`SYNC_BATCH`.

    Rows waiting on a broken calendar carry a :data:`WAIT_RECHECK` wait, so they never fill the batch.
    Once ``guard`` stops the run, the rest are ``deferred``: still due, for the next tick.
    """
    guard = guard or RunGuard()
    counts = SyncCounts()
    pks = list(
        TourCalendarSync.objects.filter(due=True)
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .filter(Q(claimed_until__isnull=True) | Q(claimed_until__lt=timezone.now()))
        .order_by("booking__starts_at", "pk")
        .values_list("pk", flat=True)[:SYNC_BATCH]
    )
    for index, pk in enumerate(pks):
        if guard.why_stopped:
            counts.deferred += len(pks) - index
            break
        row = _claim(pk)
        if row is None:
            continue
        try:
            _sync_one(row, now, counts, guard)
        except _Paused:
            counts.deferred += len(pks) - index
            break
        finally:
            TourCalendarSync.objects.filter(pk=pk).update(claimed_until=None)
    return counts


@dataclass
class CleanupCounts:
    """What one pass over the queued deletes did."""

    done: int = 0
    failed: int = 0
    deferred: int = 0


def run_cleanups(now: datetime, guard: RunGuard | None = None) -> CleanupCounts:
    """Delete the queued events (:class:`~tours.models.TourCalendarCleanup`) whose calendar works now.

    One on a broken calendar waits, untried, for the guide to reconnect (or goes with a disconnect).
    One a booking points at again (a tour back on the calendar it left) is dropped, never run.
    A failure waits like a write's; a refusal breaks the connection, and the delete waits again.
    """
    guard = guard or RunGuard()
    counts = CleanupCounts()
    pending = list(
        TourCalendarCleanup.objects.filter(connection__status=TourCalendarConnection.Status.CONNECTED)
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .filter(Q(claimed_until__isnull=True) | Q(claimed_until__lt=timezone.now()))
        .select_related("connection")
        .order_by("pk")[:SYNC_BATCH]
    )
    for index, cleanup in enumerate(pending):
        if not _claim_cleanup(cleanup.pk):
            continue
        try:
            _run_cleanup(cleanup, now, counts, guard)
        except _Paused:
            counts.deferred += len(pending) - index
            break
        except Exception as exc:
            # One bad row must not stop the rest, nor the unread bells and stuck check after it.
            logger.exception("Deleting old tour event %s from Google Calendar failed", cleanup.event_id)
            _record_cleanup_failure(cleanup, exc, now)
            counts.failed += 1
        finally:
            TourCalendarCleanup.objects.filter(pk=cleanup.pk).update(claimed_until=None)
    return counts


def _record_cleanup_failure(cleanup: TourCalendarCleanup, exc: Exception, now: datetime) -> None:
    """A failed delete waits like a failed write: 15 minutes, doubling to 6 hours."""
    attempts = cleanup.attempts + 1
    TourCalendarCleanup.objects.filter(pk=cleanup.pk).update(
        attempts=attempts,
        last_error=(str(exc) or type(exc).__name__)[:300],
        next_attempt_at=now + min(RETRY_BASE * 2 ** (attempts - 1), RETRY_CAP),
    )


def _claim_cleanup(pk: int) -> bool:
    """Take the queued delete for this run, on the real clock; False when another run holds it."""
    now = timezone.now()
    free = Q(claimed_until__isnull=True) | Q(claimed_until__lt=now)
    return bool(TourCalendarCleanup.objects.filter(free, pk=pk).update(claimed_until=now + CLAIM_FOR))


def _run_cleanup(cleanup: TourCalendarCleanup, now: datetime, counts: CleanupCounts, guard: RunGuard) -> None:
    """Delete one claimed event, unless a booking points at it again (then the delete is dropped).

    The check runs under the claim, just before Google is called. A write that lands after it sees the
    claim and queues its booking again (:func:`_place`), so if this delete lands the event comes back.

    Raises:
        _Paused: the run stops here; the delete stays queued as it was.
    """
    calendar_id, event_id = cleanup.calendar_id, cleanup.event_id
    live = TourCalendarSync.objects.filter(connection=cleanup.connection_id, calendar_id=calendar_id, event_id=event_id)
    if live.exists():
        TourCalendarCleanup.objects.filter(pk=cleanup.pk).delete()
        return
    deadline = Deadline(WRITE_DEADLINE_SECONDS, guard)
    try:
        _with_token(
            cleanup.connection,
            lambda token, timeout: google.delete_event(token, calendar_id, event_id, timeout=timeout()),
            deadline,
        )
    except google.GoogleAuthError as exc:
        mark_broken(cleanup.connection, str(exc))
    except google.GoogleError as exc:
        _record_cleanup_failure(cleanup, exc, now)
        counts.failed += 1
    else:
        TourCalendarCleanup.objects.filter(pk=cleanup.pk).delete()
        counts.done += 1


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


def _refresh_calendar_list(connection: TourCalendarConnection, deadline: Deadline) -> None:
    """Read the guide's owned calendars again for the picker; a chosen one no longer among them is gone."""
    calendars = _with_token(connection, lambda token, timeout: google.list_calendars(token, timeout=timeout), deadline)
    chosen_name = connection.calendar_label
    connection.calendars = [calendar.as_json() for calendar in calendars]
    primary = next((calendar for calendar in calendars if calendar.primary), None)
    if primary is not None:
        connection.google_email = primary.id
    connection.save(update_fields=["calendars", "google_email", "updated_at"])
    if connection.calendar_id != "primary" and connection.calendar_id not in {cal.id for cal in calendars}:
        chosen_calendar_gone(connection, chosen_name)


def _calendar_busy(
    connection: TourCalendarConnection, calendar_id: str, start: datetime, end: datetime, deadline: Deadline
) -> list[tuple[datetime, datetime]]:
    events = _with_token(
        connection,
        lambda token, timeout: google.list_events(token, calendar_id, start, end, timeout=timeout),
        deadline,
    )
    return [span for span in map(busy_span, events) if span is not None]


def read_busy(
    connection: TourCalendarConnection, *, now: datetime, deadline: Deadline
) -> list[tuple[datetime, datetime]]:
    """Read the guide's calendars and busy times over the horizon, and cache the busy times.

    The picker's list first (a chosen calendar gone from it falls back to the main one), then the
    main calendar and the chosen one. A chosen calendar that answers "not found" is gone too.

    Raises:
        google.GoogleError: as the calls raise it, or when ``deadline`` runs out.
    """
    _refresh_calendar_list(connection, deadline)
    start, end = busy_window(now)
    spans = _calendar_busy(connection, "primary", start, end, deadline)
    if connection.calendar_id != "primary":
        try:
            spans += _calendar_busy(connection, connection.calendar_id, start, end, deadline)
        except google.GoogleNotFoundError:
            chosen_calendar_gone(connection)
    merged = merge_spans(spans)
    connection.busy_spans = [[span_start.isoformat(), span_end.isoformat()] for span_start, span_end in merged]
    connection.busy_checked_at = now
    connection.save(update_fields=["busy_spans", "busy_checked_at", "updated_at"])
    return merged


def refresh_busy(connection: TourCalendarConnection, *, now: datetime, guard: RunGuard | None = None) -> bool:
    """:func:`read_busy` within :data:`BUSY_DEADLINE_SECONDS`, keeping the cache as it was when Google
    fails, and breaking the connection when it refuses.

    Returns:
        Whether the busy times were read.

    Raises:
        _Paused: the run's budget is spent or its breaker is open; the cache stays as it was.
    """
    try:
        read_busy(connection, now=now, deadline=Deadline(BUSY_DEADLINE_SECONDS, guard))
    except google.GoogleAuthError as exc:
        mark_broken(connection, str(exc))
        return False
    except google.GoogleError as exc:
        logger.warning("Could not read %s's Google Calendar busy times: %s", connection.guide, exc)
        return False
    return True


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
    reads_deferred: int = 0
    conflicts: int = 0
    unread_told: int = 0
    stopped: str = ""
    writes: SyncCounts = field(default_factory=SyncCounts)
    cleanups: CleanupCounts = field(default_factory=CleanupCounts)
    stuck: list[str] = field(default_factory=list)


def _unread(now: datetime) -> Any:
    """Connected calendars unread for over :data:`STALE_ALERT` (not just connected)."""
    old = now - STALE_ALERT
    return (
        TourCalendarConnection.objects.healthy()
        .filter(connected_at__lt=old)
        .filter(Q(busy_checked_at__isnull=True) | Q(busy_checked_at__lt=old))
    )


def stuck_problems(now: datetime) -> list[str]:
    """What has been failing long enough that a person should look: the job fails on these."""
    problems: list[str] = []
    failing = TourCalendarSync.objects.filter(due=True, attempts__gte=STUCK_ATTEMPTS).count()
    failing += TourCalendarCleanup.objects.filter(attempts__gte=STUCK_ATTEMPTS).count()
    if failing:
        problems.append(f"{failing} tour event write(s) failed {STUCK_ATTEMPTS} or more times in a row")
    stale = _unread(now).count()
    if stale:
        problems.append(f"{stale} guide(s)' connected Google Calendar has not been read for over an hour")
    return problems


def tell_guides_unread(now: datetime) -> int:
    """Bell each guide whose calendar has gone unread for over an hour, once per stretch unread.

    The period is the last good read (or the connection, never read), so a guide hears again only
    after a read succeeded and then stopped again. Returns how many such guides there are.
    """
    from core.events.emit import emit

    stale = list(_unread(now).select_related("guide"))
    for connection in stale:
        since = connection.busy_checked_at or connection.connected_at
        emit(
            UNREAD_EVENT,
            target=connection,
            context={"guide": connection.guide},
            title=UNREAD_TITLE,
            body=(
                "We could not read your Google Calendar, so busy times may be out of date and visitors may "
                "be offered times you are busy. We keep trying; reconnect on My Hours if it goes on."
            ),
            url=reverse("tours:hours"),
            period=f"tour_calendar:{connection.pk}:unread:{since:%Y%m%dT%H%M%S}",
        )
    return len(stale)


def run_budget() -> float:
    """The run's budget: :data:`MANUAL_BUDGET_SECONDS` under Run now (a web request), else :data:`JOB_BUDGET_SECONDS`."""
    from core.scheduled_jobs import Trigger, current_run

    open_run = current_run()
    if open_run is not None and open_run.trigger == Trigger.MANUAL:
        return MANUAL_BUDGET_SECONDS
    return JOB_BUDGET_SECONDS


def run(now: datetime | None = None, *, budget: float | None = None) -> SyncReport:
    """The ``sync_tour_calendars`` job: read every connected guide's busy times, longest unread first
    (so one slow guide never starves the others), bell any clash with a booked tour, write due events,
    and bell guides whose calendar has gone unread for an hour.

    Within ``budget`` seconds (:func:`run_budget` by default) and until :data:`BREAKER_FAILURES`
    failures in a row; what is left then waits for the next tick, uncounted.
    """
    now = now or timezone.now()
    guard = RunGuard(run_budget() if budget is None else budget)
    report = SyncReport()
    connections = list(
        TourCalendarConnection.objects.healthy()
        .select_related("guide")
        .order_by(F("busy_checked_at").asc(nulls_first=True), "pk")
    )
    for index, connection in enumerate(connections):
        try:
            if guard.why_stopped:
                raise _Paused(guard.why_stopped)
            read = refresh_busy(connection, now=now, guard=guard)
        except _Paused:
            report.reads_deferred = len(connections) - index
            break
        if read:
            report.read += 1
        else:
            report.read_failed += 1
    report.conflicts = alert_busy_conflicts(now)
    report.writes = sync_due_bookings(now, guard)
    report.cleanups = run_cleanups(now, guard)
    report.stopped = guard.why_stopped
    report.unread_told = tell_guides_unread(now)
    report.stuck = stuck_problems(now)
    return report
