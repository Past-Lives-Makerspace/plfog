"""Data-migration spec for hours 0002 (sets of hours) and 0003 (days off) (#1300, part 1).

Both migrations touch only fields that exist at head, so their functions run here against the
current app registry instead of rewinding the schema with ``MigrationExecutor``. Every row is
made first and its dual write undone, so the migration starts from the tables as production has them.
"""

from __future__ import annotations

from datetime import date, time
from importlib import import_module

import pytest
from django.apps import apps

from hours.models import DayOff, Hours, TimeKey
from membership.models import OrientationAvailability, OrientationDayOff
from tests.membership.factories import MemberFactory, OrientationAvailabilityFactory
from tests.tours.factories import TourDayOffFactory, TourHoursFactory
from tours.models import TourHours

_group = import_module("hours.migrations.0002_group_hours")
_days_off = import_module("hours.migrations.0003_copy_days_off")

SATURDAY = date(2030, 1, 12)
FIRST_TUESDAY = date(2030, 1, 1)


@pytest.mark.django_db
def describe_migration_0002_group_hours():
    def it_makes_one_set_per_person_and_time_and_reverses():
        person, other = MemberFactory(), MemberFactory()
        weekly = TourHoursFactory(guide=person)
        dated = TourHoursFactory(guide=person, date=SATURDAY, weekday=5, start_time=time(11), end_time=time(15))
        same = OrientationAvailabilityFactory(orienter=person, start_time=time(16), end_time=time(18))
        monthly = OrientationAvailabilityFactory(
            orienter=person, start_time=time(16), end_time=time(18), cadence="monthly", anchor_date=FIRST_TUESDAY
        )
        theirs = TourHoursFactory(guide=other)
        shared = OrientationAvailabilityFactory(start_time=time(16), end_time=time(18))
        times = {row.pk: (row.weekday, row.start_time, row.end_time) for row in TourHours.objects.all()}
        _group.ungroup_hours(apps, None)
        assert not Hours.objects.exists()

        _group.group_hours(apps, None)

        def set_of(row: TourHours | OrientationAvailability) -> Hours | None:
            row.refresh_from_db()
            return row.hours

        assert set_of(weekly) == set_of(same)
        assert set_of(weekly).key == TimeKey(None, 1, "weekly", None, False, time(16), time(18))
        assert set_of(dated).key == TimeKey(SATURDAY, 5, "weekly", None, False, time(11), time(15))
        assert set_of(monthly).key == TimeKey(None, 1, "monthly", FIRST_TUESDAY, False, time(16), time(18))
        assert set_of(theirs).member == other
        assert set_of(shared) is None
        assert Hours.objects.filter(member=person).count() == 3
        assert {row.pk: (row.weekday, row.start_time, row.end_time) for row in TourHours.objects.all()} == times

        _group.ungroup_hours(apps, None)
        assert not Hours.objects.exists()
        assert not TourHours.objects.filter(hours__isnull=False).exists()
        assert not OrientationAvailability.objects.filter(hours__isnull=False).exists()


@pytest.mark.django_db
def describe_migration_0003_copy_days_off():
    def it_copies_each_day_off_once_and_reverses():
        person, other = MemberFactory(), MemberFactory()
        TourDayOffFactory(guide=person, starts_on=SATURDAY, ends_on=date(2030, 1, 14), note="Trip")
        OrientationDayOff.objects.create(orienter=person, date=date(2030, 1, 13))
        OrientationDayOff.objects.create(orienter=person, date=date(2030, 1, 20), note="Dentist")
        OrientationDayOff.objects.create(orienter=other, date=date(2030, 1, 13))
        _days_off.delete_copies(apps, None)
        assert not DayOff.objects.exists()

        _days_off.copy_days_off(apps, None)

        assert sorted(DayOff.objects.values_list("member_id", "starts_on", "ends_on", "note")) == sorted(
            [
                (person.pk, SATURDAY, date(2030, 1, 14), "Trip"),
                (person.pk, date(2030, 1, 20), date(2030, 1, 20), "Dentist"),
                (other.pk, date(2030, 1, 13), date(2030, 1, 13), ""),
            ]
        )

        _days_off.delete_copies(apps, None)
        assert not DayOff.objects.exists()
