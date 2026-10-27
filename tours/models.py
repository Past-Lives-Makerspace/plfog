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

from membership.models import AdminCapability, Member

if TYPE_CHECKING:
    from collections.abc import Iterable

#: The most seats one tour can hold, on the settings page and on every hours row.
MAX_SEATS = 50
#: The farthest ahead visitors can be allowed to book, in days.
MAX_HORIZON_DAYS = 365
#: The longest minimum notice or cancel cutoff, in hours (two weeks).
MAX_NOTICE_HOURS = 336

DEFAULT_CLOSED_MESSAGE = "We are not booking tours right now. Please check back soon."
#: Where tours meet, on the booking pages and in every visitor email.
TOUR_ADDRESS = "Past Lives Makerspace, 2808 SE 9th Ave, Portland, OR 97202"


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


class TourHours(models.Model):
    """One weekly window in which a guide gives tours, carved into tours of ``tour_minutes`` each.

    Tuesday 4:00 to 6:00 PM with 60 minute tours is two tours, at 4:00 and 5:00, each with
    ``seats`` places. Two guides with the same hours offer two separate tours. One guide's rows
    may not overlap (``TourHoursFormSet`` refuses it), so a guide and a start time name at most one
    row, which is what a booking locks (:func:`tours.availability.open_tour_at`).
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
        choices=Weekday.choices, help_text="The day of the week these hours recur on (0 is Monday)."
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
        ]

    def __str__(self) -> str:
        return (
            f"{self.guide.display_name}: {self.get_weekday_display()} {self.start_time:%H:%M} to {self.end_time:%H:%M}"
        )

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
        """Whether ``other`` falls on the same weekday and shares any minute with this window."""
        return self.weekday == other.weekday and self.start_time < other.end_time and other.start_time < self.end_time

    def carve_spans(self, day: date) -> list[tuple[datetime, datetime]]:
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


def _new_self_serve_token() -> str:
    """The random token in a booking's reschedule and cancel links."""
    return secrets.token_urlsafe(32)


class TourBookingQuerySet(models.QuerySet["TourBooking"]):
    def holding_seats(self) -> TourBookingQuerySet:
        """Bookings that count against a tour's seats: booked or attended."""
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
        """The Tours page's Upcoming tab (#949): booked tours that have not started, soonest first."""
        return self.filter(status=TourBooking.Status.BOOKED, starts_at__gt=now).order_by("starts_at", "pk")

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

        Booked ones are waiting to be marked attended or no show.
        """
        return (
            self.filter(starts_at__lte=now).exclude(status=TourBooking.Status.CANCELLED).order_by("-starts_at", "-pk")
        )


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

    class Source(models.TextChoices):
        WEBSITE = "website", "Website"
        KIOSK = "kiosk", "Kiosk"

    #: The statuses that take seats on the tour.
    HOLDS_SEATS: ClassVar[tuple[str, ...]] = (Status.BOOKED, Status.ATTENDED)

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
        help_text="Booked and attended take seats; cancelled and no show do not.",
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
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was booked.")

    objects = TourBookingQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[list[str]] = ["starts_at"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["guide", "starts_at"],
                condition=Q(status__in=["booked", "attended"]),
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
    def party_label(self) -> str:
        """``1 person`` or ``3 people``."""
        return "1 person" if self.party_size == 1 else f"{self.party_size} people"

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

    def self_serve_refusal(self, *, now: datetime, cancel_cutoff_hours: int) -> str:
        """Why the visitor may not move or cancel this tour from the email links, or "" when they may.

        ``"cancelled"`` for a cancelled tour, ``"past"`` for one that has started or been marked
        attended or no show, and ``"too_close"`` inside the cancel cutoff (Tour Settings). Checked
        on the GET that shows a page and again on the POST that acts, so a forged POST is refused
        the same way.
        """
        if self.status == self.Status.CANCELLED:
            return "cancelled"
        if self.status != self.Status.BOOKED or self.starts_at <= now:
            return "past"
        if self.starts_at - now < timedelta(hours=cancel_cutoff_hours):
            return "too_close"
        return ""
