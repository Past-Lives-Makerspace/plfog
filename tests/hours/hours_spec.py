"""One set of hours per person, kept in step with the old editors' rows (#1300, part 1)."""

from __future__ import annotations

from datetime import date, time, timedelta
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import RestrictedError
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from hours.follow import lock_person
from hours.models import DayOff, Hours, TimeKey
from membership.models import Member, OrientationAvailability, OrientationDayOff
from tests.membership.factories import MemberFactory, OrientationAvailabilityFactory
from tests.tours.factories import TourDayOffFactory, TourHoursFactory
from tours.models import TourHours

pytestmark = pytest.mark.django_db

# 2030-01-12 is a Saturday.
SATURDAY = date(2030, 1, 12)


def _orientation(person: Member, **kwargs: Any) -> OrientationAvailability:
    """Personal orientation hours: Tuesday 4:00 to 6:00 PM, the tour factory's time, unless given."""
    values: dict[str, Any] = {"orienter": person, "start_time": time(16), "end_time": time(18)}
    values.update(kwargs)
    return OrientationAvailabilityFactory(**values)


def _sets(person: Member) -> list[TimeKey]:
    return sorted((hours.key for hours in Hours.objects.filter(member=person)), key=repr)


def _days_off(person: Member) -> list[tuple[date, date, str]]:
    return sorted(DayOff.objects.filter(member=person).values_list("starts_on", "ends_on", "note"))


def _ahead(days: int) -> date:
    return timezone.localdate() + timedelta(days=days)


def describe_grouping_hours():
    def it_gives_a_tour_row_and_an_orientation_row_of_the_same_time_one_set():
        person = MemberFactory()
        tour = TourHoursFactory(guide=person)
        orientation = _orientation(person)
        tour.refresh_from_db()
        assert tour.hours_id is not None
        assert orientation.hours_id == tour.hours_id
        hours = Hours.objects.get()
        assert hours.key == TimeKey(None, 1, "weekly", None, False, time(16), time(18))
        assert list(hours.tour_hours.all()) == [tour] and list(hours.orientation_rules.all()) == [orientation]

    def it_keeps_different_times_and_people_apart():
        person, other = MemberFactory(), MemberFactory()
        TourHoursFactory(guide=person)
        TourHoursFactory(guide=other)
        _orientation(person, start_time=time(17))
        _orientation(person, cadence=OrientationAvailability.Cadence.FORTNIGHTLY, anchor_date=date(2030, 1, 8))
        TourHoursFactory(guide=person, date=SATURDAY, weekday=SATURDAY.weekday())
        assert _sets(person) == sorted(
            [
                TimeKey(None, 1, "weekly", None, False, time(16), time(18)),
                TimeKey(None, 1, "weekly", None, False, time(17), time(18)),
                TimeKey(None, 1, "fortnightly", date(2030, 1, 8), False, time(16), time(18)),
                TimeKey(SATURDAY, 5, "weekly", None, False, time(16), time(18)),
            ],
            key=repr,
        )
        assert len(_sets(other)) == 1

    def it_moves_a_set_with_its_only_row_and_keeps_its_id():
        tour = TourHoursFactory()
        hours_id = tour.hours_id
        tour.start_time = time(15)
        tour.save()
        assert tour.hours_id == hours_id
        assert Hours.objects.get().start_time == time(15)

    def it_splits_an_edited_row_off_a_shared_set_and_leaves_the_other_its_set():
        person = MemberFactory()
        tour = TourHoursFactory(guide=person)
        orientation = _orientation(person)
        shared = orientation.hours_id
        tour.end_time = time(19)
        tour.save()
        orientation.refresh_from_db()
        assert orientation.hours_id == shared
        assert tour.hours_id not in (None, shared)
        assert len(_sets(person)) == 2

    def it_joins_rows_edited_to_the_same_time_and_removes_the_set_left_empty():
        person = MemberFactory()
        tour = TourHoursFactory(guide=person)
        orientation = _orientation(person, start_time=time(17))
        orientation.start_time = time(16)
        orientation.save()
        assert orientation.hours_id == TourHours.objects.get().hours_id
        assert Hours.objects.count() == 1
        assert tour.pk

    def it_never_writes_back_a_set_that_moved_since_the_row_was_loaded():
        person = MemberFactory()
        tour = TourHoursFactory(guide=person, start_time=time(17))
        stale = TourHours.objects.get(pk=tour.pk)
        _orientation(person, start_time=time(17))
        TourHours.objects.filter(pk=tour.pk).update(hours=None)
        Hours.objects.regroup(person.pk)
        stale.seats = 6
        stale.save()
        assert stale.hours_id == OrientationAvailability.objects.get().hours_id

    def it_removes_a_set_when_its_last_row_goes():
        person = MemberFactory()
        tour = TourHoursFactory(guide=person)
        orientation = _orientation(person)
        tour.delete()
        assert Hours.objects.filter(member=person).count() == 1
        orientation.delete()
        assert not Hours.objects.exists()

    def it_lets_a_row_go_when_its_orientor_changes_or_it_becomes_shared():
        person, other = MemberFactory(), MemberFactory()
        orientation = _orientation(person)
        orientation.orienter = other
        orientation.save()
        assert not Hours.objects.filter(member=person).exists()
        assert Hours.objects.get(member=other).pk == orientation.hours_id
        orientation.orienter = None
        orientation.save()
        assert orientation.hours_id is None
        assert not Hours.objects.exists()

    def it_gives_a_shared_orientation_row_no_set():
        orientation = OrientationAvailabilityFactory()
        assert orientation.orienter_id is None and orientation.hours_id is None
        assert not Hours.objects.exists()

    def it_never_changes_the_time_of_a_kind_row():
        person = MemberFactory()
        tour = TourHoursFactory(guide=person, start_time=time(15, 30))
        orientation = _orientation(person, cadence="monthly", anchor_date=date(2030, 1, 29), last_of_month=True)
        Hours.objects.regroup(person.pk)
        tour.refresh_from_db()
        orientation.refresh_from_db()
        assert (tour.weekday, tour.start_time, tour.end_time) == (1, time(15, 30), time(18))
        assert (orientation.cadence, orientation.anchor_date, orientation.last_of_month) == (
            "monthly",
            date(2030, 1, 29),
            True,
        )


def describe_a_set_of_hours():
    def it_refuses_a_bare_delete_while_a_row_ticks_it():
        tour = TourHoursFactory()
        with pytest.raises(RestrictedError):
            Hours.objects.get().delete()
        assert TourHours.objects.filter(pk=tour.pk).exists()

    def it_goes_with_its_person():
        person = MemberFactory()
        TourHoursFactory(guide=person)
        _orientation(person)
        person.delete()
        assert not Hours.objects.exists()

    @pytest.mark.parametrize(
        "values",
        [
            {"start_time": time(18), "end_time": time(16)},
            {"date": SATURDAY, "weekday": 1},
            {"date": SATURDAY, "weekday": 5, "cadence": "fortnightly"},
        ],
        ids=["ends_before_it_starts", "date_on_another_weekday", "dated_on_a_cadence"],
    )
    def it_refuses_an_impossible_time(values: dict[str, Any]):
        fields: dict[str, Any] = {"member": MemberFactory(), "weekday": 1, "start_time": time(16), "end_time": time(18)}
        fields.update(values)
        with pytest.raises(IntegrityError), transaction.atomic():
            Hours.objects.create(**fields)

    def it_reads_as_its_person_and_time():
        person = MemberFactory()
        weekly = Hours.objects.create(member=person, weekday=1, start_time=time(16), end_time=time(18))
        dated = Hours.objects.create(member=person, date=SATURDAY, weekday=5, start_time=time(11), end_time=time(15))
        assert str(weekly) == f"{person}: Every week, Tuesday 16:00 to 18:00"
        assert str(dated) == f"{person}: Sat 2030-01-12 11:00 to 15:00"


def describe_copying_days_off():
    def it_copies_tour_ranges_and_orientation_days_and_skips_a_day_a_range_covers():
        person = MemberFactory()
        TourDayOffFactory(guide=person, starts_on=_ahead(3), ends_on=_ahead(5), note="Trip")
        OrientationDayOff.objects.create(orienter=person, date=_ahead(4), note="Same trip")
        OrientationDayOff.objects.create(orienter=person, date=_ahead(9), note="Dentist")
        assert _days_off(person) == [(_ahead(3), _ahead(5), "Trip"), (_ahead(9), _ahead(9), "Dentist")]

    def it_follows_a_delete_and_keeps_the_rows_that_still_match():
        person = MemberFactory()
        trip = TourDayOffFactory(guide=person, starts_on=_ahead(3), ends_on=_ahead(5))
        dentist = OrientationDayOff.objects.create(orienter=person, date=_ahead(4))
        kept = DayOff.objects.get(member=person)
        trip.delete()
        assert _days_off(person) == [(_ahead(4), _ahead(4), "")]
        dentist.delete()
        assert not DayOff.objects.exists()
        assert kept.pk

    def it_keeps_a_matching_row_through_another_save():
        person = MemberFactory()
        day_off = TourDayOffFactory(guide=person)
        first = DayOff.objects.get()
        day_off.note = ""
        day_off.save()
        assert list(DayOff.objects.all()) == [first]

    def it_reads_as_its_person_and_days():
        person = MemberFactory()
        one = DayOff.objects.create(member=person, starts_on=SATURDAY, ends_on=SATURDAY)
        run = DayOff.objects.create(member=person, starts_on=SATURDAY, ends_on=SATURDAY + timedelta(days=2))
        assert str(one) == f"{person} off 2030-01-12"
        assert str(run) == f"{person} off 2030-01-12 to 2030-01-14"


def describe_locking_a_person():
    def _locks(run: Any) -> int:
        with CaptureQueriesContext(connection) as queries:
            run()
        return sum('"membership_member"' in query["sql"] for query in queries.captured_queries)

    @pytest.mark.django_db(transaction=True)
    def it_refuses_outside_a_transaction_and_locks_afresh_in_the_next_one():
        person = MemberFactory()
        with pytest.raises(RuntimeError):
            lock_person(person.pk)
        with transaction.atomic():
            lock_person(person.pk)
        with transaction.atomic():
            assert _locks(lambda: lock_person(person.pk)) == 1

    def it_locks_each_person_once_per_transaction():
        person, other = MemberFactory(), MemberFactory()
        with transaction.atomic():
            assert _locks(lambda: lock_person(person.pk)) == 1
            assert _locks(lambda: lock_person(person.pk)) == 0
            assert _locks(lambda: lock_person(other.pk)) == 1

    def it_locks_again_after_a_rollback_to_before_the_lock():
        person = MemberFactory()
        with transaction.atomic():
            try:
                with transaction.atomic():
                    lock_person(person.pk)
                    raise ValueError
            except ValueError:
                pass
            assert _locks(lambda: lock_person(person.pk)) == 1
