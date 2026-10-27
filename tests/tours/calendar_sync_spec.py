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
from tours.models import TourBooking, TourCalendarConnection, TourCalendarSync

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


def describe_sync_booking():
    def _row(booking: TourBooking, **fields: Any) -> TourCalendarSync:
        return TourCalendarSync.objects.create(booking=booking, **fields)

    @respx.mock
    def it_creates_the_event_on_the_guides_calendar(connection: TourCalendarConnection, booking: TourBooking):
        route = respx.post(PRIMARY).mock(return_value=httpx.Response(200, json={"id": "created"}))
        row = _row(booking)
        calendar_sync.sync_booking(row, now=timezone.now())
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
        calendar_sync.sync_booking(row, now=timezone.now())
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
            calendar_sync.sync_booking(row, now=timezone.now())
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
            calendar_sync.sync_booking(row, now=timezone.now())
        assert not mock.calls
        row.refresh_from_db()
        assert (row.event_id, row.due) == ("", False)

    def it_has_nothing_to_do_for_a_tour_with_no_guide(booking: TourBooking):
        booking.guide = None
        booking.save()
        row = _row(booking)
        calendar_sync.sync_booking(
            TourCalendarSync.objects.select_related("booking").get(pk=row.pk), now=timezone.now()
        )
        assert not TourCalendarSync.objects.get(pk=row.pk).due

    def it_waits_while_the_guides_calendar_is_broken(connection: TourCalendarConnection, booking: TourBooking):
        connection.status = TourCalendarConnection.Status.BROKEN
        connection.save()
        with pytest.raises(calendar_sync._Waiting):
            calendar_sync.sync_booking(_row(booking), now=timezone.now())

    def it_waits_while_the_calendar_holding_the_event_is_broken(booking: TourBooking):
        holder = TourCalendarConnectionFactory(guide=make_guide(), status=TourCalendarConnection.Status.BROKEN)
        row = _row(booking, connection=holder, calendar_id="primary", event_id="ev1")
        with pytest.raises(calendar_sync._Waiting):
            calendar_sync.sync_booking(row, now=timezone.now())

    @respx.mock
    def it_breaks_the_holder_when_google_refuses_the_delete(connection: TourCalendarConnection, booking: TourBooking):
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
            return_value=_calendar_list({"id": "gina.new@example.com", "summary": "Gina", "primary": True})
        )
        spans = calendar_sync.read_busy(connection, now=now, timeout=1, with_calendars=True)
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
        calendar_sync.read_busy(connection, now=timezone.now(), timeout=1, with_calendars=True)
        connection.refresh_from_db()
        assert connection.google_email == "gina@example.com"
        assert connection.calendar_choices() == [(TOURS_CAL, "Tours")]

    @respx.mock
    def it_reads_the_main_calendar_once_when_it_is_the_chosen_one(connection: TourCalendarConnection):
        route = respx.get(PRIMARY).mock(return_value=httpx.Response(200, json={"items": []}))
        calendar_sync.read_busy(connection, now=timezone.now(), timeout=1, with_calendars=False)
        assert route.call_count == 1


def describe_refresh_busy():
    @respx.mock
    def it_keeps_the_cache_when_google_fails(connection: TourCalendarConnection):
        connection.busy_spans = [["2030-01-08T18:00:00+00:00", "2030-01-08T19:00:00+00:00"]]
        connection.save()
        respx.get(CALENDAR_LIST).mock(return_value=httpx.Response(500, json={}))
        now = timezone.now()
        assert calendar_sync.refresh_busy(connection, now=now) is False
        connection.refresh_from_db()
        assert connection.busy_attempted_at == now
        assert len(connection.busy_spans) == 1

    @respx.mock
    def it_breaks_the_connection_when_google_refuses(connection: TourCalendarConnection):
        connection.access_token = ""
        connection.save()
        respx.post(google.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
        assert calendar_sync.refresh_busy(connection, now=timezone.now()) is False
        connection.refresh_from_db()
        assert connection.is_broken


def describe_refresh_stale_busy():
    @pytest.fixture
    def hours(guide: Member) -> Any:
        return TourHoursFactory(guide=guide)

    @respx.mock
    def it_reads_a_stale_calendar_of_a_guide_with_hours(connection: TourCalendarConnection, hours: Any):
        connection.busy_checked_at = timezone.now() - timedelta(minutes=16)
        connection.save()
        route = respx.get(PRIMARY).mock(return_value=httpx.Response(200, json={"items": []}))
        calendar_sync.refresh_stale_busy()
        assert route.called
        connection.refresh_from_db()
        assert connection.busy_checked_at > timezone.now() - timedelta(minutes=1)

    @pytest.mark.parametrize(
        "fields",
        [
            {"busy_checked_at": "fresh"},
            {"busy_attempted_at": "fresh"},
            {"status": TourCalendarConnection.Status.BROKEN},
        ],
    )
    def it_leaves_google_alone_when_nothing_is_due(connection: TourCalendarConnection, hours: Any, fields: dict):
        for name, value in fields.items():
            setattr(connection, name, timezone.now() - timedelta(minutes=2) if value == "fresh" else value)
        connection.save()
        with respx.mock(assert_all_called=False) as mock:
            calendar_sync.refresh_stale_busy()
        assert not mock.calls

    def it_skips_a_guide_with_no_hours(connection: TourCalendarConnection):
        with respx.mock(assert_all_called=False) as mock:
            calendar_sync.refresh_stale_busy()
        assert not mock.calls

    def it_stops_when_its_time_is_spent(connection: TourCalendarConnection, hours: Any, monkeypatch):
        monkeypatch.setattr(calendar_sync, "ON_DEMAND_BUDGET_SECONDS", 0)
        with respx.mock(assert_all_called=False) as mock:
            calendar_sync.refresh_stale_busy()
        assert not mock.calls

    def it_does_nothing_until_google_is_set_up(connection: TourCalendarConnection, hours: Any, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = ""
        with respx.mock(assert_all_called=False) as mock:
            calendar_sync.refresh_stale_busy()
        assert not mock.calls


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
        assert "Busy times read for 1 guide(s), 0 failed; 1 booked tour(s) over a busy time" in out.getvalue()
        assert "events written 1" in out.getvalue()
        assert TourCalendarSync.objects.get(booking=booking).event_id == "ev"

    @respx.mock
    def it_counts_a_failed_read(connection: TourCalendarConnection):
        respx.get(CALENDAR_LIST).mock(return_value=httpx.Response(500, json={}))
        report = calendar_sync.run()
        assert (report.read, report.read_failed) == (0, 1)

    def it_skips_a_calendar_read_moments_ago(connection: TourCalendarConnection):
        connection.busy_checked_at = timezone.now() - timedelta(minutes=5)
        connection.save()
        with respx.mock(assert_all_called=False) as mock:
            report = calendar_sync.run()
        assert not mock.calls and report.read == 0

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
            "1 connected calendar(s) have not been read for over an hour",
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
