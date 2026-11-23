"""Facility tours (#947): the house rules, each guide's weekly tour hours and days off, and bookings.

Not the in-app guided tours (``core/tours.py``, ``core.models.TourState``): those walk a member
around the portal. These are visits to the building by people curious about Past Lives, booked
on tours.pastlives.space from part 2 on.

Availability is never stored. :mod:`tours.availability` carves each guide's :class:`TourHours`
into tours on demand, so a change to the hours or to :class:`FacilityTourSettings` applies to the
very next read, with no nightly job.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from billing.fields import EncryptedCharField
from core.threads import ThreadMessage, ThreadPhoto
from membership.models import AdminCapability, Member

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.contrib.auth.models import User

#: The most seats one tour can hold, on the settings page and on every hours row.
MAX_SEATS = 50
#: The farthest ahead visitors can be allowed to book, in days.
MAX_HORIZON_DAYS = 365
#: The longest minimum notice or cancel cutoff, in hours (two weeks).
MAX_NOTICE_HOURS = 336

DEFAULT_CLOSED_MESSAGE = "We are not booking tours right now. Please check back soon."
#: Where tours meet, on the booking pages and in every visitor email.
TOUR_ADDRESS = "Past Lives Makerspace, 2808 SE 9th Ave, Portland, OR 97202"


#: ``datetime.date`` under a name ``TourHours.date`` (the field) does not shadow in the class body.
Day = date


def day_label(day: date) -> str:
    """``Sat, Nov 15``: how a single day reads on the My Hours tab and in its messages."""
    return f"{day:%a, %b} {day.day}"


def _length_label(minutes: int) -> str:
    """``1 hour``, ``45 minutes``: a tour length in the weekly editor's own words (its select's labels)."""
    from hub.forms import _SLOT_DURATION_CHOICES, _duration_choice_label

    labels = dict(_SLOT_DURATION_CHOICES)
    key = str(minutes)
    return labels[key] if key in labels else _duration_choice_label(minutes)


def _clock_label(value: time) -> str:
    """``11:00 AM``, ``12:30 PM``: the half hour selects' own words."""
    from hub.forms import _meeting_time_label  # hub.forms imports models across the apps

    return _meeting_time_label(value.hour, value.minute)


def guide_filter(prefix: str = "") -> Q:
    """Who gives tours, as a ``Q`` on ``Member`` (or on a relation to it through ``prefix``).

    An active member holding the Tour Guide permission, or an admin, who always has the Tour
    Guide abilities (the actual role, as ``hub.view_as.compute_actual_roles`` has it: an active
    ``fog_role`` admin, or a superuser account). A member who loses the permission stops being
    offered at once, with their hours kept should it come back.

    Args:
        prefix: the lookup path to the member, ``"guide__"`` from a row that points at one.

    Returns:
        The condition, to filter with ``.distinct()`` since the permission is a join.
    """
    active = Q(**{f"{prefix}status": Member.Status.ACTIVE})
    holds = Q(**{f"{prefix}admin_capabilities__capability": AdminCapability.Capability.TOUR_GUIDE})
    admin = Q(**{f"{prefix}fog_role": Member.FogRole.ADMIN})
    return (active & (holds | admin)) | Q(**{f"{prefix}user__is_superuser": True})


class FacilityTourSettings(models.Model):
    """The house rules for facility tours: a singleton (pk 1), edited on the Tours page's Settings tab.

    Named apart from ``hub.forms.TourSettingsForm``, the guided tours' one toggle. Read through
    :meth:`load`, which makes the row with its defaults the first time.
    """

    accepting_bookings = models.BooleanField(
        default=True,
        help_text="Accepting tour bookings. Off, the tours site shows the closed message instead of times.",
    )
    closed_message = models.CharField(
        max_length=300,
        default=DEFAULT_CLOSED_MESSAGE,
        help_text="What visitors read while tour bookings are closed.",
    )
    booking_horizon_days = models.PositiveSmallIntegerField(
        default=30,
        validators=[
            MinValueValidator(1, "Use 1 to 365 days."),
            MaxValueValidator(MAX_HORIZON_DAYS, "Use 1 to 365 days."),
        ],
        help_text="How many days ahead visitors can book. 30 means up to 30 days from today.",
    )
    min_notice_hours = models.PositiveSmallIntegerField(
        default=24,
        validators=[MaxValueValidator(MAX_NOTICE_HOURS, "Use 0 to 336 hours.")],
        help_text="How many hours before a tour it can still be booked.",
    )
    cancel_cutoff_hours = models.PositiveSmallIntegerField(
        default=24,
        validators=[MaxValueValidator(MAX_NOTICE_HOURS, "Use 0 to 336 hours.")],
        help_text="How many hours before a tour visitors can still cancel or reschedule it online.",
    )
    default_tour_minutes = models.PositiveSmallIntegerField(
        default=60,
        validators=[MinValueValidator(1)],
        help_text="The tour length a new row of tour hours starts with.",
    )
    default_seats = models.PositiveSmallIntegerField(
        default=6,
        validators=[
            MinValueValidator(1, "Use 1 to 50 seats."),
            MaxValueValidator(MAX_SEATS, "Use 1 to 50 seats."),
        ],
        help_text="The seats a new row of tour hours starts with.",
    )
    kiosk_key = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "The front desk kiosk's secret (#950): the lobby screen opens the kiosk link carrying it "
            "once, which keeps a cookie. Blank until an admin creates the link; rotating it locks out "
            "every screen holding the old one."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True, help_text="When the settings were last saved.")

    class Meta:
        verbose_name = "Tour Settings"
        verbose_name_plural = "Tour Settings"

    def __str__(self) -> str:
        return "Tour Settings"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.pk = 1
        if FacilityTourSettings.objects.filter(pk=1).exists():
            kwargs.pop("force_insert", None)
            kwargs["force_update"] = True
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> FacilityTourSettings:
        """The one settings row, made with its defaults on first use."""
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def rotate_kiosk_key(self) -> str:
        """Make a new kiosk key and save it (#950): every screen holding the old one is locked out.

        Returns:
            The new key, for the kiosk link.
        """
        self.kiosk_key = secrets.token_urlsafe(32)
        self.save(update_fields=["kiosk_key", "updated_at"])
        return self.kiosk_key

    @property
    def kiosk_fingerprint(self) -> str:
        """What the kiosk's cookie carries (signed): a digest of the key, never the key itself.

        Empty while no key is set, which no cookie can match.
        """
        if not self.kiosk_key:
            return ""
        return hashlib.sha256(self.kiosk_key.encode()).hexdigest()


class TourHoursQuerySet(models.QuerySet["TourHours"]):
    def offered(self) -> TourHoursQuerySet:
        """Rows whose guide gives tours today (:func:`guide_filter`): the only hours visitors see.

        The permission join stays in a subquery, so the outer query needs no DISTINCT and can be
        locked with ``select_for_update`` (PostgreSQL refuses FOR UPDATE with DISTINCT).
        """
        return self.filter(guide__in=Member.objects.filter(guide_filter()))

    def for_guide(self, guide: Member) -> TourHoursQuerySet:
        """One guide's rows, in week order."""
        return self.filter(guide=guide).order_by("weekday", "start_time")

    def weekly(self) -> TourHoursQuerySet:
        """Rows that repeat every week (no ``date``): the Every Week editor's rows."""
        return self.filter(date__isnull=True)

    def dated(self) -> TourHoursQuerySet:
        """Rows for one date each (#1160): the Available On list's rows."""
        return self.filter(date__isnull=False)

    def upcoming_dated(self) -> TourHoursQuerySet:
        """Dated rows from today on, soonest first: what the Available On list shows. Past ones stay stored."""
        return self.filter(date__gte=timezone.localdate()).order_by("date", "start_time")

    def touching_days(self, first: date, last: date) -> TourHoursQuerySet:
        """Every weekly row, and the dated rows from ``first`` to ``last``: what can yield a tour in that range."""
        return self.filter(Q(date__isnull=True) | Q(date__range=(first, last)))


def lock_guide_hours(guide: Member) -> None:
    """Hold one guide's hours still until the transaction ends (#1160): ``SELECT ... FOR NO KEY UPDATE`` on their member row.

    Every save of a guide's hours (the Every Week formset, Add Dates) takes this before it checks
    for overlaps and keeps the check and the write in the same transaction, so two saves at once
    (a guide and an admin, two tabs) queue here and the second sees the first's rows. The member
    row, not the hours rows: a save onto a weekday or date with no hours yet would find no hours
    row to lock, and both saves would land. NO KEY, because a booking's insert takes FOR KEY SHARE
    on its guide's row while holding the hours rows, and a plain FOR UPDATE here would deadlock it.

    Raises:
        RuntimeError: outside a transaction, where the lock would end at once.
    """
    from django.db import transaction

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("lock_guide_hours must run inside transaction.atomic().")
    list(Member.objects.select_for_update(no_key=True).filter(pk=guide.pk).values_list("pk", flat=True))


class TourHours(models.Model):
    """One window in which a guide gives tours, carved into tours of ``tour_minutes`` each.

    Tuesday 4:00 to 6:00 PM with 60 minute tours is two tours, at 4:00 and 5:00, each with
    ``seats`` places. Two guides with the same hours offer two separate tours. One guide's rows
    may not overlap (``TourHoursFormSet`` and ``TourDatesForm`` refuse it), so a guide and a start
    time name at most one row, which is what a booking locks (:func:`tours.availability.open_tour_at`).

    A row with no ``date`` repeats every week on ``weekday``. A row with a ``date`` (#1160) is that
    one day only; its ``weekday`` is still stored, equal to the date's (a check constraint holds
    the two together), so the booking lock's weekday query covers it unchanged.
    """

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    guide = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="tour_hours",
        help_text="The person who gives the tours in this window.",
    )
    weekday = models.PositiveSmallIntegerField(
        choices=Weekday.choices,
        help_text="The day of the week these hours recur on (0 is Monday); for dated hours, the date's weekday.",
    )
    date = models.DateField(
        null=True,
        blank=True,
        help_text="The one local day these hours are for (#1160). Blank for hours that repeat every week.",
    )
    start_time = models.TimeField(help_text="When the first tour can start, on a half hour mark.")
    end_time = models.TimeField(help_text="When the last tour must be over, on a half hour mark.")
    tour_minutes = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
        help_text="How long each tour lasts. The window is cut into back to back tours this long.",
    )
    seats = models.PositiveSmallIntegerField(
        validators=[
            MinValueValidator(1, "Use 1 to 50 seats."),
            MaxValueValidator(MAX_SEATS, "Use 1 to 50 seats."),
        ],
        help_text="How many people fit on each tour in this window.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the row was added.")

    objects = TourHoursQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[list[str]] = ["weekday", "start_time"]
        verbose_name = "tour hours"
        verbose_name_plural = "tour hours"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(end_time__gt=models.F("start_time")), name="ck_tourhours_end_after_start"
            ),
            models.CheckConstraint(condition=Q(tour_minutes__gt=0), name="ck_tourhours_minutes_positive"),
            models.CheckConstraint(condition=Q(seats__gt=0), name="ck_tourhours_seats_positive"),
            # Dated hours keep their date's weekday (0 is Monday, ISO counts Monday as 1), so the
            # booking lock's weekday query finds them.
            models.CheckConstraint(
                condition=Q(date__isnull=True) | Q(weekday=models.F("date__iso_week_day") - 1),
                name="ck_tourhours_date_weekday",
            ),
        ]

    def __str__(self) -> str:
        day = f"{self.date:%a %Y-%m-%d}" if self.date is not None else self.get_weekday_display()
        return f"{self.guide.staff_label}: {day} {self.start_time:%H:%M} to {self.end_time:%H:%M}"

    def clean(self) -> None:
        """Half hour marks and room for at least one tour (the Django admin runs this too)."""
        super().clean()
        errors: dict[str, str] = {}
        for field in ("start_time", "end_time"):
            value: time | None = getattr(self, field)
            if value is not None and (value.minute % 30 or value.second or value.microsecond):
                errors[field] = "Tour hours line up on half hour marks, for example 4:00 or 4:30."
        if self.start_time is not None and self.end_time is not None:
            if self.end_time <= self.start_time:
                errors["end_time"] = "End time must be after the start time."
            elif self.tour_minutes and self.window_minutes < self.tour_minutes:
                errors["tour_minutes"] = "These hours are shorter than one tour."
        if errors:
            raise ValidationError(errors)

    @property
    def window_minutes(self) -> int:
        """The window's length in minutes."""
        return (self.end_time.hour * 60 + self.end_time.minute) - (self.start_time.hour * 60 + self.start_time.minute)

    def overlaps(self, other: TourHours) -> bool:
        """Whether ``other`` can fall on the same day and shares any minute with this window.

        Two weekly rows clash on the same weekday; a dated row clashes with a weekly row of its
        weekday and with another dated row of the same date; two different dates never do.
        """
        if self.weekday != other.weekday:
            return False
        if self.date is not None and other.date is not None and self.date != other.date:
            return False
        return self.start_time < other.end_time and other.start_time < self.end_time

    def applies_on(self, day: Day) -> bool:
        """Whether this row yields tours on ``day``: its weekday for weekly hours, its date for dated ones."""
        if self.date is not None:
            return self.date == day
        return self.weekday == day.weekday()

    @property
    def times_label(self) -> str:
        """``11:00 AM to 3:00 PM``."""
        return f"{_clock_label(self.start_time)} to {_clock_label(self.end_time)}"

    @property
    def dated_summary(self) -> str:
        """The Available On list's line (#1160), worded as the weekly rows are: ``Sat, Nov 15 · 11:00 AM to 3:00 PM · 1 hour each · 6 seats``."""
        if self.date is None:
            raise ValueError("Only dated tour hours have a dated summary.")
        seats = f"{self.seats} seat{'' if self.seats == 1 else 's'}"
        return f"{day_label(self.date)} · {self.times_label} · {_length_label(self.tour_minutes)} each · {seats}"

    def carve_spans(self, day: Day) -> list[tuple[datetime, datetime]]:
        """The aware local ``(start, end)`` of every tour this window yields on ``day``.

        Steps from ``start_time`` by ``tour_minutes`` on the wall clock while a whole tour still fits
        before ``end_time``, so a daylight saving day never shifts the grid (the rule
        ``OrientationAvailability.carve_spans`` follows). On the two change days the wall clock and
        real time part ways, so each tour is checked in real time: a start the clocks skip (2:00 AM
        on the spring day) is dropped, since it would be the same instant as 3:00; every tour lasts
        exactly ``tour_minutes``, its end counted from its start rather than read off the wall
        (1:00 to 2:00 AM on the fall day is two real hours); and a tour whose real end lands past
        ``end_time`` on the wall is dropped. The caller checks the weekday.
        """
        cursor = datetime.combine(day, self.start_time)
        window_end = datetime.combine(day, self.end_time)
        length = timedelta(minutes=self.tour_minutes)
        spans: list[tuple[datetime, datetime]] = []
        while cursor + length <= window_end:
            # Through UTC throughout: within one zone Python does wall clock arithmetic (aware +
            # timedelta), and astimezone to the same zone returns the value unchanged.
            start_utc = timezone.make_aware(cursor).astimezone(UTC)
            start = timezone.localtime(start_utc)
            end = timezone.localtime(start_utc + length)
            exists = start.replace(tzinfo=None) == cursor
            if exists and timezone.localtime(end).replace(tzinfo=None) <= window_end:
                spans.append((start, end))
            cursor += length
        return spans


class TourDayOffQuerySet(models.QuerySet["TourDayOff"]):
    def upcoming(self) -> TourDayOffQuerySet:
        """Days off that have not ended yet, soonest first: the ones that still change what visitors see."""
        return self.filter(ends_on__gte=timezone.localdate()).order_by("starts_on")

    def touching(self, first: date, last: date) -> TourDayOffQuerySet:
        """Days off that cover any day from ``first`` to ``last`` (both included)."""
        return self.filter(starts_on__lte=last, ends_on__gte=first)

    def covered_days(self, first: date, last: date) -> list[date]:
        """Every day from ``first`` to ``last`` one of these days off covers, in order (one query)."""
        days: set[date] = set()
        for day_off in self.touching(first, last):
            day = max(day_off.starts_on, first)
            while day <= min(day_off.ends_on, last):
                days.add(day)
                day += timedelta(days=1)
        return sorted(days)


class TourDayOff(models.Model):
    """A day, or a run of days, a guide is away: their hours offer no tours on any of them.

    Tours already booked on those days stay booked; a guide or an admin moves or cancels them
    (part 3). Deleting the row offers the days again.
    """

    guide = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="tour_days_off",
        help_text="The person who is away.",
    )
    starts_on = models.DateField(help_text="The first local day off.")
    ends_on = models.DateField(help_text="The last local day off. The same as the first for a single day.")
    note = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Optional: a word on why, for the guide and the admins. Visitors never see it.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the day off was saved.")

    objects = TourDayOffQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[list[str]] = ["starts_on"]
        verbose_name = "tour day off"
        verbose_name_plural = "tour days off"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(ends_on__gte=models.F("starts_on")), name="ck_tourdayoff_ends_after_start"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.guide} off {self.label}"

    @property
    def label(self) -> str:
        """``Tue, Dec 23`` for one day, ``Mon, Dec 22 to Fri, Jan 2`` for a run of days."""
        first = f"{self.starts_on:%a, %b} {self.starts_on.day}"
        if self.ends_on == self.starts_on:
            return first
        return f"{first} to {self.ends_on:%a, %b} {self.ends_on.day}"

    def covers(self, day: date) -> bool:
        """Whether ``day`` is one of these days off."""
        return self.starts_on <= day <= self.ends_on


#: A request (#1166) must be confirmed this many hours before its tour, or it lapses.
CONFIRM_BEFORE_HOURS = 24


class TourGuideSettings(models.Model):
    """One guide's own tour choices (#1166): whether a visitor's booking waits for their Confirm.

    Made the first time a guide saves the choice on My Hours. A guide with no row confirms
    automatically, as every guide did before, so nothing changes until a guide picks Confirm First.
    """

    class ConfirmMode(models.TextChoices):
        AUTOMATIC = "automatic", "Confirm Automatically"
        FIRST = "first", "Confirm First"

    guide = models.OneToOneField(
        Member,
        on_delete=models.CASCADE,
        related_name="tour_guide_settings",
        help_text="The Tour Guide these choices are for.",
    )
    confirm_mode = models.CharField(
        max_length=12,
        choices=ConfirmMode.choices,
        default=ConfirmMode.AUTOMATIC,
        help_text=(
            "Confirm Automatically books a visitor at once. Confirm First holds their seats as a request "
            "until the guide confirms or declines it. The front desk kiosk always books at once."
        ),
    )
    min_notice_hours = models.PositiveSmallIntegerField(
        default=48,
        validators=[
            MinValueValidator(
                CONFIRM_BEFORE_HOURS + 1,
                message="More than 24 hours, so there is time to answer before the confirm by time.",
            ),
            MaxValueValidator(336, message="At most 336 hours (two weeks)."),
        ],
        help_text=(
            "Under Confirm First, how far ahead a visitor must book on the website (#1166). Never below Tour "
            "Settings' minimum notice, and over the 24 hours before a tour by which a request must be confirmed."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True, help_text="When the guide last changed these choices.")

    class Meta:
        verbose_name = "tour guide settings"
        verbose_name_plural = "tour guide settings"

    def __str__(self) -> str:
        return f"{self.guide} ({self.get_confirm_mode_display()})"

    @property
    def summary(self) -> str:
        """What the choice means for the guide's next booking, in one sentence."""
        if self.confirm_mode == self.ConfirmMode.FIRST:
            return (
                f"Visitors book at least {self.min_notice_hours} hours ahead, and each booking waits for you "
                "to confirm or decline it."
            )
        return "New bookings are confirmed at once, and you are told."

    @classmethod
    def notice_hours_by_guide(cls, guide_ids: Iterable[int], house: FacilityTourSettings) -> dict[int, int]:
        """The minimum notice, in hours, of each listed guide who confirms first: their own, never below the house's.

        Guides who confirm automatically are left out; the house notice is theirs. One query.
        """
        rows = cls.objects.filter(guide_id__in=list(guide_ids), confirm_mode=cls.ConfirmMode.FIRST)
        return {
            guide_id: max(hours, house.min_notice_hours)
            for guide_id, hours in rows.values_list("guide_id", "min_notice_hours")
        }

    @classmethod
    def confirm_mode_for(cls, guide: Member) -> str:
        """The guide's :class:`ConfirmMode`: Confirm Automatically when they never chose."""
        mode = cls.objects.filter(guide=guide).values_list("confirm_mode", flat=True).first()
        return mode or cls.ConfirmMode.AUTOMATIC

    @classmethod
    def confirms_first(cls, guide: Member) -> bool:
        """Whether a visitor's booking of this guide's tour waits as a request for their Confirm."""
        return cls.confirm_mode_for(guide) == cls.ConfirmMode.FIRST


def _new_self_serve_token() -> str:
    """The random token in a booking's reschedule and cancel links."""
    return secrets.token_urlsafe(32)


class TourBookingQuerySet(models.QuerySet["TourBooking"]):
    def holding_seats(self) -> TourBookingQuerySet:
        """Bookings that count against a tour's seats: booked, attended, or a request awaiting its guide (#1166)."""
        return self.filter(status__in=TourBooking.HOLDS_SEATS)

    def seats_taken(self, guide: Member, starts_at: datetime) -> int:
        """How many seats one guide's tour at ``starts_at`` already holds."""
        total = self.holding_seats().filter(guide=guide, starts_at=starts_at).aggregate(total=Sum("party_size"))
        return int(total["total"] or 0)

    def seats_taken_by_tour(
        self, guide_ids: Iterable[int], first: datetime, before: datetime
    ) -> dict[tuple[int, datetime], int]:
        """Seats held per ``(guide id, start)`` for tours starting from ``first`` up to ``before``, in one query."""
        rows = (
            self.holding_seats()
            .filter(guide_id__in=list(guide_ids), starts_at__gte=first, starts_at__lt=before)
            .values("guide_id", "starts_at")
            .annotate(total=Sum("party_size"))
        )
        # Keyed in UTC: a local start in the repeated fall back hour never equals the same
        # instant read back from the database (PEP 495), so callers key their tours in UTC too.
        return {(row["guide_id"], row["starts_at"].astimezone(UTC)): int(row["total"]) for row in rows}

    def upcoming(self, now: datetime) -> TourBookingQuerySet:
        """The Tours page's Upcoming tab (#949): booked tours and requests (#1166) that have not started, soonest first."""
        return self.filter(status__in=TourBooking.UPCOMING, starts_at__gt=now).order_by("starts_at", "pk")

    def awaiting_guide(self, guide: Member, now: datetime) -> TourBookingQuerySet:
        """Home's Tour Requests Waiting on You (#1213): ``guide``'s own requests still answerable, soonest first.

        A request whose confirm by time has passed is left out even before its lapse task runs, so
        a row never offers an answer :func:`tours.booking.confirm_tour_request` would refuse.
        """
        return self.filter(
            guide=guide,
            status=TourBooking.Status.PENDING,
            starts_at__gt=now + timedelta(hours=CONFIRM_BEFORE_HOURS),
        ).order_by("starts_at", "pk")

    def booked_on_day(self, day: date) -> TourBookingQuerySet:
        """The kiosk's list (#950): tours still booked that start on the local ``day``, by time then name.

        Cancelled, attended and no show tours never appear, nor any other day's.
        """
        first = timezone.make_aware(datetime.combine(day, time.min))
        after = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))
        return self.filter(status=TourBooking.Status.BOOKED, starts_at__gte=first, starts_at__lt=after).order_by(
            "starts_at", "visitor_name", "pk"
        )

    def past(self, now: datetime) -> TourBookingQuerySet:
        """The Tours page's Past tab (#949): tours that have started and were not cancelled, newest first.

        Booked ones are waiting to be marked attended or no show. A request its guide never answered
        (#1166) never became a tour, so it is left out.
        """
        return (
            self.filter(starts_at__lte=now)
            .exclude(status__in=[TourBooking.Status.CANCELLED, TourBooking.Status.PENDING])
            .order_by("-starts_at", "-pk")
        )


#: How the guide reads each booking question (feedback request #32): ``(label, TourBooking field)``.
GUEST_ANSWER_LABELS = (
    ("Pronouns", "pronouns"),
    ("Found us through", "found_us_through"),
    ("Referred by", "referred_by"),
    ("What brings them here", "guest_notes"),
)


class FoundUs(models.TextChoices):
    """How a visitor learned about Past Lives (feedback request #32), each value its own label.

    Each value is its label, so a booking reads the same in the database, the guide's email and the
    calendar event. SimplyBook's words, except that its friend or family and colleague or coworker
    are one answer, "Someone told me about it!" (Morlock, 2026-11-22): together, the ~1,100 old
    bookings' 90 compare with it.
    """

    GOOGLE_SEARCH = "Google Search", "Google Search"
    GOOGLE_MAPS = "Google Maps", "Google Maps"
    # Morlock, 2026-11-22: friend or family and colleague or coworker are one answer, word of mouth.
    TOLD = "Someone told me about it!", "Someone told me about it!"
    INSTAGRAM = "Instagram", "Instagram"
    FACEBOOK = "Facebook", "Facebook"
    REDDIT = "Reddit", "Reddit"
    ORG = "Another makerspace or community org", "Another makerspace or community org"
    PASSED_BY = "Walked or drove by", "Walked or drove by"
    EVENT = "Attended an event here", "Attended an event here"
    OTHER = "Other", "Other"


class TourBooking(models.Model):
    """One party booked on one guide's tour.

    A tour is a guide and a start time; its seats come from the hours that yield it, and the
    seats taken are the sum of ``party_size`` over its bookings that are booked or attended
    (:meth:`TourBookingQuerySet.seats_taken`). No screen makes one before part 2, which books
    through :func:`tours.availability.open_tour_at` with ``lock=True`` so two visitors racing for
    the last seat cannot both get it.
    """

    class Status(models.TextChoices):
        BOOKED = "booked", "Booked"
        CANCELLED = "cancelled", "Cancelled"
        ATTENDED = "attended", "Attended"
        NO_SHOW = "no_show", "No show"
        PENDING = "pending", "Requested"

    class Source(models.TextChoices):
        WEBSITE = "website", "Website"
        KIOSK = "kiosk", "Kiosk"

    #: The statuses that take seats on the tour.
    HOLDS_SEATS: ClassVar[tuple[str, ...]] = (Status.BOOKED, Status.ATTENDED, Status.PENDING)
    #: The statuses the Upcoming tab lists and a visitor may still cancel (#1166).
    UPCOMING: ClassVar[tuple[str, ...]] = (Status.BOOKED, Status.PENDING)

    guide = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tour_bookings",
        help_text="The guide giving this tour. Empty when nobody is assigned.",
    )
    starts_at = models.DateTimeField(help_text="When the tour starts.")
    ends_at = models.DateTimeField(help_text="When the tour ends.")
    party_size = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(MAX_SEATS)],
        help_text="How many people are coming: the seats this booking takes.",
    )
    visitor_name = models.CharField(max_length=200, help_text="The name the visitor booked under.")
    visitor_email = models.EmailField(help_text="Where the confirmation, reminder and change emails go.")
    visitor_phone = models.CharField(max_length=40, blank=True, default="", help_text="Optional phone number.")
    # The booking questions SimplyBook asked (feedback request #32), each as the visitor typed or
    # picked it and blank when skipped. The calendar event (tours.calendar_sync.GUEST_ANSWERS, #33)
    # reads them by these names.
    pronouns = models.CharField(max_length=60, blank=True, default="", help_text="The visitor's pronouns.")
    found_us_through = models.CharField(
        max_length=60,
        blank=True,
        default="",
        choices=FoundUs.choices,
        help_text="How they found Past Lives, in SimplyBook's words so the old bookings compare.",
    )
    referred_by = models.CharField(
        max_length=200, blank=True, default="", help_text="Who here referred them, as they typed it."
    )
    # One open question (Morlock, 2026-11-22: it replaced "most interested in" and "anything else"):
    # what they make and what they hope to find here. Named guest_notes because the calendar event
    # (tours.calendar_sync.GUEST_ANSWERS, #33) already reads it.
    guest_notes = models.TextField(
        blank=True,
        default="",
        help_text="What brings them to Past Lives: what they make, and what they hope to find here.",
    )
    # Two boxes on the booking form, both unticked to start (Morlock, reviewing #32): a studio
    # lead for the guide, and a newsletter opt-in (feedback request #7) that subscribes the
    # visitor in Mailchimp (tours.tasks.subscribe_tour_visitor). The opt-in is kept as the record
    # of their consent.
    wants_studio = models.BooleanField(default=False, help_text="They ticked interest in a dedicated studio space.")
    studio_description = models.TextField(
        blank=True, default="", help_text="Their ideal studio and what they'd do with it, asked once they tick the box."
    )
    wants_newsletter = models.BooleanField(default=False, help_text="They asked to join the Past Lives newsletter.")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tour_bookings",
        help_text="The visitor's account, when they have one.",
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.BOOKED,
        help_text=(
            "Booked, attended and requested (awaiting the guide's Confirm, #1166) take seats; "
            "cancelled and no show do not."
        ),
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who cancelled it. Empty for a visitor cancelling from the email link, or while not cancelled.",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True, help_text="When it was cancelled.")
    self_serve_token = models.CharField(
        max_length=64,
        unique=True,
        default=_new_self_serve_token,
        help_text="The random token in the visitor's reschedule and cancel links.",
    )
    sequence = models.PositiveIntegerField(
        default=0,
        help_text=(
            "The calendar invite's SEQUENCE: raised on every move and cancel, so the visitor's "
            "calendar replaces the copy it holds (the UID never changes)."
        ),
    )
    source = models.CharField(
        max_length=12,
        choices=Source.choices,
        default=Source.WEBSITE,
        help_text="How it was booked: on the tours website, or at the front desk kiosk (#950).",
    )
    checked_in_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the visitor checked in at the front desk kiosk (#950). Apart from attended: only "
            "the guide marks that."
        ),
    )
    request_group = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Set on the rows of one No preference request (#1255): one pending row per guide offering that "
            "time, the first guide to confirm gets it. Cleared on the row a guide confirms."
        ),
    )
    # --- Ask the visitor first (#1283) ---------------------------------------------------------
    proposed_guide = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tour_times_offered",
        help_text=(
            "Whose tour a guide or admin asked to move this booking to (#1283), awaiting the visitor's "
            "answer. Empty when no proposal is open."
        ),
    )
    proposed_starts_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The new start a guide or admin asked the visitor to accept (#1283). Empty when none is open.",
    )
    proposed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the open proposal was made. Its Accept and Keep links carry it, so a replaced "
            "proposal's links say it is no longer open."
        ),
    )
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tour_times_proposed",
        help_text="The guide or admin who proposed the open new time; they hear the visitor's answer.",
    )
    notes_moved_to = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=(
            "Set on a No preference request's row (#1255) whose notes moved to another row of it when the "
            "request resolved (#1274): its Notes open, and new notes land on, that row."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was booked.")

    objects = TourBookingQuerySet.as_manager()

    #: The columns one proposal lives in (#1283), saved together.
    PROPOSAL_FIELDS: ClassVar[tuple[str, ...]] = ("proposed_guide", "proposed_starts_at", "proposed_at", "proposed_by")

    #: The first names of the other guides a shared request (#1255) also went to, for the Upcoming row;
    #: filled by :meth:`attach_shared_with`, never stored.
    shared_with: tuple[str, ...] = ()

    class Meta:
        ordering: ClassVar[list[str]] = ["starts_at"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["guide", "starts_at"],
                condition=Q(status__in=["booked", "attended", "pending"]),
                name="ix_tourbooking_seats",
            ),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=models.F("starts_at")), name="ck_tourbooking_end_after_start"
            ),
            models.CheckConstraint(condition=Q(party_size__gt=0), name="ck_tourbooking_party_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.visitor_name} ({self.party_size}) {timezone.localtime(self.starts_at):%Y-%m-%d %H:%M}"

    @property
    def holds_seats(self) -> bool:
        """Whether this booking counts against its tour's seats."""
        return self.status in self.HOLDS_SEATS

    @property
    def confirm_by(self) -> datetime:
        """When a request (#1166) lapses unless its guide answers: 24 hours before the tour, in UTC."""
        return self.starts_at.astimezone(UTC) - timedelta(hours=CONFIRM_BEFORE_HOURS)

    @property
    def is_request(self) -> bool:
        """Whether this is a request still waiting for its guide to confirm or decline it (#1166)."""
        return self.status == self.Status.PENDING

    @property
    def ics_uid(self) -> str:
        """The calendar invite's UID: the same for the booking's whole life, through every move."""
        return f"tour-{self.pk}@pastlives"

    @property
    def visitor_first_name(self) -> str:
        """The first word of the name the visitor booked under, for a greeting."""
        return self.visitor_name.split()[0] if self.visitor_name.strip() else self.visitor_name

    @property
    def kiosk_name(self) -> str:
        """The name the kiosk lists (#950): first name and last initial, ``Sam R.``, or the one word given."""
        words = self.visitor_name.split()
        if len(words) < 2:
            return self.visitor_name.strip()
        return f"{words[0]} {words[-1][0].upper()}."

    @property
    def guide_first_name(self) -> str:
        """The guide's first name as visitors see it (#1273, the kiosk's pick list and Is This You), or ""."""
        from tours.notify import guide_first_name  # notify imports this module

        return guide_first_name(self.guide)

    @property
    def party_label(self) -> str:
        """``1 person`` or ``3 people``."""
        return "1 person" if self.party_size == 1 else f"{self.party_size} people"

    @property
    def guest_answers(self) -> list[tuple[str, str]]:
        """The booking questions answered (feedback request #32), as ``(label, answer)`` in order,
        for the guide's email and the Upcoming row; a skipped question is left out, and a ticked
        studio box reads "Interested in a studio: Yes"."""
        answers = [(label, str(getattr(self, name)).strip()) for label, name in GUEST_ANSWER_LABELS]
        if self.wants_studio:
            answers.append(("Interested in a studio", "Yes"))
            answers.append(("Ideal studio", self.studio_description.strip()))
        return [(label, answer) for label, answer in answers if answer]

    @property
    def visitor_phone_href(self) -> str:
        """The phone number as a ``tel:`` link wants it: digits, and a leading plus when typed."""
        digits = "".join(char for char in self.visitor_phone if char.isdigit())
        return f"+{digits}" if self.visitor_phone.strip().startswith("+") else digits

    @property
    def status_badge(self) -> str:
        """The ``pl-status-badge`` tone for this status on the Tours page (#949)."""
        tones = {
            self.Status.BOOKED: "muted",
            self.Status.ATTENDED: "ok",
            self.Status.NO_SHOW: "fail",
            self.Status.CANCELLED: "muted",
            self.Status.PENDING: "warn",
        }
        return tones[self.Status(self.status)]

    def staff_refusal(self, now: datetime) -> str:
        """Why a Tour Guide or an admin may not move or cancel this tour (#949), or "" when they may.

        ``"cancelled"``, or ``"past"`` once it has started or been marked. No cancel cutoff binds staff.
        """
        if self.status == self.Status.CANCELLED:
            return "cancelled"
        if self.status != self.Status.BOOKED or self.starts_at <= now:
            return "past"
        return ""

    @property
    def is_shared_request(self) -> bool:
        """Whether this row belongs to a No preference request (#1255); the row a guide confirms leaves it."""
        return self.request_group is not None

    def was_taken_by_another_guide(self) -> bool:
        """Whether another guide confirmed this shared request first (#1255): their Confirm cancelled this row."""
        return (
            self.is_shared_request
            and self.status == self.Status.CANCELLED
            and self.cancelled_by_id is not None
            and (self.guide is None or self.cancelled_by_id != self.guide.user_id)
        )

    def take_visitor_link_from(self, other: TourBooking) -> None:
        """Move ``other``'s self serve token (the link in the visitor's emails) onto this row (#1255).

        ``other`` gets a fresh token nobody holds. Both rows are saved; call it under the group's lock.
        """
        token = other.self_serve_token
        other.self_serve_token = _new_self_serve_token()
        other.save(update_fields=["self_serve_token"])
        self.self_serve_token = token
        self.save(update_fields=["self_serve_token"])

    @classmethod
    def attach_shared_with(cls, bookings: list[TourBooking]) -> None:
        """Fill each shared request's :attr:`shared_with` with the other pending guides' first names, in one query."""
        from tours.notify import guide_first_name

        groups = {booking.request_group for booking in bookings if booking.is_shared_request}
        if not groups:
            return
        rows = cls.objects.filter(request_group__in=groups, status=cls.Status.PENDING).select_related("guide")
        by_group: dict[Any, list[TourBooking]] = {}
        for row in rows.order_by("pk"):
            by_group.setdefault(row.request_group, []).append(row)
        for booking in bookings:
            if booking.is_shared_request:
                booking.shared_with = tuple(
                    guide_first_name(row.guide)
                    for row in by_group.get(booking.request_group, [])
                    if row.pk != booking.pk
                )

    @property
    def has_open_proposal(self) -> bool:
        """Whether a new time a guide or admin proposed (#1283) waits on the visitor's answer."""
        return self.proposed_at is not None

    @property
    def proposal_key(self) -> str:
        """The open proposal's identity in its Accept and Keep links (#1283), or "" when none is open.

        Microseconds since the epoch of ``proposed_at``, so a link drawn for one proposal never
        matches the one that replaced it.
        """
        if self.proposed_at is None:
            return ""
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        return str((self.proposed_at - epoch) // timedelta(microseconds=1))

    def propose_time(self, guide: Member, starts_at: datetime, *, proposed_by: User, now: datetime) -> None:
        """Record ``guide``'s tour at ``starts_at`` as the new time to ask the visitor about, replacing any open one."""
        self.proposed_guide = guide
        self.proposed_starts_at = starts_at
        self.proposed_at = now
        self.proposed_by = proposed_by
        self.save(update_fields=list(self.PROPOSAL_FIELDS))

    def drop_proposal(self) -> None:
        """Forget the open proposal in memory; the caller saves :attr:`PROPOSAL_FIELDS`."""
        self.proposed_guide = None
        self.proposed_starts_at = None
        self.proposed_at = None
        self.proposed_by = None

    def clear_proposal(self) -> None:
        """The visitor kept their time, or the proposed time can no longer be had: drop the proposal."""
        self.drop_proposal()
        self.save(update_fields=list(self.PROPOSAL_FIELDS))

    def proposal_refusal(self, proposal: str, now: datetime) -> str:
        """Why an Accept or Keep drawn for ``proposal`` may not act on this booking (#1283), or "" when it may.

        ``"already"`` when the booking is no longer booked or has started, ``"stale"`` when that
        proposal was answered or replaced (or none was named, or its guide is gone), ``"passed"`` when the proposed time
        has come and gone. No cancel cutoff: the guide asked, so the visitor may answer up to the tour.
        """
        if self.status != self.Status.BOOKED or self.starts_at <= now:
            return "already"
        if not proposal or proposal != self.proposal_key or self.proposed_guide_id is None:
            # No proposed guide left (their member record was deleted) is a proposal nobody can give.
            return "stale"
        if self.proposed_starts_at is None or self.proposed_starts_at <= now:
            return "passed"
        return ""

    def table_url_name(self, now: datetime) -> str:
        """Which Tours table lists this booking (#1274): Upcoming until it starts, Past after."""
        return "tours:upcoming" if self.status in self.UPCOMING and self.starts_at > now else "tours:past"

    @property
    def note_target(self) -> TourBooking:
        """The booking whose Notes this row shows and takes (#1274): itself, or where its notes moved.

        :func:`tours.booking.gather_notes` points every other row of a resolved No preference
        request straight at the surviving row, so this is never more than one step.
        """
        return self.notes_moved_to if self.notes_moved_to is not None else self

    def note_thread(self) -> models.QuerySet[TourBookingNote]:
        """This booking's notes (#1274), oldest first; a No preference request's (#1255) are every row's.

        While a request is shared each guide's row is one copy of one visitor's request, so its
        Notes are one conversation; :func:`tours.booking.gather_notes` moves them onto the row
        that survives when the request resolves, and a row whose notes moved reads that row's.
        """
        target = self.note_target
        if target.request_group is not None:
            return TourBookingNote.objects.filter(booking__request_group=target.request_group)
        return TourBookingNote.objects.filter(booking=target)

    @property
    def may_email_visitor(self) -> bool:
        """Whether a note on this row may be emailed to the visitor (#1274).

        Not on a cancelled booking, and not while a No preference request is shared: its rows
        hide the guide's name from the visitor, and only one of them holds the visitor's link.
        """
        return self.status != self.Status.CANCELLED and not self.is_shared_request

    def self_serve_refusal(self, *, now: datetime, cancel_cutoff_hours: int) -> str:
        """Why the visitor may not move or cancel this tour from the email links, or "" when they may.

        ``"cancelled"`` for a cancelled tour, ``"past"`` for one that has started or been marked
        attended or no show, and ``"too_close"`` inside the cancel cutoff (Tour Settings). Checked
        on the GET that shows a page and again on the POST that acts, so a forged POST is refused
        the same way. A request (#1166) may be cancelled like a booking; only Reschedule waits for
        the guide to confirm it (:func:`tours.booking.reschedule_tour`).
        """
        if self.status == self.Status.CANCELLED:
            return "cancelled"
        if self.status not in self.UPCOMING or self.starts_at <= now:
            return "past"
        if self.starts_at - now < timedelta(hours=cancel_cutoff_hours):
            return "too_close"
        return ""


# --- Google Calendar per guide (#951) -----------------------------------------------------------


class TourCalendarConnectionQuerySet(models.QuerySet["TourCalendarConnection"]):
    def healthy(self) -> TourCalendarConnectionQuerySet:
        """Connections Google still answers for: the only ones whose busy times hide tours."""
        return self.filter(status=TourCalendarConnection.Status.CONNECTED)

    def busy_by_guide(self, guide_ids: Iterable[int]) -> dict[int, list[tuple[datetime, datetime]]]:
        """Each listed guide's cached busy spans (UTC), for those whose calendar hides tour times now.

        Empty while Google sign in is not set up (:func:`tours.google.is_configured`): a guide whose
        connection is broken, or whose busy times were never read, offers their hours as if
        unconnected. One query.
        """
        from tours import google

        if not google.is_configured():
            return {}
        rows = self.healthy().filter(guide_id__in=list(guide_ids), busy_checked_at__isnull=False)
        return {row.guide_id: row.busy_intervals() for row in rows.only("guide_id", "busy_spans")}


class TourCalendarConnection(models.Model):
    """A Tour Guide's Google Calendar (#951): tours they give are written to it, and the times it shows
    them busy are not offered to visitors.

    One per guide, made by the Connect Google Calendar flow on the My Hours tab (per user OAuth with
    offline access). The tokens are encrypted at rest with the same Fernet key as the Stripe secrets
    (``billing.fields.EncryptedCharField``, ``STRIPE_FIELD_ENCRYPTION_KEY``). Disconnect revokes the
    token at Google and deletes the row. When Google stops accepting the token (revoked, expired) the
    row turns ``broken``: its tokens are cleared, the guide is emailed once, and their hours offer as
    if unconnected until they reconnect.
    """

    class Status(models.TextChoices):
        CONNECTED = "connected", "Connected"
        BROKEN = "broken", "Needs reconnecting"

    guide = models.OneToOneField(
        Member,
        on_delete=models.CASCADE,
        related_name="tour_calendar",
        help_text="The Tour Guide whose Google Calendar this is.",
    )
    google_email = models.CharField(
        max_length=254, blank=True, default="", help_text="The Google account connected (its main calendar's id)."
    )
    refresh_token = EncryptedCharField(
        max_length=2048,
        blank=True,
        default="",
        help_text="Google's long lived token, encrypted at rest. Cleared when the connection breaks.",
    )
    access_token = EncryptedCharField(
        max_length=4096,
        blank=True,
        default="",
        help_text="Google's short lived token (about an hour), encrypted at rest, reused until it expires.",
    )
    access_token_expires_at = models.DateTimeField(
        null=True, blank=True, help_text="When the access token stops working."
    )
    calendar_id = models.CharField(
        max_length=255,
        default="primary",
        help_text="The calendar tours are written to: the guide's main calendar unless they picked another.",
    )
    calendars = models.JSONField(
        default=list,
        blank=True,
        help_text='The calendars the guide owns, for the picker: [{"id", "name", "primary"}]. Refreshed by the sync job.',
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.CONNECTED,
        help_text="Connected, or needs reconnecting once Google stopped accepting the token.",
    )
    broken_at = models.DateTimeField(null=True, blank=True, help_text="When Google stopped accepting the token.")
    broken_reason = models.CharField(
        max_length=300, blank=True, default="", help_text="What Google said when it stopped accepting the token."
    )
    busy_spans = models.JSONField(
        default=list,
        blank=True,
        help_text='The busy times read from Google over the booking horizon, as UTC ISO pairs [["start", "end"], ...].',
    )
    busy_checked_at = models.DateTimeField(
        null=True, blank=True, help_text="When the busy times were last read from Google."
    )
    connected_at = models.DateTimeField(default=timezone.now, help_text="When the guide last connected or reconnected.")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the row last changed.")

    objects = TourCalendarConnectionQuerySet.as_manager()

    class Meta:
        verbose_name = "tour guide Google Calendar"
        verbose_name_plural = "tour guide Google Calendars"

    def __str__(self) -> str:
        return f"{self.guide}: {self.google_email or 'Google Calendar'} ({self.get_status_display()})"

    @property
    def is_broken(self) -> bool:
        """Whether the guide has to reconnect before tours are written or busy times read again."""
        return self.status == self.Status.BROKEN

    def calendar_choices(self) -> list[tuple[str, str]]:
        """The picker's options, main calendar first: its value is ``primary``, the others' their Google id."""
        return [
            ("primary" if calendar.get("primary") else str(calendar["id"]), str(calendar.get("name") or calendar["id"]))
            for calendar in self.calendars
        ]

    @property
    def calendar_label(self) -> str:
        """The chosen calendar's name as the picker shows it, or "the main calendar" before the list is read."""
        names = dict(self.calendar_choices())
        if self.calendar_id in names:
            return names[self.calendar_id]
        return "the main calendar" if self.calendar_id == "primary" else self.calendar_id

    def busy_intervals(self) -> list[tuple[datetime, datetime]]:
        """The cached busy spans as aware UTC pairs, in order."""
        return [(datetime.fromisoformat(start), datetime.fromisoformat(end)) for start, end in self.busy_spans]


class TourCalendarSync(models.Model):
    """Where one booking's event lives on a guide's Google Calendar, and whether it needs writing (#951).

    Made when a booking whose guide has connected a calendar is booked, moved or cancelled
    (``tours.signals.tour_booking_changed``) and when a guide connects or picks another calendar. The
    ``sync_tour_calendars`` job brings every ``due`` row to what the booking says now: an event on the
    current guide's chosen calendar with the current times, or none for a cancelled tour; an event left
    on another guide's calendar (a reassignment) or another calendar is deleted first. A failure
    leaves the row due and waits ``next_attempt_at``; the booking itself never waits on Google.
    """

    booking = models.OneToOneField(
        TourBooking,
        on_delete=models.CASCADE,
        related_name="calendar_sync",
        help_text="The booking whose Google event this tracks.",
    )
    connection = models.ForeignKey(
        TourCalendarConnection,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="synced_events",
        help_text="Whose Google Calendar holds the event now. Empty when none does, or the guide disconnected.",
    )
    calendar_id = models.CharField(max_length=255, blank=True, default="", help_text="The calendar holding the event.")
    event_id = models.CharField(
        max_length=255, blank=True, default="", help_text="The Google event's id, for updates and deletes."
    )
    due = models.BooleanField(default=True, help_text="Google has to be brought up to date with the booking.")
    attempts = models.PositiveSmallIntegerField(default=0, help_text="Failed tries since the last success.")
    next_attempt_at = models.DateTimeField(
        null=True, blank=True, help_text="After a failure: the earliest the job tries again."
    )
    last_error = models.CharField(max_length=300, blank=True, default="", help_text="Why the last try failed.")
    synced_at = models.DateTimeField(null=True, blank=True, help_text="When Google last matched the booking.")
    generation = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Raised every time the booking changes and the row is queued again. The job clears due only "
            "while it still holds the generation it read, so a change made while it was writing is not lost."
        ),
    )
    claimed_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text="A run is writing this row until then; another run (Run now during a tick) leaves it alone.",
    )

    class Meta:
        verbose_name = "tour Google Calendar event"
        verbose_name_plural = "tour Google Calendar events"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["next_attempt_at"], condition=Q(due=True), name="ix_tourcalsync_due"),
        ]

    def __str__(self) -> str:
        return f"{self.booking}: {self.event_id or 'no event'}{' (due)' if self.due else ''}"


class TourCalendarCleanup(models.Model):
    """A tour event to delete from a guide's Google Calendar that no booking points at any more (#951).

    Made when a booking is deleted (the Django admin or a shell; normal flows cancel instead), when a
    write lands after its booking was deleted, and when a tour is reassigned away from a guide whose
    calendar is broken. The ``sync_tour_calendars`` job deletes the event once the connection works
    (at once, or after the guide reconnects) and then the row. Disconnecting drops the row with the
    connection: the token that could reach the event is gone.
    """

    connection = models.ForeignKey(
        TourCalendarConnection,
        on_delete=models.CASCADE,
        related_name="pending_cleanups",
        help_text="Whose Google Calendar holds the event.",
    )
    calendar_id = models.CharField(max_length=255, help_text="The calendar holding the event.")
    event_id = models.CharField(max_length=255, help_text="The Google event to delete.")
    attempts = models.PositiveSmallIntegerField(default=0, help_text="Failed tries since it was queued.")
    next_attempt_at = models.DateTimeField(
        null=True, blank=True, help_text="After a failure: the earliest the job tries again."
    )
    last_error = models.CharField(max_length=300, blank=True, default="", help_text="Why the last try failed.")
    claimed_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text="A run is deleting this event until then; another run leaves it alone.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the delete was queued.")

    class Meta:
        verbose_name = "tour Google Calendar event to delete"
        verbose_name_plural = "tour Google Calendar events to delete"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["connection", "calendar_id", "event_id"], name="uq_tourcalcleanup_event"),
        ]

    def __str__(self) -> str:
        return f"Delete {self.event_id} from {self.calendar_id}"


# --- Notes on a booking (#1274) ------------------------------------------------------------------


class TourBookingNote(ThreadMessage):
    """One note on a :class:`TourBooking`'s thread, by a Tour Guide or an admin (#1274).

    Every note is from the staff side: the visitor never posts here (no member side, so
    :attr:`member_user_ids` is empty). Any Tour Guide or admin reads and adds notes on any
    booking. Posting tells the booking's guide (or the admins when it has none) and everyone else
    who wrote a note on it, minus the author, by bell (:meth:`staff_user_ids`). A note posted with
    ``sent_to_visitor`` also emails the visitor (:func:`tours.notify.send_visitor_note`); only
    whoever may act on the booking may ask for that (``tours.views._staff_booking``).
    """

    THREAD_KIND: ClassVar[str] = "tour"
    NOTICE_PERIOD: ClassVar[str] = "tour-note-{pk}"
    STAFF_SIGNATURE: ClassVar[str] = "Tour Guide"

    # Set by posting: whether a guide or an admin got a bell. Not a column.
    told_staff: bool = False
    #: The thread's timestamp in the house style ("Sat, Nov 21 · 1:22 PM"); other threads keep their own.
    time_format: ClassVar[str] = "D, M j · g:i A"

    booking = models.ForeignKey(
        TourBooking,
        on_delete=models.CASCADE,
        related_name="notes",
        help_text="The booking this note is on.",
    )
    sent_to_visitor = models.BooleanField(
        default=False,
        help_text="Whether this note was also emailed to the visitor when it was posted.",
    )

    class Meta(ThreadMessage.Meta):
        pass

    def __str__(self) -> str:
        return f"Note {self.pk} on tour booking {self.booking_id}"

    @property
    def member_user_ids(self) -> set[int]:
        """Nobody: the visitor has no side on this thread, so every note is a staff note."""
        return set()

    @property
    def member_side_name(self) -> str:
        return self.booking.visitor_name

    @property
    def thread_marker(self) -> str:
        """``Emailed to the visitor``, and once changed since, that the visitor has the words as first sent."""
        if not self.sent_to_visitor:
            return ""
        return "Emailed to the visitor, edited after sending" if self.edited_at else "Emailed to the visitor"

    def staff_user_ids(self) -> set[int]:
        """Who a note reaches: the tour's guide (or the admins with none) and every earlier writer still giving tours, minus the author."""
        from core.events.resolvers import tour_guides

        booking = self.booking
        audience = {user.pk for user, _reason in tour_guides({"guide": booking.guide})}
        writer_ids = set(
            booking.note_thread().exclude(pk=self.pk).exclude(author=None).values_list("author_id", flat=True)
        )
        # An earlier writer hears only while they still give tours (a Tour Guide, an admin or a superuser).
        writers = set(
            Member.objects.filter(guide_filter(), user_id__in=writer_ids - audience).values_list("user_id", flat=True)
        )
        return (audience | {writer for writer in writers if writer is not None}) - {self.author_id}

    def _tell_member_side(self) -> bool:
        """Every note is staff side: email the visitor when asked, and bell the other staff on the booking."""
        from tours import notify

        told = False
        if self.sent_to_visitor:
            notify.send_visitor_note(self)
            told = True
        recipient_ids = self.staff_user_ids()
        if recipient_ids:
            notify.tell_staff_note(self, recipient_ids)
            self.told_staff = True
            told = True
        return told

    def _tell_staff_side(self) -> bool:
        """Never called: nobody writes as the visitor."""
        return False

    def told_sentence(self) -> str:
        """``We emailed it to Sam.`` and ``The other guides on this tour were told.``, as they happened."""
        sentence = f" We emailed it to {self.booking.visitor_first_name}." if self.sent_to_visitor else ""
        if self.told_staff:
            sentence += " The other guides on this tour were told."
        return sentence


class TourBookingNotePhoto(ThreadPhoto):
    """One photo posted with a :class:`TourBookingNote`; it stays on the thread, never in the visitor's email."""

    message = models.ForeignKey(
        TourBookingNote,
        on_delete=models.CASCADE,
        related_name="photos",
        help_text="The note this photo was posted with.",
    )
    image = models.ImageField(upload_to="tour-notes/%Y/%m/", help_text="The uploaded photo, on the default storage.")

    class Meta(ThreadPhoto.Meta):
        pass

    def __str__(self) -> str:
        return f"Photo {self.pk} on tour note {self.message_id}"
