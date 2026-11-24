"""A person's time, kept once for every kind of thing they give (#1300).

``Hours`` is one set of a person's hours: a weekday or a date, how often it repeats, From and
Until. Each kind keeps its own row ticking that set of hours, with its own settings: a
``tours.TourHours`` (tour length, seats) and a personal ``membership.OrientationAvailability``
(type, fixed or open, seats, slot length, break), each pointing here through its ``hours`` key.
``DayOff`` is one day off, or a run of days, for every kind at once.

Part 1 of #1300 builds these underneath and nothing reads them yet. The old editors still write
the kind rows, and each such write regroups the person here (:meth:`HoursManager.regroup`,
:meth:`DayOffManager.recopy`), so these tables follow the old ones until part 2 switches the
readers. Regrouping never changes a kind row's own time, so no slot, window or tour moves.
"""

from __future__ import annotations

from collections import defaultdict
import datetime as dt
from typing import Any, ClassVar, NamedTuple

from django.db import models, transaction
from django.db.models import Q

from hours.follow import lock_person
from membership.models import Member, OrientationAvailability


class TimeKey(NamedTuple):
    """What makes two kind rows the same set of hours: the day, the repeat and the times."""

    date: dt.date | None
    weekday: int
    cadence: str
    anchor_date: dt.date | None
    last_of_month: bool
    start_time: dt.time
    end_time: dt.time


def _kind_rows() -> list[tuple[Any, str]]:
    """Every kind row model that ticks a set of hours, with the field naming its person."""
    from tours.models import TourHours

    return [(TourHours, "guide"), (OrientationAvailability, "orienter")]


class HoursManager(models.Manager["Hours"]):
    def regroup(self, member_id: int) -> None:
        """Point each of one person's kind rows at the set of hours with its time, making and removing sets to fit.

        Rows with the same :class:`TimeKey` share one ``Hours``. A set that already has a group's
        time stays that group's; otherwise a set the group's rows still hold takes their new time
        (an edit moves its set), and failing that a new set is made (an edit splits a row off a
        shared set). A set no row holds any more is deleted. A row of someone else's that still
        points at this person's set (an orientor changed by hand) is let go first, for that
        person's own regroup to place. Only the ``hours`` key of a kind row is written, by
        ``update()``, so no save side effect runs.
        """
        with transaction.atomic():
            lock_person(member_id)
            groups: dict[TimeKey, list[tuple[Any, int, int | None]]] = defaultdict(list)
            for model, person in _kind_rows():
                model.objects.filter(hours__member_id=member_id).exclude(**{person: member_id}).update(hours=None)
                for row in model.objects.filter(**{person: member_id}):
                    groups[Hours.key_of(row)].append((model, row.pk, row.hours_id))
            sets = {hours.pk: hours for hours in self.filter(member_id=member_id)}
            placed: dict[TimeKey, Hours] = {}
            for key, rows in groups.items():
                same = sorted(hours_id for _, _, hours_id in rows if hours_id in sets and sets[hours_id].key == key)
                if same:
                    placed[key] = sets[same[0]]
            kept = {hours.pk for hours in placed.values()}
            for key, rows in groups.items():
                if key in placed:
                    continue
                held = sorted({hours_id for _, _, hours_id in rows if hours_id in sets and hours_id not in kept})
                hours = sets[held[0]] if held else Hours(member_id=member_id)
                hours.set_key(key)
                hours.save()
                placed[key] = hours
                kept.add(hours.pk)
            for key, rows in groups.items():
                for model, pk, hours_id in rows:
                    if hours_id != placed[key].pk:
                        model.objects.filter(pk=pk).update(hours=placed[key])
            self.filter(member_id=member_id).exclude(pk__in=kept).delete()


class Hours(models.Model):
    """One set of a person's hours: the time, once, whatever kinds it is open to.

    No ``date`` repeats on ``weekday`` by ``cadence`` (counted from ``anchor_date``, the way
    ``OrientationAvailability.occurs_on`` counts it); a ``date`` is that one day only, its
    ``weekday`` stored equal to it and never on a cadence.
    """

    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="hours",
        help_text="The person whose time this is.",
    )
    date = models.DateField(
        null=True,
        blank=True,
        help_text="The one local day these hours are for. Blank for hours that repeat.",
    )
    weekday = models.PositiveSmallIntegerField(
        choices=OrientationAvailability.Weekday.choices,
        help_text="The day of the week these hours fall on (0 is Monday); for dated hours, the date's weekday.",
    )
    cadence = models.CharField(
        max_length=16,
        choices=OrientationAvailability.Cadence.choices,
        default=OrientationAvailability.Cadence.WEEKLY,
        help_text="How often repeating hours come round. Every cadence but weekly counts from anchor_date.",
    )
    anchor_date = models.DateField(
        null=True,
        blank=True,
        help_text="The first day repeating hours run, and where every cadence but weekly counts from.",
    )
    last_of_month = models.BooleanField(
        default=False,
        help_text="Month based cadences only: the last of this weekday in the month rather than the start day's place.",
    )
    start_time = models.TimeField(help_text="When the hours start (From).")
    end_time = models.TimeField(help_text="When the hours end (Until).")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When this set of hours was first made.")

    objects = HoursManager()

    class Meta:
        ordering: ClassVar[list[str]] = ["weekday", "start_time"]
        verbose_name = "hours"
        verbose_name_plural = "hours"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(condition=Q(end_time__gt=models.F("start_time")), name="ck_hours_end_after_start"),
            models.CheckConstraint(
                condition=Q(date__isnull=True) | Q(weekday=models.F("date__iso_week_day") - 1),
                name="ck_hours_date_weekday",
            ),
            models.CheckConstraint(
                condition=Q(date__isnull=True)
                | Q(cadence=OrientationAvailability.Cadence.WEEKLY, anchor_date__isnull=True, last_of_month=False),
                name="ck_hours_dated_no_cadence",
            ),
        ]

    def __str__(self) -> str:
        day = (
            f"{self.date:%a %Y-%m-%d}"
            if self.date is not None
            else f"{self.get_cadence_display()}, {self.get_weekday_display()}"
        )
        return f"{self.member}: {day} {self.start_time:%H:%M} to {self.end_time:%H:%M}"

    @staticmethod
    def key_of(row: Any) -> TimeKey:
        """The time of a ``TourHours`` or ``OrientationAvailability`` row, as a set of hours would hold it.

        A tour row has no cadence of its own: it repeats every week, or is its one date.
        """
        return TimeKey(
            date=getattr(row, "date", None),
            weekday=row.weekday,
            cadence=getattr(row, "cadence", OrientationAvailability.Cadence.WEEKLY),
            anchor_date=getattr(row, "anchor_date", None),
            last_of_month=getattr(row, "last_of_month", False),
            start_time=row.start_time,
            end_time=row.end_time,
        )

    @property
    def key(self) -> TimeKey:
        """This set's own time."""
        return TimeKey(
            self.date, self.weekday, self.cadence, self.anchor_date, self.last_of_month, self.start_time, self.end_time
        )

    def set_key(self, key: TimeKey) -> None:
        """Take ``key``'s time (unsaved)."""
        for field, value in key._asdict().items():
            setattr(self, field, value)


class DayOffManager(models.Manager["DayOff"]):
    def recopy(self, member_id: int) -> None:
        """Make one person's days off match their tour and orientation days off, adding and removing rows to fit.

        Each ``TourDayOff`` is one day off as it is; each ``OrientationDayOff`` is a one day range,
        unless one of the same person's tour days off already covers that date. Rows that already
        match are kept.
        """
        from membership.models import OrientationDayOff
        from tours.models import TourDayOff

        with transaction.atomic():
            lock_person(member_id)
            ranges = list(TourDayOff.objects.filter(guide_id=member_id).values_list("starts_on", "ends_on", "note"))
            wanted = list(ranges)
            for day, note in OrientationDayOff.objects.filter(orienter_id=member_id).values_list("date", "note"):
                if not any(first <= day <= last for first, last, _ in ranges):
                    wanted.append((day, day, note))
            stale: list[int] = []
            for day_off in self.filter(member_id=member_id):
                found = (day_off.starts_on, day_off.ends_on, day_off.note)
                if found in wanted:
                    wanted.remove(found)
                else:
                    stale.append(day_off.pk)
            self.filter(pk__in=stale).delete()
            self.bulk_create(
                DayOff(member_id=member_id, starts_on=first, ends_on=last, note=note) for first, last, note in wanted
            )


class DayOff(models.Model):
    """A day, or a run of days, a person is away: one Days Off for every kind of hours they give."""

    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="days_off",
        help_text="The person who is away.",
    )
    starts_on = models.DateField(help_text="The first local day off.")
    ends_on = models.DateField(help_text="The last local day off. The same as the first for a single day.")
    note = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Optional: a word on why, for the person and the admins. Members and visitors never see it.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the day off was saved.")

    objects = DayOffManager()

    class Meta:
        ordering: ClassVar[list[str]] = ["starts_on"]
        verbose_name = "day off"
        verbose_name_plural = "days off"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(condition=Q(ends_on__gte=models.F("starts_on")), name="ck_dayoff_ends_after_start"),
        ]

    def __str__(self) -> str:
        first = f"{self.starts_on:%Y-%m-%d}"
        return (
            f"{self.member} off {first}"
            if self.ends_on == self.starts_on
            else f"{self.member} off {first} to {self.ends_on:%Y-%m-%d}"
        )
