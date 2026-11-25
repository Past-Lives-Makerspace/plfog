"""Days Off, Google Calendar and Booking Notice on My Hours (#1300, part 4c)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import httpx
import pytest
import respx
from django.urls import reverse
from django.utils import timezone

from hours.days_off import DayOffForm, remove_day_off, still_booked
from hours.models import DayOff
from membership.models import Member, OrientationBooking, OrientationDayOff
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourCalendarConnectionFactory, TourDayOffFactory
from tours import google
from tours.models import TourCalendarConnection, TourDayOff
from tours.views import GOOGLE_RETURN_SESSION_KEY, GOOGLE_STATE_SESSION_KEY

pytestmark = pytest.mark.django_db

PAGE = reverse("hours:my_hours")
SAVE = reverse("hours:day_off_save")
PREVIEW = reverse("hours:day_off_preview")


def _day(days: int) -> date:
    return timezone.localdate() + timedelta(days=days)


def _at(day: date, hour: int) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour)))


def _orientor() -> Member:
    person = MemberFactory(full_legal_name="Olive Orientor")
    OrientationTypeFactory(guild=GuildFactory(guild_lead=person), name="Wheel")
    return person


def _booked_day(person: Member, day: date) -> None:
    """A tour of three and two orientations (one confirmed with Sam, one requested) on ``day``."""
    TourBookingFactory(guide=person, starts_at=_at(day, 13), ends_at=_at(day, 14), party_size=3)
    kind = OrientationTypeFactory(guild=GuildFactory(), name="Complete Beginner")
    slot = OrientationSlotFactory(orienter=person, orientation_type=kind, starts_at=_at(day, 15), ends_at=_at(day, 16))
    sam = MemberFactory(preferred_name="Sam Kim")
    OrientationBookingFactory(slot=slot, member=sam, status=OrientationBooking.Status.CONFIRMED)
    asked = OrientationSlotFactory(orienter=person, orientation_type=kind, starts_at=_at(day, 17), ends_at=_at(day, 18))
    OrientationBookingFactory(slot=asked, status=OrientationBooking.Status.REQUESTED)


def describe_still_booked():
    def it_lists_what_stays_booked_in_words():
        person = make_guide()
        day = _day(3)
        _booked_day(person, day)
        assert still_booked(person, day, day) == [
            "Tour at 1:00 PM, 3 visitors",
            "Complete Beginner at 3:00 PM with Sam K.",
            "Complete Beginner at 5:00 PM (requested)",
        ]

    def it_names_the_day_over_a_run_and_counts_a_full_slot():
        person = make_guide()
        day = _day(3)
        slot = OrientationSlotFactory(orienter=person, starts_at=_at(day, 15), ends_at=_at(day, 16), seats=3)
        for _ in range(2):
            OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        lines = still_booked(person, day - timedelta(days=1), day)
        assert lines == [f"{day:%a, %b} {day.day}: Orientation at 3:00 PM with 2 people"]

    def it_says_a_checkout_is_in_progress():
        person = make_guide()
        day = _day(3)
        slot = OrientationSlotFactory(orienter=person, starts_at=_at(day, 15), ends_at=_at(day, 16))
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT)
        assert still_booked(person, day, day) == ["Orientation at 3:00 PM (a checkout in progress)"]


def describe_add_day_off():
    def it_saves_a_run_of_days_for_every_kind_and_keeps_bookings():
        person = _orientor()
        _booked_day(person, _day(4))
        response = signed_in(person).post(
            SAVE,
            {"member": person.pk, "day_off-starts_on": _day(4), "day_off-ends_on": _day(6), "day_off-note": "Away"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 204 and response["HX-Redirect"] == PAGE
        assert list(DayOff.objects.values_list("starts_on", "ends_on", "note")) == [(_day(4), _day(6), "Away")]
        assert OrientationBooking.objects.filter(status=OrientationBooking.Status.CONFIRMED).count() == 1

    def it_saves_one_day_without_htmx():
        person = _orientor()
        response = signed_in(person).post(SAVE, {"member": person.pk, "day_off-starts_on": _day(2)})
        assert response.status_code == 302
        assert DayOff.objects.get().ends_on == _day(2)

    @pytest.mark.parametrize(
        ("first", "last", "message"),
        [
            (-1, None, "Pick today or a day ahead."),
            (3, 2, "The last day must be on or after the first."),
            (1, 400, "Pick a run of a year or less."),
            (5, 5, "Part of that is already off"),
        ],
    )
    def it_refuses_a_bad_day_off_in_the_modal(first: int, last: int | None, message: str):
        person = _orientor()
        TourDayOffFactory(guide=person, starts_on=_day(5), ends_on=_day(5))
        data = {"member": person.pk, "day_off-starts_on": _day(first)}
        if last is not None:
            data["day_off-ends_on"] = _day(last)
        response = signed_in(person).post(SAVE, data, HTTP_HX_REQUEST="true")
        assert response.status_code == 200 and message in response.content.decode()
        assert DayOff.objects.count() == 1

    def it_reopens_the_modal_without_htmx():
        person = _orientor()
        response = signed_in(person).post(SAVE, {"member": person.pk, "day_off-starts_on": _day(-2)})
        assert response.status_code == 200 and b"open-modal', 'hours-day-off-modal'" in response.content

    def it_refuses_someone_elses_day_off_unless_admin(admin: Member):
        person, other = _orientor(), _orientor()
        data = {"member": other.pk, "day_off-starts_on": _day(2)}
        assert signed_in(person).post(SAVE, data).status_code == 403
        assert signed_in(admin).post(SAVE, data).status_code == 302
        assert DayOff.objects.get().member == other

    def it_previews_what_stays_booked():
        person = make_guide()
        _booked_day(person, _day(3))
        client = signed_in(person)
        one = client.get(PREVIEW, {"member": person.pk, "day_off-starts_on": _day(3)}).content.decode()
        assert "3 bookings that day stay booked:" in one and "Tour at 1:00 PM, 3 visitors" in one
        run = client.get(PREVIEW, {"member": person.pk, "day_off-starts_on": _day(1), "day_off-ends_on": _day(4)})
        assert "on those days stay booked" in run.content.decode()
        none = client.get(PREVIEW, {"member": person.pk, "day_off-starts_on": _day(9), "day_off-ends_on": "x"})
        assert "Nothing is booked that day." in none.content.decode()
        blank = client.get(PREVIEW, {"member": person.pk, "day_off-starts_on": "nope"})
        assert "data-hours-day-off-impact" not in blank.content.decode()

    def it_reads_no_dates_from_an_unbound_form():
        assert DayOffForm(member=MemberFactory()).chosen() is None


def describe_delete_day_off():
    def it_deletes_the_range_and_the_orientation_days_it_covers():
        person = _orientor()
        TourDayOffFactory(guide=person, starts_on=_day(3), ends_on=_day(5))
        OrientationDayOff.objects.create(orienter=person, date=_day(4))
        OrientationDayOff.objects.create(orienter=person, date=_day(9))
        day_off = DayOff.objects.get(starts_on=_day(3))
        response = signed_in(person).post(reverse("hours:day_off_delete", args=[day_off.pk]))
        assert response.status_code == 302
        assert not TourDayOff.objects.exists()
        assert list(DayOff.objects.values_list("starts_on", flat=True)) == [_day(9)]

    def it_deletes_a_single_orientation_day():
        person = _orientor()
        OrientationDayOff.objects.create(orienter=person, date=_day(4))
        remove_day_off(DayOff.objects.get())
        assert not DayOff.objects.exists() and not OrientationDayOff.objects.exists()

    def it_refuses_someone_elses():
        person, other = _orientor(), _orientor()
        TourDayOffFactory(guide=other)
        day_off = DayOff.objects.get()
        assert signed_in(person).post(reverse("hours:day_off_delete", args=[day_off.pk])).status_code == 403
        assert DayOff.objects.exists()


@pytest.mark.usefixtures("google_on")
def describe_google_calendar_for_orientors():
    def it_offers_connect_and_lands_back_on_my_hours():
        person = _orientor()
        client = signed_in(person)
        assert "data-hours-gcal-connect" in client.get(PAGE).content.decode()
        response = client.get(reverse("tours:google_connect"), {"from": "hours"})
        assert response.status_code == 302 and client.session[GOOGLE_RETURN_SESSION_KEY] == "hours:my_hours"

    @respx.mock
    def it_connects_an_orientor_and_says_it_hides_their_bookings():
        respx.post(google.TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "at",
                    "refresh_token": "rt",
                    "expires_in": 3600,
                    "scope": " ".join(google.SCOPES),
                },
            )
        )
        respx.get(f"{google.API_URL}/users/me/calendarList").mock(
            return_value=httpx.Response(200, json={"items": [{"id": "o@example.com", "summary": "O", "primary": True}]})
        )
        person = _orientor()
        client = signed_in(person)
        session = client.session
        session[GOOGLE_STATE_SESSION_KEY] = "s"
        session[GOOGLE_RETURN_SESSION_KEY] = "hours:my_hours"
        session.save()
        response = client.get(reverse("tours:google_callback"), {"state": "s", "code": "c"}, follow=True)
        assert response.redirect_chain[-1][0] == PAGE
        messages = [str(message) for message in response.context["messages"]]
        assert "Google Calendar connected. Times it shows you busy are not offered to anyone booking you." in messages
        assert TourCalendarConnection.objects.get().guide == person

    def it_disconnects_back_to_my_hours():
        person = _orientor()
        TourCalendarConnectionFactory(guide=person)
        client = signed_in(person)
        page = client.get(PAGE).content.decode()
        assert "data-hours-gcal-disconnect" in page and 'data-autosave-key="gcal"' not in page
        with respx.mock(assert_all_called=False) as mock:
            mock.post(google.REVOKE_URL).mock(return_value=httpx.Response(200))
            response = client.post(reverse("tours:google_disconnect"), {"from": "hours"})
        assert response["Location"] == PAGE
        assert not TourCalendarConnection.objects.exists()

    def it_shows_a_guide_the_calendar_picker_and_a_broken_calendar_reconnect():
        guide = make_guide()
        TourCalendarConnectionFactory(guide=guide)
        assert 'data-autosave-key="gcal"' in signed_in(guide).get(PAGE).content.decode()
        TourCalendarConnection.objects.update(status=TourCalendarConnection.Status.BROKEN, broken_at=timezone.now())
        assert "data-hours-gcal-reconnect" in signed_in(guide).get(PAGE).content.decode()

    def it_still_sends_a_member_with_no_hours_page_to_give_tours(plain: Member):
        assert signed_in(plain).get(reverse("tours:google_connect"))["Location"] == reverse("tours:give")


def describe_booking_notice():
    def it_lets_a_guide_set_their_notice_here():
        guide = make_guide()
        page = signed_in(guide).get(PAGE).content.decode()
        assert "data-hours-notice" in page and 'name="min_notice_minutes"' in page


def describe_still_booked_queries():
    def it_reads_the_same_number_of_queries_for_one_booked_slot_or_three(django_assert_num_queries):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        person = make_guide()
        day = _day(3)
        _booked_day(person, day)
        with CaptureQueriesContext(connection) as one:
            still_booked(person, day, day)
        for hour in (9, 10):
            slot = OrientationSlotFactory(orienter=person, starts_at=_at(day, hour), ends_at=_at(day, hour + 1))
            OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        with django_assert_num_queries(len(one.captured_queries)):
            assert len(still_booked(person, day, day)) == 5
