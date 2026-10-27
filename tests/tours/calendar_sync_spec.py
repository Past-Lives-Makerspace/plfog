"""A Tour Guide's Google Calendar both ways (#951): connecting, writing tour events, reading busy times,
breaking, and the job. Google is mocked with respx; nothing reaches the network."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx
from django.core import mail
from django.core.management import CommandError, call_command
from django.db import connection as db
from django.utils import timezone

from core.models import Notification
from membership.models import Member
from membership.services.provisioning import provision_user_for_member
from tests.tours.conftest import make_guide
from tests.tours.factories import (
    TourBookingFactory,
    TourCalendarConnectionFactory,
    TourHoursFactory,
    local,
)
from tours import calendar_sync, google, signals
from tours.models import TourBooking, TourCalendarCleanup, TourCalendarConnection, TourCalendarSync

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("google_on")]

API = google.API_URL
PRIMARY = f"{API}/calendars/primary/events"
TOURS_CAL = "tours@group.calendar.google.com"
TOURS_EVENTS = f"{API}/calendars/tours%40group.calendar.google.com/events"
CALENDAR_LIST = f"{API}/users/me/calendarList"
GRANTED = " ".join(google.SCOPES)


def _with_user(member: Member) -> Member:
    if member.user is None:
        provision_user_for_member(member)
        member.refresh_from_db()
    return member


def _token_answer(**extra: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json={"access_token": "fresh", "refresh_token": "refresh-new", "expires_in": 3600, "scope": GRANTED, **extra},
    )


def _calendar_list(*items: dict[str, Any]) -> httpx.Response:
    listed = items or (
        {"id": "gina@example.com", "summary": "Gina Guide", "primary": True},
        {"id": TOURS_CAL, "summary": "Tours"},
    )
    return httpx.Response(200, json={"items": list(listed)})


def _sent(route: respx.Route, index: int = -1) -> dict[str, Any]:
    return json.loads(route.calls[index].request.content)


def _event(start: datetime, end: datetime, **fields: Any) -> dict[str, Any]:
    return {"start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}, **fields}


@pytest.fixture
def guide() -> Member:
    return _with_user(make_guide(full_legal_name="Gina Guide"))


@pytest.fixture
def connection(guide: Member) -> TourCalendarConnection:
    return TourCalendarConnectionFactory(guide=guide)


@pytest.fixture
def booking(guide: Member) -> TourBooking:
    return TourBookingFactory(
        guide=guide, visitor_name="Sam Rivera", visitor_email="sam@example.com", visitor_phone="503 555 0142"
    )


def describe_connect_from_code():
    @respx.mock
    def it_stores_the_encrypted_tokens_and_calendars_and_queues_upcoming_tours(guide: Member):
        respx.post(google.TOKEN_URL).mock(return_value=_token_answer())
        respx.get(CALENDAR_LIST).mock(return_value=_calendar_list())
        upcoming = TourBookingFactory(guide=guide)
        TourBookingFactory(guide=guide, status=TourBooking.Status.CANCELLED)
        TourBookingFactory(guide=guide, starts_at=timezone.now() - timedelta(days=2))

        connection = calendar_sync.connect_from_code(guide, "the-code")

        connection.refresh_from_db()
        assert connection.google_email == "gina@example.com"
        assert (connection.refresh_token, connection.access_token) == ("refresh-new", "fresh")
        assert connection.calendar_id == "primary"
        assert connection.calendar_choices() == [("primary", "Gina Guide"), (TOURS_CAL, "Tours")]
        assert connection.status == TourCalendarConnection.Status.CONNECTED
        assert list(TourCalendarSync.objects.filter(due=True).values_list("booking_id", flat=True)) == [upcoming.pk]
        with db.cursor() as cursor:
            cursor.execute("SELECT refresh_token, access_token FROM tours_tourcalendarconnection")
            stored = cursor.fetchone()
        assert "refresh-new" not in stored[0] and "fresh" not in stored[1]

    @respx.mock
    def it_reconnects_in_place_keeping_a_calendar_still_owned(connection: TourCalendarConnection):
        connection.calendar_id = TOURS_CAL
        connection.status = TourCalendarConnection.Status.BROKEN
        connection.broken_at = timezone.now()
        connection.broken_reason = "revoked"
        connection.save()
        respx.post(google.TOKEN_URL).mock(return_value=_token_answer())
        respx.get(CALENDAR_LIST).mock(return_value=_calendar_list())

        again = calendar_sync.connect_from_code(connection.guide, "code")

        assert again.pk == connection.pk
        assert (again.status, again.broken_at, again.broken_reason, again.calendar_id) == (
            TourCalendarConnection.Status.CONNECTED,
            None,
            "",
            TOURS_CAL,
        )

    @respx.mock
    def it_falls_back_to_the_main_calendar_when_the_chosen_one_is_gone(connection: TourCalendarConnection):
        connection.calendar_id = "gone@group.calendar.google.com"
        connection.save()
        respx.post(google.TOKEN_URL).mock(return_value=_token_answer())
        respx.get(CALENDAR_LIST).mock(return_value=_calendar_list())
        assert calendar_sync.connect_from_code(connection.guide, "code").calendar_id == "primary"

    @respx.mock
    def it_forgets_events_on_another_accounts_calendar(connection: TourCalendarConnection, booking: TourBooking):
        row = TourCalendarSync.objects.create(
            booking=booking, connection=connection, calendar_id="primary", event_id="old", due=False
        )
        respx.post(google.TOKEN_URL).mock(return_value=_token_answer())
        respx.get(CALENDAR_LIST).mock(
            return_value=_calendar_list({"id": "other@example.com", "summary": "Other", "primary": True})
        )
        calendar_sync.connect_from_code(connection.guide, "code")
        row.refresh_from_db()
        assert (row.connection_id, row.event_id, row.due) == (None, "", True)

    @respx.mock
    def it_leaves_the_email_blank_without_a_main_calendar(guide: Member):
        respx.post(google.TOKEN_URL).mock(return_value=_token_answer())
        respx.get(CALENDAR_LIST).mock(return_value=_calendar_list({"id": TOURS_CAL, "summary": "Tours"}))
        assert calendar_sync.connect_from_code(guide, "code").google_email == ""


def describe_choose_calendar():
    def it_moves_upcoming_tours_and_reads_busy_times_again(connection: TourCalendarConnection, booking: TourBooking):
        connection.busy_spans = [["2030-01-01T00:00:00+00:00", "2030-01-01T01:00:00+00:00"]]
        connection.busy_checked_at = timezone.now()
        connection.save()
        calendar_sync.choose_calendar(connection, TOURS_CAL)
        connection.refresh_from_db()
        assert (connection.calendar_id, connection.busy_spans, connection.busy_checked_at) == (TOURS_CAL, [], None)
        assert connection.calendar_label == "Tours"
        assert TourCalendarSync.objects.get(booking=booking).due


def describe_disconnect():
    @respx.mock
    def it_revokes_deletes_and_forgets_where_events_were(connection: TourCalendarConnection, booking: TourBooking):
        route = respx.post(google.REVOKE_URL).mock(return_value=httpx.Response(200))
        row = TourCalendarSync.objects.create(
            booking=booking, connection=connection, calendar_id="primary", event_id="ev", due=True
        )
        assert calendar_sync.disconnect(connection) is True
        assert route.calls.last.request.content == b"token=refresh-1"
        assert not TourCalendarConnection.objects.exists()
        row.refresh_from_db()
        assert (row.connection_id, row.calendar_id, row.event_id, row.due) == (None, "", "", True)

    @respx.mock
    def it_deletes_here_even_when_google_does_not_confirm(connection: TourCalendarConnection):
        respx.post(google.REVOKE_URL).mock(return_value=httpx.Response(500))
        assert calendar_sync.disconnect(connection) is False
        assert not TourCalendarConnection.objects.exists()

    def it_skips_google_with_no_token_left(connection: TourCalendarConnection):
        connection.refresh_token = connection.access_token = ""
        connection.save()
        with respx.mock(assert_all_called=False) as mock:
            assert calendar_sync.disconnect(connection) is True
        assert not mock.calls


def describe_queue_booking_sync():
    def it_marks_a_connected_guides_booking_due(connection: TourCalendarConnection, booking: TourBooking):
        TourCalendarSync.objects.create(booking=booking, due=False, attempts=3, last_error="x")
        signals.tour_booking_changed.send(sender=TourBooking, booking=booking, change=signals.MOVED)
        row = TourCalendarSync.objects.get(booking=booking)
        assert (row.due, row.attempts, row.last_error) == (True, 0, "")

    def it_ignores_a_guide_with_no_calendar(booking: TourBooking):
        signals.tour_booking_changed.send(sender=TourBooking, booking=booking, change=signals.BOOKED)
        assert not TourCalendarSync.objects.exists()

    def it_follows_an_event_left_on_a_calendar(connection: TourCalendarConnection):
        other = TourBookingFactory(guide=make_guide())
        TourCalendarSync.objects.create(booking=other, connection=connection, event_id="ev", due=False)
        signals.tour_booking_changed.send(sender=TourBooking, booking=other, change=signals.MOVED)
        assert TourCalendarSync.objects.get(booking=other).due

    def it_hears_a_real_booking_once_committed_even_when_an_email_fails(
        guide: Member, connection: TourCalendarConnection, django_capture_on_commit_callbacks: Any
    ):
        from tours.booking import Visitor, book_tour

        day = date(2030, 1, 8)  # a Tuesday
        TourHoursFactory(guide=guide, seats=4)
        visitor = Visitor(name="Sam Rivera", email="sam@example.com", phone="", party_size=1)
        with (
            patch("tours.notify.announce", side_effect=RuntimeError("mail down")),
            django_capture_on_commit_callbacks(execute=True),
        ):
            booking = book_tour(
                guide=guide, starts_at=local(day, 16), visitor=visitor, now=local(day, 9) - timedelta(days=1)
            )
        assert TourCalendarSync.objects.get(booking=booking).due


def describe_access_token():
    def it_reuses_a_token_that_still_works(connection: TourCalendarConnection):
        with respx.mock(assert_all_called=False) as mock:
            assert calendar_sync.access_token(connection, timeout=1) == "access-1"
        assert not mock.calls

    @respx.mock
    def it_refreshes_an_expired_one_and_keeps_a_new_refresh_token(connection: TourCalendarConnection):
        connection.access_token_expires_at = timezone.now() + timedelta(seconds=30)
        connection.save()
        respx.post(google.TOKEN_URL).mock(return_value=_token_answer())
        assert calendar_sync.access_token(connection, timeout=1) == "fresh"
        connection.refresh_from_db()
        assert (connection.access_token, connection.refresh_token) == ("fresh", "refresh-new")

    @respx.mock
    def it_keeps_the_refresh_token_when_google_sends_none(connection: TourCalendarConnection):
        connection.access_token = ""
        connection.save()
        respx.post(google.TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "fresh"}))
        calendar_sync.access_token(connection, timeout=1)
        connection.refresh_from_db()
        assert connection.refresh_token == "refresh-1"

    def it_is_an_auth_error_with_no_refresh_token(connection: TourCalendarConnection):
        connection.access_token = connection.refresh_token = ""
        with pytest.raises(google.GoogleAuthError):
            calendar_sync.access_token(connection, timeout=1)


def describe_mark_broken():
    def it_breaks_once_clears_the_tokens_and_tells_the_guide_once(connection: TourCalendarConnection):
        assert calendar_sync.mark_broken(connection, "Token has been expired or revoked.") is True
        assert calendar_sync.mark_broken(connection, "again") is False
        connection.refresh_from_db()
        assert connection.is_broken
        assert (connection.refresh_token, connection.access_token, connection.broken_reason) == (
            "",
            "",
            "Token has been expired or revoked.",
        )
        emails = [message for message in mail.outbox if message.subject == "Reconnect your Google Calendar for tours"]
        assert len(emails) == 1
        assert emails[0].to == [connection.guide.user.email]
        assert "gina@example.com" in emails[0].body
        assert "https://members.example.org/facility-tours/hours/" in emails[0].body
        bells = Notification.objects.filter(user=connection.guide.user, trigger="tour.calendar_broken")
        assert bells.count() == 1


def describe_event_body():
    def it_names_the_visitor_and_party_and_carries_their_contact(booking: TourBooking):
        body = calendar_sync.event_body(booking)
        assert body["summary"] == "Past Lives tour: Sam Rivera (2)"
        assert "Email: sam@example.com" in body["description"]
        assert "Phone: 503 555 0142" in body["description"]
        assert body["extendedProperties"]["private"] == {"plfogTour": "1", "plfogBooking": str(booking.pk)}
        assert body["start"]["dateTime"] == booking.starts_at.astimezone(UTC).isoformat()

    def it_leaves_out_a_missing_phone(booking: TourBooking):
        booking.visitor_phone = ""
        assert "Phone" not in calendar_sync.event_body(booking)["description"]

    def it_makes_a_stable_id_google_accepts(booking: TourBooking, settings):
        event_id = calendar_sync.event_id_for(booking)
        assert event_id == calendar_sync.event_id_for(booking)
        assert event_id.startswith(f"pltour{booking.pk}s")
        assert set(event_id) <= set("0123456789abcdefghijklmnopqrstuv")
        settings.MEMBER_BASE_URL = "http://localhost:8000"
        assert calendar_sync.event_id_for(booking) != event_id


def _sync() -> calendar_sync.SyncCounts:
    """One pass of the job's writes."""
    return calendar_sync.sync_due_bookings(timezone.now())


def describe_sync_booking():
    def _row(booking: TourBooking, **fields: Any) -> TourCalendarSync:
        return TourCalendarSync.objects.create(booking=booking, **fields)

    @respx.mock
    def it_creates_the_event_on_the_guides_calendar(connection: TourCalendarConnection, booking: TourBooking):
        route = respx.post(PRIMARY).mock(return_value=httpx.Response(200, json={"id": "created"}))
        row = _row(booking)
        assert _sync().done == 1
        row.refresh_from_db()
        assert (row.event_id, row.connection_id, row.calendar_id, row.due) == (
            "created",
            connection.pk,
            "primary",
            False,
        )
        assert row.synced_at is not None
        assert _sent(route)["id"] == calendar_sync.event_id_for(booking)
        assert route.calls.last.request.headers["Authorization"] == "Bearer access-1"

    @respx.mock
    def it_updates_an_event_already_there(connection: TourCalendarConnection, booking: TourBooking):
        route = respx.put(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(200, json={"id": "ev1"}))
        row = _row(booking, connection=connection, calendar_id="primary", event_id="ev1")
        calendar_sync.sync_booking(row, now=timezone.now())
        assert _sent(route)["summary"] == "Past Lives tour: Sam Rivera (2)"
        assert TourCalendarSync.objects.get(pk=row.pk).event_id == "ev1"

    @respx.mock
    def it_recreates_an_event_google_lost(connection: TourCalendarConnection, booking: TourBooking):
        respx.put(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(404, json={}))
        respx.post(PRIMARY).mock(return_value=httpx.Response(200, json={"id": "again"}))
        row = _row(booking, connection=connection, calendar_id="primary", event_id="ev1")
        calendar_sync.sync_booking(row, now=timezone.now())
        assert TourCalendarSync.objects.get(pk=row.pk).event_id == "again"

    @respx.mock
    def it_updates_in_place_when_our_id_is_already_taken(connection: TourCalendarConnection, booking: TourBooking):
        ours = calendar_sync.event_id_for(booking)
        respx.post(PRIMARY).mock(return_value=httpx.Response(409, json={}))
        put = respx.put(f"{PRIMARY}/{ours}").mock(return_value=httpx.Response(200, json={}))
        row = _row(booking)
        calendar_sync.sync_booking(row, now=timezone.now())
        assert _sent(put)["status"] == "confirmed"
        assert TourCalendarSync.objects.get(pk=row.pk).event_id == ours

    @respx.mock
    def it_deletes_the_event_of_a_cancelled_tour(connection: TourCalendarConnection, booking: TourBooking):
        route = respx.delete(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(204))
        booking.status = TourBooking.Status.CANCELLED
        booking.save()
        row = _row(booking, connection=connection, calendar_id="primary", event_id="ev1")
        _sync()
        row.refresh_from_db()
        assert route.called
        assert (row.event_id, row.connection_id, row.due) == ("", None, False)

    def it_has_nothing_to_do_for_a_cancelled_tour_never_written(
        connection: TourCalendarConnection, booking: TourBooking
    ):
        booking.status = TourBooking.Status.CANCELLED
        booking.save()
        row = _row(booking)
        with respx.mock(assert_all_called=False) as mock:
            _sync()
        assert not mock.calls
        assert not TourCalendarSync.objects.get(pk=row.pk).due

    @respx.mock
    def it_moves_a_reassigned_tour_to_the_new_guides_calendar(connection: TourCalendarConnection, booking: TourBooking):
        new_guide = make_guide(full_legal_name="Nia New")
        TourCalendarConnectionFactory(guide=new_guide, calendar_id=TOURS_CAL, access_token="access-2")
        deleted = respx.delete(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(204))
        created = respx.post(TOURS_EVENTS).mock(return_value=httpx.Response(200, json={"id": "moved"}))
        row = _row(booking, connection=connection, calendar_id="primary", event_id="ev1")
        booking.guide = new_guide
        booking.save()
        calendar_sync.sync_booking(
            TourCalendarSync.objects.select_related("booking").get(pk=row.pk), now=timezone.now()
        )
        assert deleted.calls.last.request.headers["Authorization"] == "Bearer access-1"
        assert created.calls.last.request.headers["Authorization"] == "Bearer access-2"
        row.refresh_from_db()
        assert (row.event_id, row.calendar_id, row.connection.guide) == ("moved", TOURS_CAL, new_guide)

    @respx.mock
    def it_only_deletes_when_the_new_guide_has_no_calendar(connection: TourCalendarConnection, booking: TourBooking):
        respx.delete(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(204))
        row = _row(booking, connection=connection, calendar_id="primary", event_id="ev1")
        booking.guide = make_guide()
        booking.save()
        calendar_sync.sync_booking(
            TourCalendarSync.objects.select_related("booking").get(pk=row.pk), now=timezone.now()
        )
        assert TourCalendarSync.objects.get(pk=row.pk).event_id == ""

    @respx.mock
    def it_moves_the_event_when_the_guide_picks_another_calendar(
        connection: TourCalendarConnection, booking: TourBooking
    ):
        respx.delete(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(204))
        respx.post(TOURS_EVENTS).mock(return_value=httpx.Response(200, json={"id": "there"}))
        row = _row(booking, connection=connection, calendar_id="primary", event_id="ev1")
        connection.calendar_id = TOURS_CAL
        connection.save()
        calendar_sync.sync_booking(row, now=timezone.now())
        assert TourCalendarSync.objects.get(pk=row.pk).calendar_id == TOURS_CAL

    def it_forgets_an_event_left_by_a_guide_who_disconnected(booking: TourBooking):
        row = _row(booking, connection=None, calendar_id="primary", event_id="orphan")
        with respx.mock(assert_all_called=False) as mock:
            _sync()
        assert not mock.calls
        row.refresh_from_db()
        assert (row.event_id, row.due) == ("", False)

    def it_has_nothing_to_do_for_a_tour_with_no_guide(booking: TourBooking):
        booking.guide = None
        booking.save()
        row = _row(booking)
        _sync()
        assert not TourCalendarSync.objects.get(pk=row.pk).due

    def it_waits_while_the_guides_calendar_is_broken(connection: TourCalendarConnection, booking: TourBooking):
        connection.status = TourCalendarConnection.Status.BROKEN
        connection.save()
        with pytest.raises(calendar_sync._Waiting):
            calendar_sync.sync_booking(_row(booking), now=timezone.now())

    def it_waits_while_its_own_guides_broken_calendar_holds_the_event(
        connection: TourCalendarConnection, booking: TourBooking
    ):
        connection.status = TourCalendarConnection.Status.BROKEN
        connection.save()
        booking.status = TourBooking.Status.CANCELLED
        booking.save()
        row = _row(booking, connection=connection, calendar_id="primary", event_id="ev1")
        with pytest.raises(calendar_sync._Waiting):
            calendar_sync.sync_booking(row, now=timezone.now())

    @respx.mock
    def it_writes_the_new_guide_and_forgets_the_old_guides_broken_calendar(
        connection: TourCalendarConnection, booking: TourBooking
    ):
        TourCalendarConnection.objects.filter(pk=connection.pk).update(
            status=TourCalendarConnection.Status.BROKEN, refresh_token="", access_token=""
        )
        row = _row(booking, connection=connection, calendar_id="primary", event_id="ev1", due=False)
        new_guide = make_guide(full_legal_name="Nia New")
        TourCalendarConnectionFactory(guide=new_guide, calendar_id=TOURS_CAL, access_token="access-2")
        created = respx.post(TOURS_EVENTS).mock(return_value=httpx.Response(200, json={"id": "moved"}))
        booking.guide = new_guide
        booking.save()
        calendar_sync.queue_booking_sync(None, booking=booking)
        assert _sync().done == 1
        assert created.called
        row.refresh_from_db()
        assert (row.event_id, row.calendar_id, row.due) == ("moved", TOURS_CAL, False)
        assert list(TourCalendarCleanup.objects.values_list("connection_id", "event_id")) == [(connection.pk, "ev1")]

    @respx.mock
    def it_still_deletes_from_the_old_guide_when_the_new_guides_calendar_is_broken(
        connection: TourCalendarConnection, booking: TourBooking
    ):
        deleted = respx.delete(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(204))
        row = _row(booking, connection=connection, calendar_id="primary", event_id="ev1")
        new_guide = make_guide(full_legal_name="Nia New")
        TourCalendarConnectionFactory(guide=new_guide, status=TourCalendarConnection.Status.BROKEN)
        booking.guide = new_guide
        booking.save()
        assert _sync().waiting == 1
        assert deleted.called
        row.refresh_from_db()
        assert (row.event_id, row.connection_id, row.due) == ("", None, True)

    @respx.mock
    def it_forgets_the_event_when_the_old_guide_refuses_the_delete(
        connection: TourCalendarConnection, booking: TourBooking
    ):
        respx.delete(f"{PRIMARY}/ev1").mock(
            return_value=httpx.Response(403, json={"error": {"errors": [{"reason": "forbidden"}]}})
        )
        row = _row(booking, connection=connection, calendar_id="primary", event_id="ev1")
        booking.guide = make_guide()
        booking.save()
        assert _sync().done == 1
        connection.refresh_from_db()
        assert connection.is_broken
        assert TourCalendarSync.objects.get(pk=row.pk).event_id == ""
        assert TourCalendarCleanup.objects.filter(connection=connection, event_id="ev1").exists()

    @respx.mock
    def it_breaks_the_holder_when_google_refuses_the_delete_twice(
        connection: TourCalendarConnection, booking: TourBooking
    ):
        respx.post(google.TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "fresh"}))
        respx.delete(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(401, json={}))
        booking.status = TourBooking.Status.CANCELLED
        booking.save()
        row = _row(booking, connection=connection, calendar_id="primary", event_id="ev1")
        with pytest.raises(calendar_sync._Waiting):
            calendar_sync.sync_booking(row, now=timezone.now())
        connection.refresh_from_db()
        assert connection.is_broken

    @respx.mock
    def it_breaks_the_guides_calendar_when_google_refuses_the_write(
        connection: TourCalendarConnection, booking: TourBooking
    ):
        respx.post(PRIMARY).mock(
            return_value=httpx.Response(403, json={"error": {"errors": [{"reason": "insufficientPermissions"}]}})
        )
        with pytest.raises(calendar_sync._Waiting):
            calendar_sync.sync_booking(_row(booking), now=timezone.now())
        connection.refresh_from_db()
        assert connection.is_broken


def describe_sync_due_bookings():
    @respx.mock
    def it_writes_due_rows_and_leaves_the_rest(connection: TourCalendarConnection, guide: Member):
        now = timezone.now()
        due = TourCalendarSync.objects.create(booking=TourBookingFactory(guide=guide))
        TourCalendarSync.objects.create(booking=TourBookingFactory(guide=guide), due=False)
        TourCalendarSync.objects.create(
            booking=TourBookingFactory(guide=guide), next_attempt_at=now + timedelta(hours=1)
        )
        route = respx.post(PRIMARY).mock(return_value=httpx.Response(200, json={"id": "ok"}))
        counts = calendar_sync.sync_due_bookings(now)
        assert (counts.done, counts.failed, counts.waiting, counts.given_up) == (1, 0, 0, 0)
        assert route.call_count == 1
        assert not TourCalendarSync.objects.get(pk=due.pk).due

    @respx.mock
    def it_waits_longer_after_each_failure_up_to_six_hours(connection: TourCalendarConnection, booking: TourBooking):
        respx.post(PRIMARY).mock(return_value=httpx.Response(503, json={}))
        row = TourCalendarSync.objects.create(booking=booking)
        now = timezone.now()
        assert calendar_sync.sync_due_bookings(now).failed == 1
        row.refresh_from_db()
        assert (row.due, row.attempts, row.next_attempt_at) == (True, 1, now + timedelta(minutes=15))
        assert "503" in row.last_error
        later = now + timedelta(minutes=16)
        calendar_sync.sync_due_bookings(later)
        row.refresh_from_db()
        assert (row.attempts, row.next_attempt_at) == (2, later + timedelta(minutes=30))
        row.attempts = 9
        row.save()
        calendar_sync.sync_due_bookings(later + timedelta(hours=1))
        row.refresh_from_db()
        assert row.next_attempt_at == later + timedelta(hours=7)

    def it_records_an_unexpected_error_and_carries_on(connection: TourCalendarConnection, booking: TourBooking):
        TourCalendarSync.objects.create(booking=booking)
        with patch("tours.calendar_sync.sync_booking", side_effect=RuntimeError("")):
            assert calendar_sync.sync_due_bookings(timezone.now()).failed == 1
        assert TourCalendarSync.objects.get(booking=booking).last_error == "RuntimeError"

    def it_counts_rows_waiting_on_a_reconnect(connection: TourCalendarConnection, booking: TourBooking):
        connection.status = TourCalendarConnection.Status.BROKEN
        connection.save()
        TourCalendarSync.objects.create(booking=booking)
        assert calendar_sync.sync_due_bookings(timezone.now()).waiting == 1
        assert TourCalendarSync.objects.get(booking=booking).due

    @respx.mock
    def it_keeps_a_move_made_while_it_was_writing(connection: TourCalendarConnection, booking: TourBooking):
        TourCalendarSync.objects.create(booking=booking)
        new_start = booking.starts_at + timedelta(hours=2)

        def staff_moves_it_meanwhile(request: httpx.Request) -> httpx.Response:
            TourBooking.objects.filter(pk=booking.pk).update(
                starts_at=new_start, ends_at=new_start + timedelta(hours=1)
            )
            calendar_sync.queue_booking_sync(None, booking=TourBooking.objects.get(pk=booking.pk))
            return httpx.Response(200, json={"id": "ev"})

        route = respx.post(PRIMARY).mock(side_effect=staff_moves_it_meanwhile)
        respx.put(url__startswith=f"{PRIMARY}/").mock(return_value=httpx.Response(200, json={}))
        _sync()
        row = TourCalendarSync.objects.get(booking=booking)
        assert row.due and row.event_id == "ev"
        assert _sent(route)["start"]["dateTime"] == booking.starts_at.astimezone(UTC).isoformat()
        _sync()
        row.refresh_from_db()
        assert not row.due

    def it_reads_the_booking_fresh_for_each_row(connection: TourCalendarConnection, booking: TourBooking):
        TourCalendarSync.objects.create(booking=booking)
        seen: list[Any] = []
        with patch(
            "tours.calendar_sync.sync_booking", side_effect=lambda row, now, guard: seen.append(row.booking.party_size)
        ):
            TourBooking.objects.filter(pk=booking.pk).update(party_size=5)
            _sync()
        assert seen == [5]

    def it_leaves_a_row_another_run_is_writing(connection: TourCalendarConnection, booking: TourBooking):
        TourCalendarSync.objects.create(booking=booking, claimed_until=timezone.now() + timedelta(minutes=5))
        with respx.mock(assert_all_called=False) as mock:
            counts = _sync()
        assert not mock.calls and counts.done == 0

    def it_cannot_claim_a_row_already_claimed(connection: TourCalendarConnection, booking: TourBooking):
        row = TourCalendarSync.objects.create(booking=booking)
        assert calendar_sync._claim(row.pk) is not None
        assert calendar_sync._claim(row.pk) is None
        TourCalendarSync.objects.filter(pk=row.pk).update(claimed_until=timezone.now() - timedelta(seconds=1))
        assert calendar_sync._claim(row.pk) is not None

    def it_claims_on_the_real_clock_however_long_the_run_has_gone(
        connection: TourCalendarConnection, booking: TourBooking
    ):
        TourCalendarSync.objects.create(booking=booking)
        run_started = timezone.now() - timedelta(minutes=11)
        seen: list[Any] = []

        def write(row: TourCalendarSync, now: datetime, guard: Any) -> None:
            seen.append(TourCalendarSync.objects.get(pk=row.pk).claimed_until)
            assert calendar_sync._claim(row.pk) is None  # a run starting now cannot take it

        with patch("tours.calendar_sync.sync_booking", side_effect=write):
            calendar_sync.sync_due_bookings(run_started)
        assert seen and seen[0] > timezone.now() + calendar_sync.CLAIM_FOR - timedelta(minutes=1)

    def it_lets_go_of_a_row_once_written(connection: TourCalendarConnection, booking: TourBooking):
        TourCalendarSync.objects.create(booking=booking)
        with patch("tours.calendar_sync.sync_booking"):
            _sync()
        assert TourCalendarSync.objects.get(booking=booking).claimed_until is None

    def it_skips_a_row_claimed_between_the_list_and_the_claim(connection: TourCalendarConnection, booking: TourBooking):
        TourCalendarSync.objects.create(booking=booking)
        with patch("tours.calendar_sync._claim", return_value=None), patch("tours.calendar_sync.sync_booking") as write:
            _sync()
        assert not write.called

    def it_keeps_rows_waiting_on_a_reconnect_out_of_the_way(connection: TourCalendarConnection, guide: Member):
        connection.status = TourCalendarConnection.Status.BROKEN
        connection.save()
        waiting = TourCalendarSync.objects.create(booking=TourBookingFactory(guide=guide))
        now = timezone.now()
        calendar_sync.sync_due_bookings(now)
        waiting.refresh_from_db()
        assert waiting.next_attempt_at == now + calendar_sync.WAIT_RECHECK
        assert waiting.attempts == 0 and "reconnect" in waiting.last_error
        with patch("tours.calendar_sync.sync_booking") as write:
            calendar_sync.sync_due_bookings(now + timedelta(minutes=15))
        assert not write.called

    def it_gives_up_a_day_after_the_tour_ended(connection: TourCalendarConnection, guide: Member):
        old = TourBookingFactory(guide=guide, starts_at=timezone.now() - timedelta(days=3))
        TourCalendarSync.objects.create(booking=old)
        assert calendar_sync.sync_due_bookings(timezone.now()).given_up == 1
        row = TourCalendarSync.objects.get(booking=old)
        assert (row.due, row.last_error) == (False, "The tour was over before Google was updated.")


def describe_busy_span():
    START = datetime(2030, 1, 8, 18, tzinfo=UTC)
    END = START + timedelta(hours=1)

    def it_reads_a_timed_event_in_utc():
        start = START.astimezone(timezone.get_current_timezone())
        assert calendar_sync.busy_span(_event(start, start + timedelta(hours=1))) == (START, END)

    def it_reads_a_naive_time_as_local():
        naive = {"dateTime": "2030-01-08T10:00:00"}
        span = calendar_sync.busy_span({"start": naive, "end": {"dateTime": "2030-01-08T11:00:00"}})
        assert span is not None and timezone.localtime(span[0]).hour == 10

    def it_reads_an_all_day_event_from_local_midnight():
        span = calendar_sync.busy_span({"start": {"date": "2030-01-08"}, "end": {"date": "2030-01-09"}})
        assert span is not None
        assert timezone.localtime(span[0]) == local(date(2030, 1, 8), 0)
        assert span[1] - span[0] == timedelta(days=1)

    @pytest.mark.parametrize(
        "fields",
        [
            {"status": "cancelled"},
            {"transparency": "transparent"},
            {"eventType": "workingLocation"},
            {"eventType": "birthday"},
            {"extendedProperties": {"private": {"plfogTour": "1"}}},
            {"attendees": [{"email": "x@example.com"}, {"self": True, "responseStatus": "declined"}]},
        ],
    )
    def it_skips_what_blocks_no_time(fields: dict[str, Any]):
        assert calendar_sync.busy_span(_event(START, END, **fields)) is None

    def it_counts_an_event_the_guide_accepted():
        event = _event(START, END, attendees=[{"self": True, "responseStatus": "accepted"}], extendedProperties={})
        assert calendar_sync.busy_span(event) == (START, END)

    @pytest.mark.parametrize(
        "event",
        [
            {"start": "junk", "end": {"date": "2030-01-09"}},
            {"start": {}, "end": {"date": "2030-01-09"}},
            {"start": {"date": "2030-01-09"}, "end": {"date": "2030-01-09"}},
        ],
    )
    def it_skips_an_event_with_no_usable_times(event: dict[str, Any]):
        assert calendar_sync.busy_span(event) is None


def describe_spans():
    T = datetime(2030, 1, 8, 18, tzinfo=UTC)
    H = timedelta(hours=1)

    def it_joins_overlapping_and_touching_spans():
        merged = calendar_sync.merge_spans([(T + 3 * H, T + 4 * H), (T, T + H), (T + H, T + 2 * H), (T, T + H / 2)])
        assert merged == [(T, T + 2 * H), (T + 3 * H, T + 4 * H)]

    def it_counts_shared_time_but_not_touching_ends():
        spans = [(T, T + H)]
        assert calendar_sync.overlaps(T + H / 2, T + 2 * H, spans)
        assert not calendar_sync.overlaps(T + H, T + 2 * H, spans)
        assert not calendar_sync.overlaps(T - H, T, spans)


def describe_read_busy():
    @respx.mock
    def it_reads_the_main_and_chosen_calendars_leaving_out_our_tours(connection: TourCalendarConnection):
        now = timezone.now()
        a = now + timedelta(days=1)
        connection.calendar_id = TOURS_CAL
        connection.save()
        primary = respx.get(PRIMARY).mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        _event(a, a + timedelta(hours=1)),
                        _event(a, a + timedelta(hours=1), extendedProperties={"private": {"plfogTour": "1"}}),
                    ]
                },
            )
        )
        respx.get(TOURS_EVENTS).mock(
            return_value=httpx.Response(
                200, json={"items": [_event(a + timedelta(minutes=30), a + timedelta(hours=2))]}
            )
        )
        respx.get(CALENDAR_LIST).mock(
            return_value=_calendar_list(
                {"id": "gina.new@example.com", "summary": "Gina", "primary": True},
                {"id": TOURS_CAL, "summary": "Tours"},
            )
        )
        spans = calendar_sync.read_busy(connection, now=now, deadline=calendar_sync.Deadline(30))
        assert spans == [(a.astimezone(UTC), (a + timedelta(hours=2)).astimezone(UTC))]
        connection.refresh_from_db()
        assert connection.busy_checked_at == now
        assert connection.busy_intervals() == spans
        assert connection.google_email == "gina.new@example.com"
        horizon_end = datetime.fromisoformat(primary.calls.last.request.url.params["timeMax"])
        assert timezone.localtime(horizon_end).date() == timezone.localdate(now) + timedelta(days=31)

    @respx.mock
    def it_keeps_the_email_when_the_list_has_no_main_calendar(connection: TourCalendarConnection):
        respx.get(CALENDAR_LIST).mock(return_value=_calendar_list({"id": TOURS_CAL, "summary": "Tours"}))
        respx.get(PRIMARY).mock(return_value=httpx.Response(200, json={"items": []}))
        calendar_sync.read_busy(connection, now=timezone.now(), deadline=calendar_sync.Deadline(30))
        connection.refresh_from_db()
        assert connection.google_email == "gina@example.com"
        assert connection.calendar_choices() == [(TOURS_CAL, "Tours")]

    @respx.mock
    def it_reads_the_main_calendar_once_when_it_is_the_chosen_one(connection: TourCalendarConnection):
        respx.get(CALENDAR_LIST).mock(return_value=_calendar_list())
        route = respx.get(PRIMARY).mock(return_value=httpx.Response(200, json={"items": []}))
        calendar_sync.read_busy(connection, now=timezone.now(), deadline=calendar_sync.Deadline(30))
        assert route.call_count == 1


def describe_a_chosen_calendar_that_is_gone():
    def _gone_bells(connection: TourCalendarConnection) -> Any:
        return Notification.objects.filter(user=connection.guide.user, trigger="tour.calendar_gone")

    @pytest.fixture
    def on_tours(connection: TourCalendarConnection) -> TourCalendarConnection:
        connection.calendar_id = TOURS_CAL
        connection.save()
        return connection

    @respx.mock
    def it_falls_back_when_the_calendar_is_no_longer_listed(on_tours: TourCalendarConnection, booking: TourBooking):
        respx.get(CALENDAR_LIST).mock(
            return_value=_calendar_list({"id": "gina@example.com", "summary": "Gina", "primary": True})
        )
        respx.get(PRIMARY).mock(return_value=httpx.Response(200, json={"items": []}))
        assert calendar_sync.refresh_busy(on_tours, now=timezone.now()) is True
        on_tours.refresh_from_db()
        assert on_tours.calendar_id == "primary" and not on_tours.is_broken
        assert TourCalendarSync.objects.get(booking=booking).due
        assert list(_gone_bells(on_tours).values_list("title", flat=True)) == ["Your tours calendar is gone"]
        assert "Tours" in _gone_bells(on_tours).get().body

    @respx.mock
    def it_falls_back_when_the_calendar_answers_not_found(on_tours: TourCalendarConnection):
        respx.get(CALENDAR_LIST).mock(return_value=_calendar_list())
        respx.get(PRIMARY).mock(return_value=httpx.Response(200, json={"items": []}))
        respx.get(TOURS_EVENTS).mock(
            return_value=httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})
        )
        now = timezone.now()
        assert calendar_sync.refresh_busy(on_tours, now=now) is True
        on_tours.refresh_from_db()
        assert (on_tours.calendar_id, on_tours.busy_checked_at) == ("primary", now)
        assert calendar_sync.stuck_problems(now + timedelta(minutes=30)) == []

    @respx.mock
    def it_writes_to_the_main_calendar_when_a_create_finds_it_gone(
        on_tours: TourCalendarConnection, booking: TourBooking
    ):
        respx.post(TOURS_EVENTS).mock(return_value=httpx.Response(404, json={}))
        main = respx.post(PRIMARY).mock(return_value=httpx.Response(200, json={"id": "on-main"}))
        TourCalendarSync.objects.create(booking=booking)
        _sync()
        assert main.called
        row = TourCalendarSync.objects.get(booking=booking)
        assert (row.event_id, row.calendar_id) == ("on-main", "primary")
        assert _gone_bells(on_tours).count() == 1

    @respx.mock
    def it_retries_later_when_the_main_calendar_itself_is_not_found(
        connection: TourCalendarConnection, booking: TourBooking
    ):
        respx.post(PRIMARY).mock(return_value=httpx.Response(404, json={}))
        TourCalendarSync.objects.create(booking=booking)
        assert _sync().failed == 1


def describe_the_401_retry():
    @respx.mock
    def it_gets_a_fresh_token_once_and_carries_on(connection: TourCalendarConnection, booking: TourBooking):
        token = respx.post(google.TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "fresh"}))
        respx.post(PRIMARY).mock(side_effect=[httpx.Response(401, json={}), httpx.Response(200, json={"id": "ev"})])
        TourCalendarSync.objects.create(booking=booking)
        assert _sync().done == 1
        assert token.call_count == 1
        connection.refresh_from_db()
        assert not connection.is_broken and connection.access_token == "fresh"

    @respx.mock
    def it_breaks_when_the_refresh_is_refused(connection: TourCalendarConnection, booking: TourBooking):
        respx.post(google.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
        respx.post(PRIMARY).mock(return_value=httpx.Response(401, json={}))
        TourCalendarSync.objects.create(booking=booking)
        assert _sync().waiting == 1
        connection.refresh_from_db()
        assert connection.is_broken


def describe_the_per_guide_deadline():
    def it_stops_reading_one_guide_when_their_time_is_spent(connection: TourCalendarConnection, monkeypatch):
        clock = {"t": 1000.0}
        monkeypatch.setattr(calendar_sync.clock, "monotonic", lambda: clock["t"])

        def slow(request: httpx.Request) -> httpx.Response:
            clock["t"] += calendar_sync.BUSY_DEADLINE_SECONDS
            return _calendar_list()

        with respx.mock(assert_all_called=False) as mock:
            mock.get(CALENDAR_LIST).mock(side_effect=slow)
            events = mock.get(PRIMARY).mock(return_value=httpx.Response(200, json={"items": []}))
            assert calendar_sync.refresh_busy(connection, now=timezone.now()) is False
        assert not events.called
        connection.refresh_from_db()
        assert connection.busy_checked_at is None and not connection.is_broken

    def it_gives_each_call_what_is_left(monkeypatch):
        clock = {"t": 0.0}
        monkeypatch.setattr(calendar_sync.clock, "monotonic", lambda: clock["t"])
        deadline = calendar_sync.Deadline(12)
        assert deadline.timeout() == google.DEFAULT_TIMEOUT
        clock["t"] = 9
        assert deadline.timeout() == 3


def describe_refresh_busy():
    @respx.mock
    def it_keeps_the_cache_when_google_fails(connection: TourCalendarConnection):
        connection.busy_spans = [["2030-01-08T18:00:00+00:00", "2030-01-08T19:00:00+00:00"]]
        connection.save()
        respx.get(CALENDAR_LIST).mock(return_value=httpx.Response(500, json={}))
        assert calendar_sync.refresh_busy(connection, now=timezone.now()) is False
        connection.refresh_from_db()
        assert len(connection.busy_spans) == 1

    @respx.mock
    def it_breaks_the_connection_when_google_refuses(connection: TourCalendarConnection):
        connection.access_token = ""
        connection.save()
        respx.post(google.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
        assert calendar_sync.refresh_busy(connection, now=timezone.now()) is False
        connection.refresh_from_db()
        assert connection.is_broken


def describe_alert_busy_conflicts():
    def _busy_over(connection: TourCalendarConnection, booking: TourBooking) -> None:
        connection.busy_spans = [[booking.starts_at.isoformat(), booking.ends_at.isoformat()]]
        connection.busy_checked_at = timezone.now()
        connection.save()

    def it_bells_the_guide_once_and_keeps_the_tour(connection: TourCalendarConnection, booking: TourBooking):
        _busy_over(connection, booking)
        assert calendar_sync.alert_busy_conflicts(timezone.now()) == 1
        calendar_sync.alert_busy_conflicts(timezone.now())
        bells = Notification.objects.filter(user=connection.guide.user, trigger="tour.calendar_busy")
        assert list(bells.values_list("title", flat=True)) == [calendar_sync.BUSY_TITLE]
        assert "Sam Rivera" in bells.get().body
        booking.refresh_from_db()
        assert booking.status == TourBooking.Status.BOOKED

    def it_rings_again_after_the_tour_moves_to_another_busy_time(
        connection: TourCalendarConnection, booking: TourBooking
    ):
        _busy_over(connection, booking)
        calendar_sync.alert_busy_conflicts(timezone.now())
        booking.starts_at += timedelta(days=1)
        booking.ends_at += timedelta(days=1)
        booking.save()
        _busy_over(connection, booking)
        calendar_sync.alert_busy_conflicts(timezone.now())
        assert Notification.objects.filter(trigger="tour.calendar_busy").count() == 2

    def it_ignores_cancelled_tours_and_free_times(connection: TourCalendarConnection, booking: TourBooking):
        _busy_over(connection, booking)
        booking.status = TourBooking.Status.CANCELLED
        booking.save()
        TourBookingFactory(guide=connection.guide, starts_at=booking.starts_at + timedelta(hours=1))
        assert calendar_sync.alert_busy_conflicts(timezone.now()) == 0

    def it_leaves_an_earlier_tour_on_the_same_day_alone(connection: TourCalendarConnection, booking: TourBooking):
        _busy_over(connection, booking)
        TourBookingFactory(guide=connection.guide, starts_at=booking.starts_at - timedelta(hours=2))
        assert calendar_sync.alert_busy_conflicts(timezone.now()) == 1

    def it_skips_a_calendar_with_no_busy_times(connection: TourCalendarConnection, booking: TourBooking):
        connection.busy_checked_at = timezone.now()
        connection.save()
        assert calendar_sync.alert_busy_conflicts(timezone.now()) == 0


def describe_the_job():
    def it_says_so_when_google_is_not_set_up(settings):
        settings.GOOGLE_OAUTH_CLIENT_SECRET = ""
        out = StringIO()
        call_command("sync_tour_calendars", stdout=out)
        assert "not set up" in out.getvalue()

    @respx.mock
    def it_reads_bells_and_writes_in_one_run(connection: TourCalendarConnection, booking: TourBooking):
        respx.get(CALENDAR_LIST).mock(return_value=_calendar_list())
        respx.get(PRIMARY).mock(
            return_value=httpx.Response(200, json={"items": [_event(booking.starts_at, booking.ends_at)]})
        )
        respx.post(PRIMARY).mock(return_value=httpx.Response(200, json={"id": "ev"}))
        TourCalendarSync.objects.create(booking=booking)
        out = StringIO()
        call_command("sync_tour_calendars", stdout=out)
        assert (
            "Busy times read for 1 guide(s), 0 failed, 0 left for the next run; 1 booked tour(s) over a busy time"
            in out.getvalue()
        )
        assert "Stopped early" not in out.getvalue()
        assert "events written 1" in out.getvalue()
        assert TourCalendarSync.objects.get(booking=booking).event_id == "ev"

    @respx.mock
    def it_counts_a_failed_read(connection: TourCalendarConnection):
        respx.get(CALENDAR_LIST).mock(return_value=httpx.Response(500, json={}))
        report = calendar_sync.run()
        assert (report.read, report.read_failed) == (0, 1)

    @respx.mock
    def it_reads_every_connected_calendar_every_tick(connection: TourCalendarConnection):
        connection.busy_checked_at = timezone.now() - timedelta(minutes=5)
        connection.save()
        TourCalendarConnectionFactory(guide=make_guide(), status=TourCalendarConnection.Status.BROKEN)
        respx.get(CALENDAR_LIST).mock(return_value=_calendar_list())
        respx.get(PRIMARY).mock(return_value=httpx.Response(200, json={"items": []}))
        assert calendar_sync.run().read == 1

    def it_fails_loudly_when_writes_keep_failing_or_a_calendar_goes_unread(
        connection: TourCalendarConnection, booking: TourBooking
    ):
        TourCalendarSync.objects.create(
            booking=booking, attempts=calendar_sync.STUCK_ATTEMPTS, next_attempt_at=timezone.now() + timedelta(hours=1)
        )
        connection.connected_at = timezone.now() - timedelta(hours=2)
        connection.busy_checked_at = timezone.now() - timedelta(minutes=5)
        connection.save()
        problems = calendar_sync.stuck_problems(timezone.now() + timedelta(hours=1))
        assert problems == [
            "1 tour event write(s) failed 4 or more times in a row",
            "1 guide(s)' connected Google Calendar has not been read for over an hour",
        ]
        with (
            patch("tours.calendar_sync.stuck_problems", return_value=problems),
            pytest.raises(CommandError, match="failed 4 or more times"),
        ):
            call_command("sync_tour_calendars", stdout=StringIO())


def describe_connection_model():
    def it_lists_the_main_calendar_as_primary_and_names_the_chosen_one(connection: TourCalendarConnection):
        assert connection.calendar_choices() == [("primary", "Gina Guide"), (TOURS_CAL, "Tours")]
        assert connection.calendar_label == "Gina Guide"
        connection.calendars = []
        assert connection.calendar_label == "the main calendar"
        connection.calendar_id = "other@group"
        assert connection.calendar_label == "other@group"
        assert "Connected" in str(connection)
        assert str(TourCalendarSync(booking=TourBookingFactory(), due=True)).endswith("no event (due)")

    def it_offers_busy_times_only_from_healthy_read_calendars(connection: TourCalendarConnection, settings):
        connection.busy_spans = [["2030-01-08T18:00:00+00:00", "2030-01-08T19:00:00+00:00"]]
        connection.save()
        guide_ids = [connection.guide_id]
        assert TourCalendarConnection.objects.busy_by_guide(guide_ids) == {}
        connection.busy_checked_at = timezone.now()
        connection.save()
        assert TourCalendarConnection.objects.busy_by_guide(guide_ids) == {
            connection.guide_id: [(datetime(2030, 1, 8, 18, tzinfo=UTC), datetime(2030, 1, 8, 19, tzinfo=UTC))]
        }
        settings.GOOGLE_OAUTH_CLIENT_ID = ""
        assert TourCalendarConnection.objects.busy_by_guide(guide_ids) == {}


def describe_the_runs_limits():
    def _slow(clock: dict[str, float], seconds: float, response: httpx.Response) -> Any:
        def answer(request: httpx.Request) -> httpx.Response:
            clock["t"] += seconds
            return response

        return answer

    @pytest.fixture
    def clock(monkeypatch) -> dict[str, float]:
        fake = {"t": 1000.0}
        monkeypatch.setattr(calendar_sync.clock, "monotonic", lambda: fake["t"])
        return fake

    def it_holds_one_guides_read_to_its_deadline_across_pages(connection: TourCalendarConnection, clock: dict):
        pages = {"n": 0}

        def page(request: httpx.Request) -> httpx.Response:
            clock["t"] += 9.0
            pages["n"] += 1
            assert request.extensions["timeout"]["read"] <= google.DEFAULT_TIMEOUT
            return httpx.Response(200, json={"items": [], "nextPageToken": f"p{pages['n']}"})

        with respx.mock(assert_all_called=False) as mock:
            mock.get(CALENDAR_LIST).mock(return_value=_calendar_list())
            mock.get(PRIMARY).mock(side_effect=page)
            start = clock["t"]
            assert calendar_sync.refresh_busy(connection, now=timezone.now()) is False
        assert clock["t"] - start <= calendar_sync.BUSY_DEADLINE_SECONDS + google.DEFAULT_TIMEOUT
        assert pages["n"] == 4
        connection.refresh_from_db()
        assert connection.busy_checked_at is None and not connection.is_broken

    def it_asks_the_deadline_before_every_calendar_list_page(connection: TourCalendarConnection, clock: dict):
        page = httpx.Response(200, json={"items": [{"id": "x@group"}], "nextPageToken": "more"})
        with respx.mock(assert_all_called=False) as mock:
            listed = mock.get(CALENDAR_LIST).mock(side_effect=_slow(clock, 11.0, page))
            assert calendar_sync.refresh_busy(connection, now=timezone.now()) is False
        assert listed.call_count == 3

    @respx.mock
    def it_stops_the_run_when_its_budget_is_spent_and_counts_nothing_failed(
        connection: TourCalendarConnection, booking: TourBooking, clock: dict
    ):
        second = TourCalendarConnectionFactory(guide=make_guide(), busy_checked_at=timezone.now())
        # 4 seconds a call against a 5 second run: the first guide is read, the second is cut short.
        respx.get(CALENDAR_LIST).mock(side_effect=_slow(clock, 4.0, _calendar_list()))
        respx.get(PRIMARY).mock(return_value=httpx.Response(200, json={"items": []}))
        TourCalendarSync.objects.create(booking=booking)
        report = calendar_sync.run(budget=5)
        assert (report.read, report.read_failed, report.reads_deferred) == (1, 0, 1)
        assert report.writes.deferred == 1 and report.writes.failed == 0
        assert report.stopped == "the run's time was spent"
        row = TourCalendarSync.objects.get(booking=booking)
        assert row.due and row.attempts == 0 and row.claimed_until is None
        second.refresh_from_db()
        assert not second.is_broken

    @respx.mock
    def it_opens_the_breaker_after_three_failures_in_a_row(guide: Member):
        connections = [TourCalendarConnectionFactory(guide=make_guide()) for _ in range(5)]
        listed = respx.get(CALENDAR_LIST).mock(return_value=httpx.Response(503, json={}))
        report = calendar_sync.run(budget=600)
        assert listed.call_count == 3
        assert (report.read_failed, report.reads_deferred) == (3, 2)
        assert report.stopped == "Google failed 3 times in a row"
        assert not any(TourCalendarConnection.objects.get(pk=c.pk).is_broken for c in connections)

    @respx.mock
    def it_closes_the_breaker_again_on_an_answer(guide: Member):
        for _ in range(4):
            TourCalendarConnectionFactory(guide=make_guide())
        respx.get(CALENDAR_LIST).mock(
            side_effect=[
                httpx.Response(503, json={}),
                httpx.Response(503, json={}),
                _calendar_list(),
                httpx.Response(503, json={}),
            ]
        )
        respx.get(PRIMARY).mock(return_value=httpx.Response(200, json={"items": []}))
        report = calendar_sync.run(budget=600)
        assert (report.read, report.read_failed, report.stopped) == (1, 3, "")

    @respx.mock
    def it_counts_a_404_as_an_answer(connection: TourCalendarConnection, booking: TourBooking):
        guard = calendar_sync.RunGuard()
        guard.failures = 2
        respx.put(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(404, json={}))
        respx.post(PRIMARY).mock(return_value=httpx.Response(200, json={"id": "again"}))
        TourCalendarSync.objects.create(booking=booking, connection=connection, calendar_id="primary", event_id="ev1")
        assert calendar_sync.sync_due_bookings(timezone.now(), guard).done == 1
        assert guard.failures == 0

    def it_leaves_the_rest_of_the_writes_once_stopped(connection: TourCalendarConnection, guide: Member):
        for _ in range(3):
            TourCalendarSync.objects.create(booking=TourBookingFactory(guide=guide))
        guard = calendar_sync.RunGuard()
        guard.failures = calendar_sync.BREAKER_FAILURES
        with patch("tours.calendar_sync.sync_booking") as write:
            counts = calendar_sync.sync_due_bookings(timezone.now(), guard)
        assert not write.called and counts.deferred == 3

    def it_pauses_mid_write_and_keeps_the_row_as_it_was(connection: TourCalendarConnection, booking: TourBooking):
        TourCalendarSync.objects.create(booking=booking)
        with patch("tours.calendar_sync.sync_booking", side_effect=calendar_sync._Paused("spent")):
            counts = calendar_sync.sync_due_bookings(timezone.now())
        assert counts.deferred == 1
        row = TourCalendarSync.objects.get(booking=booking)
        assert (row.due, row.attempts, row.last_error, row.claimed_until) == (True, 0, "", None)

    def it_gives_run_now_twenty_seconds_and_a_tick_two_minutes():
        from core.scheduled_jobs import Trigger, record_run

        assert calendar_sync.run_budget() == calendar_sync.JOB_BUDGET_SECONDS == 120
        with record_run("sync_tour_calendars", trigger=Trigger.MANUAL):
            assert calendar_sync.run_budget() == calendar_sync.MANUAL_BUDGET_SECONDS == 20
        with record_run("sync_tour_calendars", trigger=Trigger.SCHEDULED):
            assert calendar_sync.run_budget() == 120

    def it_says_when_it_stopped_early(connection: TourCalendarConnection):
        out = StringIO()
        with patch(
            "tours.calendar_sync.run", return_value=calendar_sync.SyncReport(stopped="Google failed 3 times in a row")
        ):
            call_command("sync_tour_calendars", stdout=out)
        assert "Stopped early: Google failed 3 times in a row." in out.getvalue()


def describe_guides_unread_for_an_hour():
    @pytest.fixture
    def unread(connection: TourCalendarConnection) -> TourCalendarConnection:
        connection.connected_at = timezone.now() - timedelta(hours=3)
        connection.busy_checked_at = timezone.now() - timedelta(hours=2)
        connection.save()
        return connection

    def _bells(connection: TourCalendarConnection) -> Any:
        return Notification.objects.filter(user=connection.guide.user, trigger="tour.calendar_unread")

    def it_bells_the_guide_once_per_stretch_unread(unread: TourCalendarConnection):
        assert calendar_sync.tell_guides_unread(timezone.now()) == 1
        calendar_sync.tell_guides_unread(timezone.now())
        assert list(_bells(unread).values_list("title", flat=True)) == [calendar_sync.UNREAD_TITLE]
        assert "busy times may be out of date" in _bells(unread).get().body
        unread.busy_checked_at = timezone.now() - timedelta(minutes=61)
        unread.save()
        calendar_sync.tell_guides_unread(timezone.now())
        assert _bells(unread).count() == 2

    def it_bells_a_guide_never_read(connection: TourCalendarConnection):
        connection.connected_at = timezone.now() - timedelta(hours=2)
        connection.save()
        assert calendar_sync.tell_guides_unread(timezone.now()) == 1

    def it_leaves_a_fresh_or_new_connection_alone(connection: TourCalendarConnection):
        assert calendar_sync.tell_guides_unread(timezone.now()) == 0
        assert not _bells(connection).exists()

    @respx.mock
    def it_reads_the_longest_unread_guide_first(unread: TourCalendarConnection):
        fresh = TourCalendarConnectionFactory(
            guide=make_guide(), busy_checked_at=timezone.now() - timedelta(minutes=14)
        )
        never = TourCalendarConnectionFactory(guide=make_guide())
        order: list[str] = []

        def listed(request: httpx.Request) -> httpx.Response:
            order.append(request.headers["Authorization"])
            return httpx.Response(503, json={})

        TourCalendarConnection.objects.filter(pk=never.pk).update(access_token="never")
        TourCalendarConnection.objects.filter(pk=unread.pk).update(access_token="unread")
        TourCalendarConnection.objects.filter(pk=fresh.pk).update(access_token="fresh")
        respx.get(CALENDAR_LIST).mock(side_effect=listed)
        calendar_sync.run(budget=600)
        assert order == ["Bearer never", "Bearer unread", "Bearer fresh"]


def describe_events_no_booking_points_at():
    @pytest.fixture
    def written(connection: TourCalendarConnection, booking: TourBooking) -> TourCalendarSync:
        return TourCalendarSync.objects.create(
            booking=booking, connection=connection, calendar_id="primary", event_id="ev1", due=False
        )

    @respx.mock
    def it_deletes_the_event_of_a_booking_deleted_outright(
        connection: TourCalendarConnection, booking: TourBooking, written: TourCalendarSync
    ):
        # booking.delete() is what the Django admin and a shell do (tours has no admin page yet).
        booking.delete()
        assert not TourCalendarSync.objects.exists()
        deleted = respx.delete(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(204))
        report = calendar_sync.run_cleanups(timezone.now())
        assert report.done == 1 and deleted.called
        assert not TourCalendarCleanup.objects.exists()

    def it_queues_the_delete_for_a_shell_queryset_delete(written: TourCalendarSync, booking: TourBooking):
        TourBooking.objects.filter(pk=booking.pk).delete()
        assert list(TourCalendarCleanup.objects.values_list("calendar_id", "event_id")) == [("primary", "ev1")]

    def it_queues_nothing_for_a_booking_never_written(booking: TourBooking):
        TourCalendarSync.objects.create(booking=booking)
        booking.delete()
        TourBookingFactory().delete()
        assert not TourCalendarCleanup.objects.exists()

    @respx.mock
    def it_deletes_an_event_written_after_its_booking_was_deleted(
        connection: TourCalendarConnection, booking: TourBooking
    ):
        TourCalendarSync.objects.create(booking=booking)

        def deleted_meanwhile(request: httpx.Request) -> httpx.Response:
            TourBooking.objects.filter(pk=booking.pk).delete()
            return httpx.Response(200, json={"id": "late"})

        respx.post(PRIMARY).mock(side_effect=deleted_meanwhile)
        _sync()
        assert TourCalendarCleanup.objects.filter(connection=connection, event_id="late").exists()
        gone = respx.delete(f"{PRIMARY}/late").mock(return_value=httpx.Response(204))
        assert calendar_sync.run_cleanups(timezone.now()).done == 1
        assert gone.called

    @respx.mock
    def it_deletes_the_old_event_once_a_broken_holder_reconnects(
        connection: TourCalendarConnection, booking: TourBooking, written: TourCalendarSync
    ):
        TourCalendarConnection.objects.filter(pk=connection.pk).update(
            status=TourCalendarConnection.Status.BROKEN, refresh_token="", access_token=""
        )
        new_guide = make_guide(full_legal_name="Nia New")
        TourCalendarConnectionFactory(guide=new_guide, calendar_id=TOURS_CAL, access_token="access-2")
        respx.post(TOURS_EVENTS).mock(return_value=httpx.Response(200, json={"id": "moved"}))
        booking.guide = new_guide
        booking.save()
        calendar_sync.queue_booking_sync(None, booking=booking)
        _sync()
        old = respx.delete(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(204))
        assert calendar_sync.run_cleanups(timezone.now()).done == 0  # still broken: waits, untried
        assert not old.called
        respx.post(google.TOKEN_URL).mock(return_value=_token_answer())
        respx.get(CALENDAR_LIST).mock(return_value=_calendar_list())
        calendar_sync.connect_from_code(connection.guide, "code")
        assert calendar_sync.run_cleanups(timezone.now()).done == 1
        assert old.calls.last.request.headers["Authorization"] == "Bearer fresh"
        assert not TourCalendarCleanup.objects.exists()

    @respx.mock
    def it_drops_the_delete_when_the_holder_disconnects(connection: TourCalendarConnection):
        TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id="ev1")
        respx.post(google.REVOKE_URL).mock(return_value=httpx.Response(200))
        calendar_sync.disconnect(connection)
        assert not TourCalendarCleanup.objects.exists()

    @respx.mock
    def it_drops_the_delete_when_the_holder_reconnects_another_account(connection: TourCalendarConnection):
        TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id="ev1")
        respx.post(google.TOKEN_URL).mock(return_value=_token_answer())
        respx.get(CALENDAR_LIST).mock(
            return_value=_calendar_list({"id": "other@example.com", "summary": "Other", "primary": True})
        )
        calendar_sync.connect_from_code(connection.guide, "code")
        assert not TourCalendarCleanup.objects.exists()

    @respx.mock
    def it_retries_a_failed_delete_later_and_counts_it_stuck(connection: TourCalendarConnection):
        cleanup = TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id="ev1")
        respx.delete(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(503, json={}))
        now = timezone.now()
        assert calendar_sync.run_cleanups(now).failed == 1
        cleanup.refresh_from_db()
        assert (cleanup.attempts, cleanup.next_attempt_at) == (1, now + timedelta(minutes=15))
        assert calendar_sync.run_cleanups(now).failed == 0  # waiting its turn
        TourCalendarCleanup.objects.filter(pk=cleanup.pk).update(attempts=calendar_sync.STUCK_ATTEMPTS)
        assert calendar_sync.stuck_problems(now)[0].startswith("1 tour event write(s)")
        assert str(cleanup) == "Delete ev1 from primary"

    @respx.mock
    def it_breaks_the_holder_when_google_refuses_the_delete(connection: TourCalendarConnection):
        TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id="ev1")
        respx.post(google.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
        respx.delete(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(401, json={}))
        calendar_sync.run_cleanups(timezone.now())
        connection.refresh_from_db()
        assert connection.is_broken and TourCalendarCleanup.objects.exists()

    def it_stops_when_the_run_stops(connection: TourCalendarConnection):
        for event in ("a1", "a2"):
            TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id=event)
        guard = calendar_sync.RunGuard()
        guard.failures = calendar_sync.BREAKER_FAILURES
        assert calendar_sync.run_cleanups(timezone.now(), guard).deferred == 2
        assert TourCalendarCleanup.objects.count() == 2


def describe_a_tour_back_on_the_calendar_it_left():
    def _break(connection: TourCalendarConnection) -> None:
        TourCalendarConnection.objects.filter(pk=connection.pk).update(
            status=TourCalendarConnection.Status.BROKEN, refresh_token="", access_token=""
        )

    def _reconnect(connection: TourCalendarConnection) -> None:
        TourCalendarConnection.objects.filter(pk=connection.pk).update(
            status=TourCalendarConnection.Status.CONNECTED,
            refresh_token="r",
            access_token="access-1",
            access_token_expires_at=timezone.now() + timedelta(hours=1),
        )

    def _there_and_back(guide: Member, booking: TourBooking) -> None:
        other = make_guide(full_legal_name="Nia New")  # no calendar
        booking.guide = other
        booking.save()
        calendar_sync.queue_booking_sync(None, booking=booking)
        _sync()
        booking.guide = guide
        booking.save()
        calendar_sync.queue_booking_sync(None, booking=booking)

    @respx.mock
    def it_keeps_the_event_live_when_a_broken_guide_gets_the_tour_back(
        connection: TourCalendarConnection, guide: Member, booking: TourBooking
    ):
        ours = calendar_sync.event_id_for(booking)
        TourCalendarSync.objects.create(
            booking=booking, connection=connection, calendar_id="primary", event_id=ours, due=False
        )
        _break(connection)
        _there_and_back(guide, booking)
        assert TourCalendarCleanup.objects.filter(connection=connection, event_id=ours).exists()
        _reconnect(connection)
        calendar_sync.queue_upcoming(guide, now=timezone.now())
        respx.post(PRIMARY).mock(return_value=httpx.Response(409, json={}))
        put = respx.put(f"{PRIMARY}/{ours}").mock(return_value=httpx.Response(200, json={"id": ours}))
        deleted = respx.delete(f"{PRIMARY}/{ours}").mock(return_value=httpx.Response(204))
        respx.get(CALENDAR_LIST).mock(return_value=_calendar_list())
        respx.get(PRIMARY).mock(return_value=httpx.Response(200, json={"items": []}))
        calendar_sync.run(budget=600)
        assert put.called and TourCalendarSync.objects.get(booking=booking).event_id == ours
        assert not deleted.called
        assert not TourCalendarCleanup.objects.exists()

    @respx.mock
    def it_keeps_it_live_after_a_refused_delete_too(
        connection: TourCalendarConnection, guide: Member, booking: TourBooking
    ):
        ours = calendar_sync.event_id_for(booking)
        TourCalendarSync.objects.create(
            booking=booking, connection=connection, calendar_id="primary", event_id=ours, due=False
        )
        respx.delete(f"{PRIMARY}/{ours}").mock(
            return_value=httpx.Response(403, json={"error": {"errors": [{"reason": "forbidden"}]}})
        )
        _there_and_back(guide, booking)
        connection.refresh_from_db()
        assert connection.is_broken
        assert TourCalendarCleanup.objects.filter(connection=connection, event_id=ours).exists()
        _reconnect(connection)
        respx.post(PRIMARY).mock(return_value=httpx.Response(200, json={"id": ours}))
        _sync()
        assert not TourCalendarCleanup.objects.exists()

    def it_drops_a_queued_delete_a_booking_points_at_again(connection: TourCalendarConnection, booking: TourBooking):
        TourCalendarSync.objects.create(
            booking=booking, connection=connection, calendar_id="primary", event_id="ev1", due=False
        )
        TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id="ev1")
        with respx.mock(assert_all_called=False) as mock:
            counts = calendar_sync.run_cleanups(timezone.now())
        assert not mock.calls and counts.done == 0
        assert not TourCalendarCleanup.objects.exists()

    def it_queues_one_delete_per_event(connection: TourCalendarConnection):
        calendar_sync.queue_cleanup(connection, "primary", "ev1")
        calendar_sync.queue_cleanup(connection, "primary", "ev1")
        calendar_sync.queue_cleanup(connection, TOURS_CAL, "ev1")
        assert TourCalendarCleanup.objects.count() == 2


def describe_a_big_calendar_and_the_breaker():
    def it_does_not_count_a_guides_own_deadline_against_the_run(monkeypatch):
        fake = {"t": 1000.0}
        monkeypatch.setattr(calendar_sync.clock, "monotonic", lambda: fake["t"])
        for n in range(4):
            TourCalendarConnectionFactory(guide=make_guide(full_legal_name=f"Big {n}"))

        def page(request: httpx.Request) -> httpx.Response:
            fake["t"] += 9.0
            return httpx.Response(200, json={"items": [], "nextPageToken": "more"})

        with respx.mock(assert_all_called=False) as mock:
            mock.get(CALENDAR_LIST).mock(return_value=_calendar_list())
            mock.get(PRIMARY).mock(side_effect=page)
            report = calendar_sync.run(budget=10_000)
        assert (report.read_failed, report.reads_deferred, report.stopped) == (4, 0, "")

    def it_still_counts_a_real_timeout(connection: TourCalendarConnection, booking: TourBooking):
        guard = calendar_sync.RunGuard()
        with respx.mock(assert_all_called=False) as mock:
            mock.post(PRIMARY).mock(side_effect=httpx.ReadTimeout("slow"))
            TourCalendarSync.objects.create(booking=booking)
            calendar_sync.sync_due_bookings(timezone.now(), guard)
        assert guard.failures == 1


def describe_two_runs_and_one_queued_delete():
    @pytest.fixture
    def queued(connection: TourCalendarConnection) -> TourCalendarCleanup:
        return TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id="ev1")

    @respx.mock
    def it_puts_the_event_back_when_a_write_lands_while_the_delete_is_in_flight(
        connection: TourCalendarConnection, booking: TourBooking, queued: TourCalendarCleanup
    ):
        row = TourCalendarSync.objects.create(booking=booking, due=False)
        generation = row.generation

        def other_run_writes_it_meanwhile(request: httpx.Request) -> httpx.Response:
            # Run A passed its check under the claim; run B's write lands before A's DELETE does.
            calendar_sync._place(row, connection, "primary", "ev1")
            return httpx.Response(204)

        deleted = respx.delete(f"{PRIMARY}/ev1").mock(side_effect=other_run_writes_it_meanwhile)
        assert calendar_sync.run_cleanups(timezone.now()).done == 1
        assert deleted.called
        row.refresh_from_db()
        assert row.due and row.generation == generation + 1 and row.event_id == "ev1"
        assert not TourCalendarCleanup.objects.exists()
        put = respx.put(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(200, json={"id": "ev1"}))
        _sync()
        assert _sent(put)["status"] == "confirmed"
        assert not TourCalendarSync.objects.get(pk=row.pk).due

    def it_drops_an_unclaimed_delete_when_the_write_lands_first(
        connection: TourCalendarConnection, booking: TourBooking, queued: TourCalendarCleanup
    ):
        row = TourCalendarSync.objects.create(booking=booking, due=False)
        calendar_sync._place(row, connection, "primary", "ev1")
        assert not TourCalendarCleanup.objects.exists()
        assert not TourCalendarSync.objects.get(pk=row.pk).due

    def it_leaves_a_delete_another_run_holds(queued: TourCalendarCleanup):
        assert calendar_sync._claim_cleanup(queued.pk)
        assert not calendar_sync._claim_cleanup(queued.pk)
        with respx.mock(assert_all_called=False) as mock:
            counts = calendar_sync.run_cleanups(timezone.now())
        assert not mock.calls and counts.done == 0
        TourCalendarCleanup.objects.filter(pk=queued.pk).update(claimed_until=timezone.now() - timedelta(seconds=1))
        assert calendar_sync._claim_cleanup(queued.pk)

    def it_skips_a_delete_claimed_between_the_list_and_the_claim(queued: TourCalendarCleanup):
        with (
            patch("tours.calendar_sync._claim_cleanup", return_value=False),
            respx.mock(assert_all_called=False) as mock,
        ):
            assert calendar_sync.run_cleanups(timezone.now()).done == 0
        assert not mock.calls

    @respx.mock
    def it_lets_go_of_the_claim_whatever_happened(queued: TourCalendarCleanup):
        respx.delete(f"{PRIMARY}/ev1").mock(return_value=httpx.Response(503, json={}))
        calendar_sync.run_cleanups(timezone.now())
        queued.refresh_from_db()
        assert queued.claimed_until is None and queued.attempts == 1
        guard = calendar_sync.RunGuard()
        guard.failures = calendar_sync.BREAKER_FAILURES
        TourCalendarCleanup.objects.filter(pk=queued.pk).update(next_attempt_at=None)
        calendar_sync.run_cleanups(timezone.now(), guard)
        queued.refresh_from_db()
        assert queued.claimed_until is None


def describe_a_write_meeting_a_delete_of_its_own_event():
    @pytest.fixture
    def ours(booking: TourBooking) -> str:
        return calendar_sync.event_id_for(booking)

    @respx.mock
    def it_waits_while_another_run_is_deleting_that_event(
        connection: TourCalendarConnection, booking: TourBooking, ours: str
    ):
        cleanup = TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id=ours)
        assert calendar_sync._claim_cleanup(cleanup.pk)  # run X holds the delete and passed its check
        row = TourCalendarSync.objects.create(booking=booking)
        written = respx.post(PRIMARY).mock(return_value=httpx.Response(200, json={"id": ours}))
        now = timezone.now()
        counts = calendar_sync.sync_due_bookings(now)
        assert not written.called and counts.deferred == 1
        row.refresh_from_db()
        assert row.due and row.next_attempt_at == now + calendar_sync.IN_FLIGHT_RETRY and row.attempts == 0
        # X's DELETE lands and X removes its cleanup; the next run writes the event for good.
        TourCalendarCleanup.objects.filter(pk=cleanup.pk).delete()
        calendar_sync.sync_due_bookings(now + timedelta(minutes=2))
        assert written.called
        row.refresh_from_db()
        assert (row.due, row.event_id) == (False, ours)

    @respx.mock
    def it_takes_an_unclaimed_delete_away_before_writing(
        connection: TourCalendarConnection, booking: TourBooking, ours: str
    ):
        cleanup = TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id=ours)
        TourCalendarSync.objects.create(booking=booking)
        claims: list[bool] = []

        def write(request: httpx.Request) -> httpx.Response:
            # Run X tries to claim the delete while this write is at Google: it is already gone.
            claims.append(calendar_sync._claim_cleanup(cleanup.pk))
            return httpx.Response(200, json={"id": ours})

        respx.post(PRIMARY).mock(side_effect=write)
        assert _sync().done == 1
        assert claims == [False]
        assert not TourCalendarCleanup.objects.exists()

    def it_leaves_a_queued_delete_of_another_event_alone(connection: TourCalendarConnection, ours: str):
        other = TourCalendarCleanup.objects.create(
            connection=connection, calendar_id="primary", event_id="someone-else"
        )
        held: list[int] = []
        calendar_sync._hold_queued_delete(connection, ours, held)
        assert held == []
        other.refresh_from_db()
        assert other.claimed_until is None

    @respx.mock
    def it_keeps_the_queued_delete_when_the_write_fails_so_a_cancel_still_removes_the_event(
        connection: TourCalendarConnection, booking: TourBooking, ours: str
    ):
        # The reviewer's case: the stale event's delete is queued, the write fails, the tour is cancelled.
        TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id=ours)
        calendar_sync.mark_due(booking.pk)
        respx.post(PRIMARY).mock(return_value=httpx.Response(503, json={}))
        assert calendar_sync.sync_due_bookings(timezone.now()).failed == 1
        left = TourCalendarCleanup.objects.get(event_id=ours)
        assert left.claimed_until is None
        TourBooking.objects.filter(pk=booking.pk).update(status=TourBooking.Status.CANCELLED)
        calendar_sync.queue_booking_sync(None, booking=TourBooking.objects.get(pk=booking.pk))
        TourCalendarSync.objects.filter(booking=booking).update(next_attempt_at=None)
        calendar_sync.sync_due_bookings(timezone.now())
        deleted = respx.delete(f"{PRIMARY}/{ours}").mock(return_value=httpx.Response(204))
        assert calendar_sync.run_cleanups(timezone.now()).done == 1
        assert deleted.called and not TourCalendarCleanup.objects.exists()

    def it_releases_its_claim_when_the_run_pauses_mid_write(
        connection: TourCalendarConnection, booking: TourBooking, ours: str
    ):
        TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id=ours)
        TourCalendarSync.objects.create(booking=booking)
        with patch("tours.calendar_sync._write_event", side_effect=calendar_sync._Paused("spent")):
            assert calendar_sync.sync_due_bookings(timezone.now()).deferred == 1
        assert TourCalendarCleanup.objects.get(event_id=ours).claimed_until is None

    @respx.mock
    def it_claims_the_main_calendars_queued_delete_when_the_chosen_one_vanishes(
        connection: TourCalendarConnection, booking: TourBooking, ours: str
    ):
        connection.calendar_id = TOURS_CAL
        connection.save()
        main = TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id=ours)
        TourCalendarSync.objects.create(booking=booking)
        respx.post(TOURS_EVENTS).mock(return_value=httpx.Response(404, json={}))
        claims: list[bool] = []

        def write_on_main(request: httpx.Request) -> httpx.Response:
            claims.append(calendar_sync._claim_cleanup(main.pk))  # another run cannot take it mid-write
            return httpx.Response(200, json={"id": ours})

        respx.post(PRIMARY).mock(side_effect=write_on_main)
        assert _sync().done == 1
        assert claims == [False]
        assert not TourCalendarCleanup.objects.exists()

    @respx.mock
    def it_waits_when_another_run_holds_the_main_calendars_delete(
        connection: TourCalendarConnection, booking: TourBooking, ours: str
    ):
        connection.calendar_id = TOURS_CAL
        connection.save()
        main = TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id=ours)
        assert calendar_sync._claim_cleanup(main.pk)
        TourCalendarSync.objects.create(booking=booking)
        respx.post(TOURS_EVENTS).mock(return_value=httpx.Response(404, json={}))
        written = respx.post(PRIMARY).mock(return_value=httpx.Response(200, json={"id": ours}))
        assert _sync().deferred == 1
        assert not written.called
        assert TourCalendarSync.objects.get(booking=booking).due


def describe_a_queued_delete_that_breaks_unexpectedly():
    def it_records_it_and_carries_on_with_the_rest_of_the_run(connection: TourCalendarConnection):
        bad = TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id="bad")
        TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id="good")
        real = calendar_sync._run_cleanup

        def run_one(cleanup: TourCalendarCleanup, *args: Any) -> None:
            if cleanup.event_id == "bad":
                raise RuntimeError("")
            real(cleanup, *args)

        now = timezone.now()
        with (
            patch("tours.calendar_sync._run_cleanup", side_effect=run_one),
            respx.mock(assert_all_called=False) as mock,
        ):
            mock.delete(f"{PRIMARY}/good").mock(return_value=httpx.Response(204))
            counts = calendar_sync.run_cleanups(now)
        assert (counts.failed, counts.done) == (1, 1)
        bad.refresh_from_db()
        assert (bad.attempts, bad.last_error, bad.claimed_until) == (1, "RuntimeError", None)
        assert bad.next_attempt_at == now + calendar_sync.RETRY_BASE

    def it_still_bells_unread_guides_and_checks_what_is_stuck(connection: TourCalendarConnection):
        TourCalendarCleanup.objects.create(connection=connection, calendar_id="primary", event_id="bad")
        connection.connected_at = timezone.now() - timedelta(hours=3)
        connection.busy_checked_at = timezone.now() - timedelta(hours=2)
        connection.save()
        with (
            patch("tours.calendar_sync._run_cleanup", side_effect=RuntimeError("boom")),
            patch("tours.calendar_sync.refresh_busy", return_value=False),
        ):
            report = calendar_sync.run(budget=600)
        assert report.cleanups.failed == 1 and report.unread_told == 1 and report.stuck
