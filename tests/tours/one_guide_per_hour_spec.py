"""Each tour hour belongs to one guide, visitors pick a time, and no booking waits for a Confirm (#1309).

Brandon's feedback requests #29, #30 and #31: new or edited hours that run into another guide's
are refused naming whose they are; where hours posted before overlap, visitors see the earlier
posted guide's tour only; the No preference picker and pool sit behind Tour Settings'
``guides_share_hours`` (off); Confirm First is gone and a request still waiting is booked.
"""

from __future__ import annotations

from datetime import UTC, date, time, timedelta
from importlib import import_module
from typing import Any

import pytest
from django.apps import apps
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from membership.models import AdminCapability, Member
from tests.tours.conftest import let_guides_share_hours, make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourDayOffFactory, TourHoursFactory, local
from tours.availability import open_tour_at, open_tours
from tours.booking import TourUnavailableError, Visitor, book_any_guide, book_tour, book_waiting_requests
from tours.models import FacilityTourSettings, TourBooking, TourGuideSettings, TourHours
from tours.tasks import book_waiting_tour_requests

pytestmark = pytest.mark.django_db

_migration = import_module("tours.migrations.0011_one_guide_per_hour")

# 2030-01-07 is a Monday; tours run Tuesday the 8th, 4:00 and 5:00 PM.
NOW = local(date(2030, 1, 7), 9)
TUESDAY = date(2030, 1, 8)
FOUR = local(TUESDAY, 16)
SAVE = reverse("tours:hours_save")
DATES = reverse("tours:dates_save")
AUTOSAVE = {"HTTP_X_AUTOSAVE": "1"}
HOST = "tours.pastlives.space"
TAKEN = "Each tour hour belongs to one guide, so pick a time nobody has."


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    cache.clear()


@pytest.fixture
def amber() -> Member:
    """The guide who posted first: Tuesday 4:00 to 6:00 PM, every week."""
    member = make_guide(full_legal_name="Amber Capwell")
    TourHoursFactory(guide=member)
    return member


@pytest.fixture
def dayzie() -> Member:
    return make_guide(full_legal_name="Dayzie Dee")


def _hours_post(guide: Member, *rows: dict[str, str], initial: int = 0) -> dict[str, str]:
    data = {
        "guide": str(guide.pk),
        "hours-TOTAL_FORMS": str(len(rows)),
        "hours-INITIAL_FORMS": str(initial),
        "hours-MIN_NUM_FORMS": "0",
        "hours-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        values = {"weekday": "1", "start_time": "16:00", "end_time": "18:00", "tour_minutes": "60", "seats": "4"}
        values.update(row)
        data.update({f"hours-{index}-{name}": value for name, value in values.items()})
    return data


def _next(weekday: int) -> date:
    """The next local day with ``weekday`` after today."""
    today = timezone.localdate()
    return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)


def _visitor(party: int = 2) -> Visitor:
    return Visitor(name="Sam Rivera", email="sam@example.com", phone="", party_size=party)


def _starts(tours: list[Any]) -> list[tuple[str, int]]:
    return [(tour.guide.greeting_name, timezone.localtime(tour.starts_at).hour) for tour in tours]


def describe_posting_hours_another_guide_has():
    def it_refuses_a_new_weekly_row_naming_whose_hours_they_are(amber: Member, dayzie: Member):
        data = _hours_post(dayzie, {"start_time": "17:00", "end_time": "19:00"})
        response = signed_in(dayzie).post(SAVE, data, **AUTOSAVE)
        assert response.status_code == 422
        assert response.json()["errors"] == {
            "hours-0-__all__": [f"Every Tuesday, 4:00 PM to 6:00 PM is Amber's. {TAKEN}"]
        }
        assert not TourHours.objects.filter(guide=dayzie).exists()

    def it_refuses_a_weekly_row_over_another_guides_upcoming_date(dayzie: Member):
        saturday = _next(5)
        lee = make_guide(full_legal_name="Lee Long")
        TourHoursFactory(guide=lee, weekday=5, date=saturday, start_time=time(12), end_time=time(14))
        TourHoursFactory(
            guide=lee, weekday=5, date=saturday - timedelta(days=7), start_time=time(12), end_time=time(14)
        )
        data = _hours_post(dayzie, {"weekday": "5", "start_time": "13:00", "end_time": "15:00"})
        errors = signed_in(dayzie).post(SAVE, data, **AUTOSAVE).json()["errors"]
        label = f"{saturday:%a, %b} {saturday.day}"
        # The date already past holds nothing; only the upcoming one is named.
        assert errors == {"hours-0-__all__": [f"{label}, 12:00 PM to 2:00 PM is Lee's. {TAKEN}"]}

    def it_saves_hours_beside_another_guides(amber: Member, dayzie: Member):
        data = _hours_post(dayzie, {"start_time": "18:00", "end_time": "20:00"}, {"weekday": "2"})
        assert signed_in(dayzie).post(SAVE, data, **AUTOSAVE).status_code == 200
        assert TourHours.objects.filter(guide=dayzie).count() == 2

    def it_keeps_saving_a_row_that_overlapped_before_until_its_times_change(amber: Member, dayzie: Member):
        # Posted before #1309, when guides could share hours.
        kept = TourHoursFactory(guide=dayzie, start_time=time(17), end_time=time(19))
        client = signed_in(dayzie)
        row = {"id": str(kept.pk), "start_time": "17:00", "end_time": "19:00", "seats": "6"}
        assert client.post(SAVE, _hours_post(dayzie, row, initial=1), **AUTOSAVE).status_code == 200
        kept.refresh_from_db()
        assert kept.seats == 6
        moved = {**row, "end_time": "20:00"}
        response = client.post(SAVE, _hours_post(dayzie, moved, initial=1), **AUTOSAVE)
        assert response.status_code == 422
        assert response.json()["errors"]["hours-0-__all__"] == [
            f"Every Tuesday, 4:00 PM to 6:00 PM is Amber's. {TAKEN}"
        ]

    def it_never_counts_hours_of_someone_who_no_longer_gives_tours(amber: Member, dayzie: Member):
        AdminCapability.objects.filter(member=amber).delete()
        data = _hours_post(dayzie, {})
        assert signed_in(dayzie).post(SAVE, data, **AUTOSAVE).status_code == 200

    def it_lets_guides_share_hours_while_the_switch_is_on(amber: Member, dayzie: Member):
        let_guides_share_hours()
        assert signed_in(dayzie).post(SAVE, _hours_post(dayzie, {}), **AUTOSAVE).status_code == 200

    def it_refuses_a_picked_date_inside_another_guides_weekly_hours(amber: Member, dayzie: Member):
        tuesday, wednesday = _next(1), _next(2)
        data = {
            "guide": str(dayzie.pk),
            "dates": [tuesday.isoformat(), wednesday.isoformat()],
            "start_time": "15:00",
            "end_time": "17:00",
            "tour_minutes": "60",
            "seats": "4",
        }
        page = signed_in(dayzie).post(DATES, data).content.decode()
        label = f"{tuesday:%a, %b} {tuesday.day}"
        assert f"{label}: Every Tuesday, 4:00 PM to 6:00 PM is Amber&#x27;s." in page
        assert not TourHours.objects.filter(guide=dayzie).exists()

    def it_locks_tour_settings_before_the_guide_so_two_guides_saving_queue(dayzie: Member):
        if connection.vendor != "postgresql":
            pytest.skip("SELECT ... FOR NO KEY UPDATE is PostgreSQL's")
        with CaptureQueriesContext(connection) as queries:
            signed_in(dayzie).post(SAVE, _hours_post(dayzie, {}), **AUTOSAVE)
        locks = [query["sql"] for query in queries.captured_queries if "FOR NO KEY UPDATE" in query["sql"]]
        assert '"tours_facilitytoursettings"' in locks[0] and '"membership_member"' in locks[1]


def describe_hours_that_overlapped_before():
    @pytest.fixture
    def both(amber: Member, dayzie: Member) -> tuple[Member, Member]:
        """Dayzie's Tuesday 5:00 to 7:00 PM, posted after Amber's 4:00 to 6:00 PM."""
        TourHoursFactory(guide=dayzie, start_time=time(17), end_time=time(19))
        return amber, dayzie

    def it_shows_visitors_the_earlier_posted_guides_tour_only(both: tuple[Member, Member]):
        assert _starts(open_tours(TUESDAY, TUESDAY, now=NOW)) == [
            ("Amber Capwell", 16),
            ("Amber Capwell", 17),
            ("Dayzie Dee", 18),
        ]

    def it_shows_staff_every_guides_tours(both: tuple[Member, Member]):
        assert len(open_tours(TUESDAY, TUESDAY, now=NOW, staff=True)) == 4

    def it_shows_both_while_guides_may_share_hours(both: tuple[Member, Member]):
        let_guides_share_hours()
        assert len(open_tours(TUESDAY, TUESDAY, now=NOW)) == 4

    def it_holds_the_time_for_its_owner_even_when_full(both: tuple[Member, Member]):
        amber, _dayzie = both
        TourBookingFactory(guide=amber, starts_at=local(TUESDAY, 17), party_size=4)
        assert _starts(open_tours(TUESDAY, TUESDAY, now=NOW)) == [("Amber Capwell", 16), ("Dayzie Dee", 18)]

    def it_frees_the_time_on_a_day_the_owner_is_off(both: tuple[Member, Member]):
        amber, _dayzie = both
        TourDayOffFactory(guide=amber, starts_on=TUESDAY, ends_on=TUESDAY)
        assert _starts(open_tours(TUESDAY, TUESDAY, now=NOW)) == [("Dayzie Dee", 17), ("Dayzie Dee", 18)]

    def it_lists_one_guide_as_visitors_see_them(both: tuple[Member, Member]):
        _amber, dayzie = both
        assert _starts(open_tours(TUESDAY, TUESDAY, guide=dayzie, now=NOW)) == [("Dayzie Dee", 18)]

    def it_refuses_a_visitor_booking_the_shadowed_tour_and_lets_staff_pick_it(both: tuple[Member, Member]):
        amber, dayzie = both
        five = local(TUESDAY, 17)
        assert open_tour_at(dayzie, five, now=NOW) is None
        assert open_tour_at(dayzie, five, now=NOW, staff=True) is not None
        assert open_tour_at(amber, five, now=NOW) is not None
        with pytest.raises(TourUnavailableError):
            book_tour(guide=dayzie, starts_at=five, visitor=_visitor(), now=NOW)


def describe_the_booking_page():
    def it_shows_one_tour_per_time_with_no_guide_menu(amber: Member, dayzie: Member):
        day = _next(1)
        TourHoursFactory(guide=dayzie)  # the very same hours, posted before #1309
        page = Client(HTTP_HOST=HOST).get(f"/?date={day:%Y-%m-%d}").content.decode()
        assert page.count("data-tour-time=") == 2 and "data-tour-no-preference" not in page
        anyone = f"any-{local(day, 16).astimezone(UTC):%Y%m%dT%H%MZ}"
        form = Client(HTTP_HOST=HOST).get(f"/?tour={anyone}").content.decode()
        assert "data-tour-guide" not in form and "No preference" not in form

    def it_books_a_stale_no_preference_key_with_the_guide_who_owns_the_time(amber: Member, dayzie: Member):
        TourHoursFactory(guide=dayzie)
        assert book_any_guide(starts_at=FOUR, visitor=_visitor(), now=NOW).guide == amber


def describe_the_switch():
    def it_is_off_by_default_and_on_the_settings_tab_for_admins(admin: Member):
        assert FacilityTourSettings.load().guides_share_hours is False
        page = signed_in(admin).get(reverse("tours:settings")).content.decode()
        assert "data-tour-settings-share" in page and "Let guides share hours" in page

    def it_saves_from_the_settings_tab(admin: Member):
        data = {
            "accepting_bookings": "on",
            "closed_message": "",
            "booking_horizon_days": "30",
            "min_notice_hours": "24",
            "cancel_cutoff_hours": "24",
            "guides_share_hours": "on",
            "default_tour_minutes": "60",
            "default_seats": "6",
        }
        assert signed_in(admin).post(reverse("tours:settings"), data, **AUTOSAVE).status_code == 200
        assert FacilityTourSettings.load().guides_share_hours is True


def describe_no_confirm_step():
    @pytest.fixture
    def request_(amber: Member) -> TourBooking:
        """A request Amber never answered, from when she was on Confirm First."""
        signed_in(amber)
        amber.refresh_from_db()
        starts = local(_next(1) + timedelta(days=7), 16)
        return TourBookingFactory(
            guide=amber,
            starts_at=starts,
            visitor_email="sam@example.com",
            status=TourBooking.Status.PENDING,
        )

    def it_books_a_waiting_request_with_the_confirmation_and_invite(
        request_: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        with django_capture_on_commit_callbacks(execute=True):
            assert book_waiting_requests() == [request_]
        request_.refresh_from_db()
        assert request_.status == TourBooking.Status.BOOKED
        sent = [message for message in mail.outbox if message.to == ["sam@example.com"]]
        assert len(sent) == 1 and "is confirmed" in sent[0].subject
        assert "METHOD:REQUEST" in sent[0].attachments[0].get_content()

    def it_is_safe_to_run_twice(request_: TourBooking):
        book_waiting_requests()
        assert book_waiting_requests() == []

    def it_leaves_a_started_request_and_one_whose_guide_has_no_account(amber: Member):
        started = TourBookingFactory(
            guide=amber, starts_at=timezone.now() - timedelta(hours=1), status=TourBooking.Status.PENDING
        )
        nobody = make_guide(full_legal_name="No Account")
        waiting = TourBookingFactory(guide=nobody, status=TourBooking.Status.PENDING)
        assert book_waiting_requests() == []
        assert {row.status for row in TourBooking.objects.filter(pk__in=[started.pk, waiting.pk])} == {
            TourBooking.Status.PENDING
        }

    def it_books_a_shared_request_once_and_tells_the_other_guide_it_was_taken(amber: Member, dayzie: Member):
        signed_in(amber)
        signed_in(dayzie)
        group = "8f9c2a5e-3d0c-4f7a-9a65-6d1f0c3b2a10"
        starts = local(_next(1) + timedelta(days=7), 16)
        first = TourBookingFactory(
            guide=amber, starts_at=starts, status=TourBooking.Status.PENDING, request_group=group
        )
        second = TourBookingFactory(
            guide=dayzie, starts_at=starts, status=TourBooking.Status.PENDING, request_group=group
        )
        assert book_waiting_requests() == [first]
        second.refresh_from_db()
        assert second.status == TourBooking.Status.CANCELLED and second.was_taken_by_another_guide()

    def it_moves_every_guide_to_automatic_and_queues_the_booking(
        request_: TourBooking, amber: Member, settings: Any, django_capture_on_commit_callbacks: Any
    ):
        settings.TASKS = {"default": {"BACKEND": "django.tasks.backends.dummy.DummyBackend"}}
        book_waiting_tour_requests.get_backend().clear()
        TourGuideSettings.objects.create(guide=amber, confirm_mode=TourGuideSettings.ConfirmMode.FIRST)
        with django_capture_on_commit_callbacks(execute=True):
            _migration.everyone_confirms_automatically(apps, None)
        assert not TourGuideSettings.confirms_first(amber)
        (queued,) = book_waiting_tour_requests.get_backend().results
        assert queued.task.func is book_waiting_tour_requests.func
        book_waiting_tour_requests.call()
        request_.refresh_from_db()
        assert request_.status == TourBooking.Status.BOOKED

    def it_queues_nothing_with_no_request_waiting(amber: Member, django_capture_on_commit_callbacks: Any):
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            _migration.everyone_confirms_automatically(apps, None)
        assert callbacks == []

    def it_books_a_website_visitor_at_once(amber: Member):
        booking = book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(), now=NOW)
        assert booking.status == TourBooking.Status.BOOKED
