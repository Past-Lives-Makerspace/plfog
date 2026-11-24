"""BDD specs for what blocks a person's time (#1300, part 2): ``hours.busy`` and every reader of it.

``busy_spans`` and its SQL twin ``busy_q`` (through ``OrientationSlot.objects.bookable()``) read the
same five things: booked tours, seat holding orientation slots, sessions of classes taught, Google
busy rows and days off. Then each kind's engine: tours skip an orientor's booked time, fixed slots
and open windows skip a guide's booked tour, a booking or a move onto busy time is refused, and a
tour hour nobody booked blocks nothing.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from hours.busy import busy_spans, is_busy
from hours.models import DayOff
from membership import orientations
from membership.models import BUSY_MESSAGE, Member, OrientationBooking, OrientationError, OrientationSlot
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    OrientationAvailabilityBlockFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)
from tests.tours.conftest import make_guide
from tests.tours.factories import TourBookingFactory, TourCalendarConnectionFactory, TourHoursFactory
from tours.availability import open_tours
from tours.booking import TourUnavailableError, Visitor, book_tour
from tours.models import CalendarBusySpan, TourBooking, TourCalendarConnection

pytestmark = pytest.mark.django_db


@pytest.fixture
def google_on(settings: Any) -> None:
    """Google sign in for Tour Guides is set up (#951), so Google busy time counts."""
    settings.GOOGLE_OAUTH_CLIENT_ID = "client-id"
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "client-secret"


CONFIRMED = OrientationBooking.Status.CONFIRMED


def _day(offset: int = 5) -> date:
    return timezone.localdate() + timedelta(days=offset)


def _at(hour: int, minute: int = 0, offset: int = 5) -> datetime:
    return timezone.make_aware(datetime.combine(_day(offset), time(hour, minute)))


def _slot(orienter: Member | None, hour: int, *, seats: int = 4, minutes: int = 60, **fields: Any) -> OrientationSlot:
    """A slot at ``hour`` on the day, in a guild ``orienter`` leads so it is theirs to give."""
    fields.setdefault("starts_at", _at(hour))
    fields.setdefault("ends_at", fields["starts_at"] + timedelta(minutes=minutes))
    if "guild" not in fields:
        fields["guild"] = GuildFactory(guild_lead=orienter)
    return OrientationSlotFactory(orienter=orienter, seats=seats, **fields)


def _booked_slot(orienter: Member, hour: int, **fields: Any) -> OrientationSlot:
    slot = _slot(orienter, hour, **fields)
    OrientationBookingFactory(slot=slot, status=CONFIRMED)
    return slot


def _class(teacher: Member, hour: int, status: str = ClassOffering.Status.PUBLISHED, **fields: Any) -> Any:
    offering = ClassOfferingFactory(status=status, instructors=[teacher], **fields)
    return ClassSessionFactory(class_offering=offering, starts_at=_at(hour), ends_at=_at(hour + 2))


def _google(person: Member, hour: int, **fields: Any) -> TourCalendarConnection:
    start = _at(hour)
    fields.setdefault("busy_checked_at", timezone.now())
    return TourCalendarConnectionFactory(
        guide=person, busy_spans=[[start.isoformat(), (start + timedelta(hours=1)).isoformat()]], **fields
    )


def _bookable(slot: OrientationSlot) -> bool:
    return OrientationSlot.objects.bookable().filter(pk=slot.pk).exists()


def _span(hour: int, end_hour: int) -> tuple[datetime, datetime]:
    return (_at(hour), _at(end_hour))


WHOLE_DAY = (_at(0), _at(23, 59))


def describe_busy_spans():
    def it_answers_nothing_for_nobody():
        assert busy_spans([], *WHOLE_DAY) == {}

    def it_gives_each_person_a_list_even_when_free():
        person = MemberFactory()
        assert busy_spans([person.pk], *WHOLE_DAY) == {person.pk: []}

    def it_counts_a_tour_holding_seats_and_not_a_cancelled_one():
        guide = MemberFactory()
        TourBookingFactory(guide=guide, starts_at=_at(10), ends_at=_at(11))
        TourBookingFactory(guide=guide, starts_at=_at(14), ends_at=_at(15), status=TourBooking.Status.CANCELLED)
        assert busy_spans([guide.pk], *WHOLE_DAY)[guide.pk] == [_span(10, 11)]
        assert busy_spans([guide.pk], *WHOLE_DAY, tours=False)[guide.pk] == []

    def it_counts_a_seat_holding_slot_once_and_leaves_out_the_one_being_booked():
        orientor = MemberFactory()
        slot = _booked_slot(orientor, 10)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.REQUESTED)
        _slot(orientor, 12)
        declined = _slot(orientor, 14)
        OrientationBookingFactory(slot=declined, status=OrientationBooking.Status.DECLINED)
        assert busy_spans([orientor.pk], *WHOLE_DAY)[orientor.pk] == [_span(10, 11)]
        assert busy_spans([orientor.pk], *WHOLE_DAY, except_slot=slot.pk)[orientor.pk] == []

    def it_counts_every_teacher_of_a_live_class_and_no_draft_or_cancelled_one():
        teacher, partner = MemberFactory(), MemberFactory()
        session = _class(teacher, 10)
        session.class_offering.instructor_rows.create(member=partner)
        _class(teacher, 14, status=ClassOffering.Status.DRAFT)
        _class(teacher, 17, status=ClassOffering.Status.CANCELLED)
        found = busy_spans([teacher.pk, partner.pk], *WHOLE_DAY)
        assert found == {teacher.pk: [_span(10, 12)], partner.pk: [_span(10, 12)]}

    def it_counts_google_busy_only_from_a_connected_read_calendar_once_google_is_set_up(google_on: None, settings: Any):
        person, unread, broken = MemberFactory(), MemberFactory(), MemberFactory()
        _google(person, 9)
        _google(unread, 9, busy_checked_at=None)
        _google(broken, 9, status=TourCalendarConnection.Status.BROKEN)
        found = busy_spans([person.pk, unread.pk, broken.pk], *WHOLE_DAY)
        assert found == {person.pk: [_span(9, 10)], unread.pk: [], broken.pk: []}
        assert busy_spans([person.pk], *WHOLE_DAY, google_busy=False)[person.pk] == []
        settings.GOOGLE_OAUTH_CLIENT_ID = ""
        assert busy_spans([person.pk], *WHOLE_DAY)[person.pk] == []

    def it_counts_a_day_off_as_every_whole_local_day_it_covers():
        person = MemberFactory()
        DayOff.objects.create(member=person, starts_on=_day(5), ends_on=_day(6))
        DayOff.objects.create(member=person, starts_on=_day(9), ends_on=_day(9))
        midnight = timezone.make_aware(datetime.combine(_day(7), time.min))
        assert busy_spans([person.pk], *WHOLE_DAY)[person.pk] == [(_at(0), midnight)]

    def it_merges_what_overlaps_or_touches():
        guide = MemberFactory()
        TourBookingFactory(guide=guide, starts_at=_at(10), ends_at=_at(11))
        _booked_slot(guide, 11)
        _booked_slot(guide, 11, minutes=30)
        assert busy_spans([guide.pk], *WHOLE_DAY)[guide.pk] == [_span(10, 12)]


def describe_is_busy():
    def it_lets_a_time_that_only_touches_busy_time_through():
        guide = MemberFactory()
        TourBookingFactory(guide=guide, starts_at=_at(10), ends_at=_at(11))
        assert is_busy(guide.pk, _at(10, 30), _at(11, 30)) is True
        assert is_busy(guide.pk, _at(11), _at(12)) is False
        assert is_busy(guide.pk, _at(9), _at(10)) is False


def describe_bookable_slots():
    def it_hides_a_slot_whose_orientor_has_a_tour_booked_over_it_and_not_one_alongside():
        guide = MemberFactory()
        over, beside = _slot(guide, 10), _slot(guide, 12)
        TourBookingFactory(guide=guide, starts_at=_at(10, 30), ends_at=_at(11, 30))
        assert (_bookable(over), _bookable(beside)) == (False, True)

    def it_keeps_selling_a_slots_own_seats_and_hides_an_overlapping_one():
        orientor = MemberFactory()
        booked = _booked_slot(orientor, 10)
        overlapping = _slot(orientor, 10, minutes=30, guild=booked.guild, orientation_type=booked.orientation_type)
        assert (_bookable(booked), _bookable(overlapping)) == (True, False)

    def it_hides_a_slot_over_a_class_they_teach():
        teacher = MemberFactory()
        slot = _slot(teacher, 11)
        _class(teacher, 10)
        assert _bookable(slot) is False

    def it_hides_a_slot_on_a_day_off_or_running_into_one():
        person = MemberFactory()
        on_the_day = _slot(person, 10)
        into_it = _slot(person, 0, starts_at=_at(23, offset=4), ends_at=_at(10))
        before = _slot(person, 0, starts_at=_at(22, offset=4), ends_at=_at(0))
        DayOff.objects.create(member=person, starts_on=_day(5), ends_on=_day(5))
        assert (_bookable(on_the_day), _bookable(into_it), _bookable(before)) == (False, False, True)

    def it_hides_a_slot_over_google_busy_time_only_once_google_is_set_up(google_on: None, settings: Any):
        person = MemberFactory()
        slot = _slot(person, 9)
        _google(person, 9)
        assert _bookable(slot) is False
        settings.GOOGLE_OAUTH_CLIENT_ID = ""
        assert _bookable(slot) is True

    def it_never_hides_a_shared_slot():
        TourBookingFactory(starts_at=_at(10), ends_at=_at(11))
        assert _bookable(_slot(None, 10)) is True


def describe_booking_an_orientation():
    def it_refuses_a_slot_whose_orientor_has_a_tour_then():
        guide = MemberFactory()
        slot = _slot(guide, 10)
        TourBookingFactory(guide=guide, starts_at=_at(10), ends_at=_at(11))
        with pytest.raises(OrientationError, match=BUSY_MESSAGE):
            orientations.request_orientation(slot, MemberFactory())
        with pytest.raises(OrientationError, match=BUSY_MESSAGE):
            orientations.request_orientation(slot, MemberFactory(), by_staff=True)

    def it_offers_no_window_time_over_their_booked_tour():
        guide = MemberFactory()
        window = OrientationAvailabilityBlockFactory(orienter=guide, starts_at=_at(9), ends_at=_at(12))
        orientation_type = OrientationTypeFactory(guild=window.guild, duration_minutes=60)
        TourBookingFactory(guide=guide, starts_at=_at(10), ends_at=_at(11))
        free = {window.pk: [(_at(9), _at(10)), (_at(11), _at(12))]}
        assert type(window).free_intervals_by_block([window]) == free
        assert window.free_intervals() == free[window.pk]
        with pytest.raises(OrientationError):
            orientations.request_block_orientation(window, MemberFactory(), _at(10), orientation_type=orientation_type)

    def it_reads_many_windows_busy_time_at_once_each_by_its_own_orientor():
        guide, free = MemberFactory(), MemberFactory()
        windows = [
            OrientationAvailabilityBlockFactory(orienter=person, starts_at=_at(9), ends_at=_at(12))
            for person in (guide, free, guide)
        ]
        TourBookingFactory(guide=guide, starts_at=_at(10), ends_at=_at(11))
        found = type(windows[0]).free_intervals_by_block(windows)
        split = [(_at(9), _at(10)), (_at(11), _at(12))]
        assert found == {windows[0].pk: split, windows[1].pk: [(_at(9), _at(12))], windows[2].pk: split}
        assert type(windows[0]).free_intervals_by_block([]) == {}


def describe_moving_an_orientation():
    def it_refuses_to_propose_a_time_its_orientor_is_busy():
        orientor = MemberFactory()
        booking = OrientationBookingFactory(slot=_slot(orientor, 10), status=CONFIRMED)
        _class(orientor, 14)
        with pytest.raises(OrientationError, match=BUSY_MESSAGE):
            orientations.propose_orientation_time(booking, _at(15), actor=None)

    def it_refuses_a_time_its_orientor_is_busy_and_allows_one_over_the_slot_it_leaves():
        orientor = MemberFactory()
        slot = _slot(orientor, 10)
        booking = OrientationBookingFactory(slot=slot, status=CONFIRMED)
        TourBookingFactory(guide=orientor, starts_at=_at(14), ends_at=_at(15))
        with pytest.raises(OrientationError, match=BUSY_MESSAGE):
            orientations.move_orientation(booking, _at(14), actor=None)
        moved = orientations.move_orientation(booking, _at(10, 30), actor=None)
        assert moved.slot.starts_at == _at(10, 30)
        assert moved.slot.orienter == orientor


def describe_tours():
    def it_skips_a_guides_booked_orientation_and_class_and_offers_the_rest():
        guide = make_guide()
        TourHoursFactory(guide=guide, weekday=_day().weekday(), start_time=time(9), end_time=time(17), tour_minutes=60)
        _booked_slot(guide, 10)
        _slot(guide, 12)  # posted, nobody booked: blocks nothing
        _class(guide, 14)
        starts = [tour.starts_at for tour in open_tours(_day(), _day(), guide=guide, staff=True)]
        hours = [timezone.localtime(start).hour for start in starts]
        assert hours == [9, 11, 12, 13, 16]

    def it_refuses_to_book_a_tour_over_the_guides_orientation():
        guide = make_guide()
        TourHoursFactory(guide=guide, weekday=_day().weekday(), start_time=time(10), end_time=time(11))
        _booked_slot(guide, 10)
        visitor = Visitor(name="Sam Rivera", email="sam@example.com", phone="5035550100", party_size=1)
        with pytest.raises(TourUnavailableError):
            book_tour(guide=guide, starts_at=_at(10), visitor=visitor, now=_at(8, offset=0))


def describe_the_google_busy_rows():
    def it_reads_as_the_guide_and_the_time():
        connection = TourCalendarConnectionFactory(guide=MemberFactory(full_legal_name="Gina Guide"))
        row = CalendarBusySpan.objects.create(connection=connection, starts_at=_at(9), ends_at=_at(10))
        assert str(row).startswith("Gina Guide busy ")
