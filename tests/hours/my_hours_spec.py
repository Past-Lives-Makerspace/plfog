"""BDD specs for My Hours, the page (#1300, part 3): ``hours.page``, ``hours.views`` and the gate.

The week reads every block from ``hours.busy.busy_holds`` (the one rule), so a booked tour, a
seat holding orientation, a class taught, Google busy time and a day off each draw; open hours
are each set of hours that offers something that day. The lists below it name what each set is
open to. Only admins, Tour Guides and current orientors open the page, and only admins pick
someone else's.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from hours.busy import CLASS, DAY_OFF, GOOGLE, ORIENTATION, TOUR, busy_holds
from hours.models import DayOff, Hours
from hours.page import Chip, MyHours
from membership.models import Member, OrientationAvailability, OrientationBooking
from tests.membership.factories import (
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import (
    TourBookingFactory,
    TourCalendarConnectionFactory,
    TourDayOffFactory,
    TourHoursFactory,
)
from tours.models import CalendarBusySpan, TourCalendarConnection, TourGuideSettings

pytestmark = pytest.mark.django_db

URL = "/hours/"


def _next_week(weekday: int) -> date:
    """The day ``weekday`` (0 Monday) of next week: always ahead, always in ``week_offset=1``."""
    today = timezone.localdate()
    return today - timedelta(days=today.weekday()) + timedelta(weeks=1, days=weekday)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _day(page: MyHours, day: date) -> Any:
    return next(week_day for week_day in page.week if week_day.date == day)


def _chips(page: MyHours, day: date) -> list[tuple[str, str, str]]:
    return [(chip.style, chip.time_label, chip.what) for chip in _day(page, day).chips]


@pytest.fixture
def google_on(settings: Any) -> None:
    settings.GOOGLE_OAUTH_CLIENT_ID = "client-id"
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "client-secret"


def describe_busy_holds():
    def it_reads_nobody_as_nothing():
        assert busy_holds([], _at(_next_week(0), 0), _at(_next_week(6), 0)) == []

    def it_names_each_thing_holding_the_time_by_its_kind_and_row(google_on: None):
        person = MemberFactory()
        monday = _next_week(0)
        tour = TourBookingFactory(guide=person, starts_at=_at(monday, 10), ends_at=_at(monday, 11))
        slot = OrientationSlotFactory(orienter=person, starts_at=_at(monday, 12), ends_at=_at(monday, 13))
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        offering = ClassOfferingFactory(instructors=[person], status=ClassOffering.Status.PUBLISHED)
        session = ClassSessionFactory(class_offering=offering, starts_at=_at(monday, 14), ends_at=_at(monday, 16))
        connection = TourCalendarConnectionFactory(
            guide=person,
            busy_checked_at=timezone.now(),
            busy_spans=[[_at(monday, 17).isoformat(), _at(monday, 18).isoformat()]],
        )
        day_off = DayOff.objects.create(
            member=person, starts_on=monday + timedelta(days=1), ends_on=monday + timedelta(days=1)
        )
        holds = busy_holds([person.pk], _at(monday, 0), _at(monday + timedelta(days=7), 0))
        found = {(hold.source, hold.ref) for hold in holds}
        span = CalendarBusySpan.objects.get(connection=connection)
        assert (TOUR, tour.pk) in found
        assert (ORIENTATION, slot.pk) in found
        assert (CLASS, session.pk) in found
        assert (DAY_OFF, day_off.pk) in found
        assert (GOOGLE, span.pk) in found


def describe_access():
    def it_sends_a_signed_out_visitor_to_sign_in():
        response = Client().get(URL)
        assert response.status_code == 302
        assert "login" in response["Location"]

    def it_refuses_a_member_who_gives_nothing():
        assert signed_in(MemberFactory()).get(URL).status_code == 403

    @pytest.mark.parametrize("role", ["guide", "lead", "staff", "equipment"])
    def it_opens_for_tour_guides_and_current_orientors(role: str):
        person = make_guide() if role == "guide" else MemberFactory()
        if role == "lead":
            GuildFactory(guild_lead=person)
        elif role == "staff":
            GuildStaffMembershipFactory(member=person)
        elif role == "equipment":
            EquipmentStaffMembershipFactory(member=person)
        response = signed_in(person).get(URL)
        assert response.status_code == 200
        assert b"data-hours-week" in response.content
        assert b"data-hours-picker" not in response.content

    def it_refuses_a_lapsed_guild_lead():
        person = MemberFactory(status=Member.Status.FORMER)
        GuildFactory(guild_lead=person)
        assert signed_in(person).get(URL).status_code in (302, 403)

    def it_refuses_another_persons_hours_to_a_non_admin():
        guide, other = make_guide(), make_guide()
        assert signed_in(guide).get(f"{URL}?member={other.pk}").status_code == 403
        assert signed_in(guide).get(f"{URL}?member={guide.pk}").status_code == 200

    def it_answers_404_for_an_account_with_no_member_record():
        user = User.objects.create_superuser(username="root", email="root@example.com", password="x")
        Member.objects.filter(user=user).delete()
        client = Client()
        client.force_login(user)
        assert client.get(URL).status_code == 404


def describe_admin_picker():
    @pytest.fixture
    def admin() -> Member:
        return MemberFactory(full_legal_name="Ada Admin", fog_role=Member.FogRole.ADMIN)

    def it_lists_everyone_with_a_page_or_hours_and_opens_the_one_picked(admin: Member):
        guide = make_guide(full_legal_name="Gina Guide")
        lapsed = MemberFactory(full_legal_name="Lou Lapsed")
        TourHoursFactory(guide=lapsed)
        MemberFactory(full_legal_name="Pat Plain")
        client = signed_in(admin)
        page = client.get(URL).content.decode()
        assert "data-hours-picker" in page
        assert "Gina Guide" in page and "Lou Lapsed" in page and "Pat Plain" not in page
        picked = client.get(f"{URL}?member={guide.pk}")
        assert picked.status_code == 200
        assert picked.context["target"] == guide
        assert f"member={guide.pk}&amp;week_offset=1" in picked.content.decode()

    def it_answers_404_for_someone_with_no_page_or_garbage(admin: Member):
        client = signed_in(admin)
        assert client.get(f"{URL}?member={MemberFactory().pk}").status_code == 404
        assert client.get(f"{URL}?member=abc").status_code == 404


def describe_the_week():
    def it_draws_open_hours_with_the_kinds_they_offer_and_every_booking_and_busy_time(google_on: None):
        guide = make_guide()
        guild = GuildFactory(name="Printmaking Guild", guild_lead=guide)
        tuesday = _next_week(1)
        TourHoursFactory(guide=guide, weekday=1, start_time=time(11), end_time=time(18))
        OrientationAvailabilityFactory(guild=guild, orienter=guide, weekday=1, start_time=time(11), end_time=time(18))
        TourBookingFactory(guide=guide, starts_at=_at(tuesday, 13), ends_at=_at(tuesday, 14), party_size=2)
        TourBookingFactory(guide=guide, starts_at=_at(tuesday, 13), ends_at=_at(tuesday, 14), party_size=1)
        slot = OrientationSlotFactory(guild=guild, orienter=guide, starts_at=_at(tuesday, 15), ends_at=_at(tuesday, 16))
        OrientationBookingFactory(
            slot=slot, member=MemberFactory(preferred_name="Sam Kim"), status=OrientationBooking.Status.CONFIRMED
        )
        page = MyHours.for_member(guide, week_offset=1)
        assert [kind.label for kind in page.kinds] == ["Tours", "Printmaking Guild orientations"]
        assert _chips(page, tuesday) == [
            ("open", "11a to 6p", "Open"),
            ("booked", "1p to 2p", "Tour · 3 visitors"),
            ("booked", "3p to 4p", "Printmaking Guild · Sam K."),
        ]
        open_chip = _day(page, tuesday).chips[0]
        assert [kind.key for kind in open_chip.kinds] == ["tours", f"guild-{guild.pk}"]
        assert _day(page, tuesday).chips[1].kind == page.kinds[0]

    def it_names_a_waiting_request_and_a_slot_several_have_booked():
        orientor = MemberFactory()
        guild = GuildFactory(name="Ceramics Guild", guild_lead=orientor)
        wednesday = _next_week(2)
        waiting = OrientationSlotFactory(
            guild=guild,
            orienter=orientor,
            starts_at=_at(wednesday, 13),
            ends_at=_at(wednesday, 14, 30),
        )
        OrientationBookingFactory(slot=waiting, status=OrientationBooking.Status.REQUESTED)
        full = OrientationSlotFactory(
            guild=guild, orienter=orientor, starts_at=_at(wednesday, 16), ends_at=_at(wednesday, 17)
        )
        OrientationBookingFactory(slot=full, status=OrientationBooking.Status.CONFIRMED)
        OrientationBookingFactory(slot=full, status=OrientationBooking.Status.CONFIRMED)
        page = MyHours.for_member(orientor, week_offset=1)
        assert _chips(page, wednesday) == [
            ("booked", "1p to 2:30p", "Ceramics Guild · requested"),
            ("booked", "4p to 5p", "Ceramics Guild · 2 booked"),
        ]
        assert not page.gives_tours

    def it_hatches_a_class_taught_and_google_busy_time_split_across_midnight(google_on: None):
        person = make_guide()
        thursday, friday = _next_week(3), _next_week(4)
        offering = ClassOfferingFactory(
            instructors=[person], title="Intro to Linocut", status=ClassOffering.Status.PUBLISHED
        )
        ClassSessionFactory(class_offering=offering, starts_at=_at(thursday, 18, 30), ends_at=_at(thursday, 21))
        TourCalendarConnectionFactory(
            guide=person,
            busy_checked_at=timezone.now(),
            busy_spans=[[_at(thursday, 22).isoformat(), _at(friday, 2).isoformat()]],
        )
        page = MyHours.for_member(person, week_offset=1)
        assert _chips(page, thursday) == [
            ("busy", "6:30p to 9p", "Class: Intro to Linocut"),
            ("busy", "10p to 12a", "Busy in Google"),
        ]
        assert _chips(page, friday) == [("busy", "12a to 2a", "Busy in Google")]

    def it_stripes_a_day_off_offers_no_open_hours_on_it_and_keeps_its_bookings():
        guide = make_guide()
        tuesday = _next_week(1)
        TourHoursFactory(guide=guide, weekday=1, start_time=time(11), end_time=time(18))
        TourBookingFactory(guide=guide, starts_at=_at(tuesday, 13), ends_at=_at(tuesday, 14), party_size=1)
        TourDayOffFactory(guide=guide, starts_on=tuesday, ends_on=tuesday)
        page = MyHours.for_member(guide, week_offset=1)
        assert _day(page, tuesday).is_day_off
        assert _chips(page, tuesday) == [("booked", "1p to 2p", "Tour · 1 visitor")]
        assert not _day(page, tuesday + timedelta(days=1)).is_day_off

    def it_offers_a_repeating_set_only_on_the_weeks_it_comes_round():
        orientor = MemberFactory()
        guild = GuildFactory(guild_lead=orientor)
        sunday = _next_week(6)
        OrientationAvailabilityFactory(
            guild=guild,
            orienter=orientor,
            weekday=6,
            cadence=OrientationAvailability.Cadence.FORTNIGHTLY,
            anchor_date=sunday,
            start_time=time(11),
            end_time=time(18),
        )
        assert [chip.style for chip in _day(MyHours.for_member(orientor, week_offset=1), sunday).chips] == ["open"]
        later = sunday + timedelta(weeks=1)
        assert _day(MyHours.for_member(orientor, week_offset=2), later).chips == []

    def it_labels_this_week_and_a_week_across_two_months():
        guide = make_guide()
        page = MyHours.for_member(guide)
        assert page.week[0].date.weekday() == 0
        assert any(day.is_today for day in page.week)
        monday = page.week[0].date
        assert page.week_label.startswith(f"{monday:%b} {monday.day} to ")
        # Feb 1 and Mar 1, 2027 are both Mondays, so from late December the next such week is 13 weeks out.
        offset = next(
            n for n in range(20) if (monday + timedelta(weeks=n)).month != (monday + timedelta(weeks=n, days=6)).month
        )
        crossing = MyHours.for_member(guide, week_offset=offset)
        first, last = crossing.week[0].date, crossing.week[-1].date
        assert crossing.week_label == f"{first:%b} {first.day} to {last:%b} {last.day}"


def describe_the_lists():
    def it_lists_every_week_and_upcoming_dates_with_what_each_set_is_open_to():
        guide = make_guide()
        guild = GuildFactory(name="Printmaking Guild", guild_lead=guide)
        TourHoursFactory(guide=guide, weekday=0, start_time=time(11), end_time=time(18), seats=6)
        OrientationAvailabilityFactory(
            guild=guild, orienter=guide, weekday=0, start_time=time(11), end_time=time(18), seats=4
        )
        OrientationAvailabilityFactory(
            open_window=True, guild=guild, orienter=guide, weekday=2, start_time=time(12), end_time=time(17)
        )
        friday = _next_week(4)
        TourHoursFactory(guide=guide, date=friday, weekday=4, start_time=time(14), end_time=time(18), seats=1)
        TourHoursFactory(
            guide=guide,
            date=timezone.localdate() - timedelta(days=3),
            weekday=(timezone.localdate() - timedelta(days=3)).weekday(),
        )
        page = MyHours.for_member(guide)
        weekly = [(row.when, [offer.text for offer in row.offers]) for row in page.weekly]
        assert weekly == [
            (
                "Mon · 11:00 AM to 6:00 PM · Every week",
                [
                    "Tours · 1 hour each · 6 seats",
                    f"{guild.orientation_types.get().name} · fixed start times · 4 seats",
                ],
            ),
            ("Wed · 12:00 PM to 5:00 PM · Every week", ["Any orientation · any time in the window"]),
        ]
        assert [(row.when, [o.text for o in row.offers]) for row in page.dated] == [
            (f"{friday:%a, %b} {friday.day} · 2:00 PM to 6:00 PM", ["Tours · 1 hour each · 1 seat"])
        ]

    def it_names_a_tool_and_says_when_a_set_is_open_to_nothing():
        orientor = MemberFactory()
        rule = OrientationAvailabilityFactory(equipment_owned=True, orienter=orientor, seats=1)
        EquipmentStaffMembershipFactory(member=orientor, equipment=rule.orientation_type.equipment)
        OrientationAvailabilityFactory(
            guild=GuildFactory(guild_lead=orientor), orienter=orientor, weekday=3, is_active=False
        )
        page = MyHours.for_member(orientor)
        assert [kind.label for kind in page.kinds] == [f"{rule.orientation_type.equipment.name} orientations"]
        assert [len(row.offers) for row in page.weekly] == [1, 0]
        assert page.weekly[0].offers[0].text.endswith("· 1 seat")
        response = signed_in(orientor).get(URL)
        assert "Not open to anything yet." in response.content.decode()

    def it_lists_upcoming_days_off_only():
        guide = make_guide()
        today = timezone.localdate()
        TourDayOffFactory(guide=guide, starts_on=today - timedelta(days=5), ends_on=today - timedelta(days=4))
        TourDayOffFactory(
            guide=guide, starts_on=today + timedelta(days=3), ends_on=today + timedelta(days=3), note="Out of town"
        )
        page = MyHours.for_member(guide)
        assert [day_off.note for day_off in page.days_off] == ["Out of town"]

    def it_renders_empty_lists_when_nothing_is_set():
        content = signed_in(make_guide()).get(URL).content.decode()
        for marker in ("data-hours-weekly-empty", "data-hours-dated-empty", "data-hours-days-off-empty"):
            assert marker in content


def describe_google_calendar_and_notice():
    def it_says_google_is_not_set_up(settings: Any):
        settings.GOOGLE_OAUTH_CLIENT_ID = ""
        assert 'data-hours-gcal="dormant"' in signed_in(make_guide()).get(URL).content.decode()

    def it_reads_each_calendar_state(google_on: None):
        guide = make_guide()
        client = signed_in(guide)
        assert 'data-hours-gcal="none"' in client.get(URL).content.decode()
        connection = TourCalendarConnectionFactory(guide=guide, busy_checked_at=None)
        page = client.get(URL).content.decode()
        assert 'data-hours-gcal="connected"' in page and "Not read yet." in page
        connection.status = TourCalendarConnection.Status.BROKEN
        connection.broken_at = timezone.now()
        connection.save()
        assert 'data-hours-gcal="broken"' in client.get(URL).content.decode()

    def it_shows_a_guides_booking_notice_and_none_for_an_orientor():
        guide = make_guide()
        assert MyHours.for_member(guide).notice_line.startswith("Visitors on the website can book a tour until")
        TourGuideSettings.objects.create(guide=guide, min_notice_minutes=0)
        assert MyHours.for_member(guide).notice_line == "Visitors on the website can book a tour right up to its start."
        orientor = MemberFactory()
        GuildFactory(guild_lead=orientor)
        content = signed_in(orientor).get(URL).content.decode()
        assert "data-hours-notice" not in content


def describe_labels():
    def it_writes_how_often_hours_come_round():
        cadence = OrientationAvailability.Cadence
        anchor = date(2026, 1, 13)  # the 2nd Tuesday
        assert Hours(weekday=1, cadence=cadence.WEEKLY).repeat_label == "Every week"
        assert Hours(weekday=1, cadence=cadence.FORTNIGHTLY, anchor_date=anchor).repeat_label == "Every other week"
        assert (
            Hours(weekday=1, cadence=cadence.MONTHLY, anchor_date=anchor).repeat_label
            == "Every month on the 2nd Tuesday"
        )
        assert (
            Hours(weekday=1, cadence=cadence.MONTHLY, anchor_date=anchor, last_of_month=True).repeat_label
            == "Every month on the last Tuesday"
        )
        assert Hours(weekday=1, cadence=cadence.YEARLY, anchor_date=anchor).repeat_label == (
            "Every year on the 2nd Tuesday of January"
        )
        assert Hours(weekday=1, cadence=cadence.MONTHLY).repeat_label == "Every month"
        ahead = timezone.localdate() + timedelta(days=30)
        assert (
            Hours(weekday=ahead.weekday(), anchor_date=ahead).repeat_label == f"Every week from {ahead:%b} {ahead.day}"
        )
        assert Hours(weekday=1, date=date(2026, 1, 13)).repeat_label == ""

    def it_writes_a_day_off_and_a_chip_time():
        assert DayOff(starts_on=date(2026, 12, 23), ends_on=date(2026, 12, 23)).label == "Wed, Dec 23"
        assert DayOff(starts_on=date(2026, 12, 22), ends_on=date(2027, 1, 1)).label == "Tue, Dec 22 to Fri, Jan 1"
        noon = _at(date(2026, 12, 23), 12)
        assert Chip("busy", noon, noon + timedelta(minutes=90), "x").time_label == "12p to 1:30p"
