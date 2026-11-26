"""My Hours, the page (#1300, part 3): one person's week and hours, every kind together, read only.

:class:`MyHours` is everything ``hours/my_hours.html`` draws for one person: the kinds they offer
(Tours, and each guild or tool whose orientations they give), a week of their time (open hours
dashed, bookings solid in their kind's colour, classes they teach and Google busy time hatched, a
day off striped), their Every Week and Available On hours with what each set is open to, their
Days Off, their Google Calendar and, for a guide, their Booking Notice.

The week's bookings, classes, Google busy time and days off come from
:func:`hours.busy.busy_holds`, the one rule for what holds a person's time; this module only names
them. Who has a My Hours page is :func:`hours_people_q`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from collections.abc import Iterable
from typing import TYPE_CHECKING

from django.db.models import Prefetch, Q
from django.utils import timezone

from classes.models import ClassSession
from hours.busy import CLASS, DAY_OFF, GOOGLE, ORIENTATION, SEAT_HOLDING, TOUR, Hold, busy_holds
from hours.models import DayOff, Hours
from hub.hour_grid import MINUTES_PER_DAY, Block, GridDay, HourGridWeek, Inner, clock, hour_range, lay_lanes
from membership.models import Member, OrientationAvailability, OrientationBooking, OrientationSlot
from tours import google
from tours.models import (
    FacilityTourSettings,
    TourBooking,
    TourCalendarConnection,
    TourGuideSettings,
    TourHours,
    guide_filter,
    notice_label,
)

if TYPE_CHECKING:
    from membership.models import OrientationType

TOURS_KEY = "tours"
#: The colour classes orientation kinds take in turn (``my-hours.css``); Tours is always gold.
KIND_CLASSES = ("pl-kind--a", "pl-kind--b", "pl-kind--c", "pl-kind--d")
OPEN, BOOKED, BUSY = "open", "booked", "busy"
#: Chips that start together read open, then booked, then busy.
_STYLE_ORDER = {OPEN: 0, BOOKED: 1, BUSY: 2}
#: What one day of the week's hour grid holds (#1332): the blocks, with bookings inside open hours.
GRID_DAY_TEMPLATE = "hours/partials/_hour_grid_day.html"


def hours_people_q() -> Q:
    """Who has a My Hours page, as a ``Q`` on ``Member`` (filter with ``.distinct()``; the roles are joins).

    Everyone who gives tours (``tours.models.guide_filter``: Tour Guides and admins) and every
    current orientor of a guild or tool: an active member who leads a guild, holds a staff role on
    one, or is on a tool's staff (``Guild.orientor_ids`` and ``Equipment.orientor_ids``, as rows).
    """
    orientor = Q(status=Member.Status.ACTIVE) & (
        Q(led_guilds__isnull=False) | Q(guild_staff_roles__isnull=False) | Q(equipment_staff_memberships__isnull=False)
    )
    return guide_filter() | orientor


def _short_clock(moment: time) -> str:
    """``11a``, ``2:30p``, ``12p``: a week chip's time, short enough for a seventh of a card."""
    hour = moment.hour % 12 or 12
    minutes = f":{moment.minute:02d}" if moment.minute else ""
    return f"{hour}{minutes}{'a' if moment.hour < 12 else 'p'}"


def _clock(moment: time) -> str:
    """``11:00 AM``: the half hour selects' own words."""
    from hub.forms import _meeting_time_label  # hub.forms imports models across the apps

    return _meeting_time_label(moment.hour, moment.minute)


def _midnight(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


@dataclass(frozen=True)
class Kind:
    """One kind of thing a person offers: Tours, or one guild's or tool's orientations."""

    key: str
    label: str
    css: str


@dataclass(frozen=True)
class Chip:
    """One block on the week: open hours, a booking, or busy time."""

    style: str
    starts_at: datetime
    ends_at: datetime
    what: str
    kind: Kind | None = None
    kinds: tuple[Kind, ...] = ()

    @property
    def time_label(self) -> str:
        """``11a to 6p``."""
        start, end = timezone.localtime(self.starts_at), timezone.localtime(self.ends_at)
        return f"{_short_clock(start.time())} to {_short_clock(end.time())}"


@dataclass
class WeekDay:
    """One day of the week: its chips in order, and whether it is a day off."""

    date: date
    is_today: bool
    is_day_off: bool = False
    chips: list[Chip] = field(default_factory=list)


@dataclass(frozen=True)
class Offer:
    """One kind a set of hours is open to, with that kind's own settings in words."""

    kind: Kind
    text: str


@dataclass(frozen=True)
class HoursSet:
    """One set of hours as a row: when, and what it is open to."""

    hours: Hours
    when: str
    offers: tuple[Offer, ...]


@dataclass
class MyHours:
    """Everything the My Hours page shows for one person. Build with :meth:`for_member`."""

    member: Member
    kinds: list[Kind]
    week_offset: int
    week_label: str
    week: list[WeekDay]
    grid: HourGridWeek
    weekly: list[HoursSet]
    dated: list[HoursSet]
    days_off: list[DayOff]
    gcal_configured: bool
    gcal: TourCalendarConnection | None
    gives_tours: bool
    notice_line: str

    @classmethod
    def for_member(cls, member: Member, *, week_offset: int = 0) -> MyHours:
        """Read ``member``'s page, the week ``week_offset`` weeks from this one."""
        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
        days = [monday + timedelta(days=n) for n in range(7)]
        sets = list(
            Hours.objects.filter(member=member).prefetch_related(
                Prefetch("tour_hours", queryset=TourHours.objects.order_by("pk")),
                Prefetch(
                    "orientation_rules",
                    queryset=OrientationAvailability.objects.filter(is_active=True)
                    .select_related("guild", "orientation_type__guild", "orientation_type__equipment")
                    .order_by("pk"),
                ),
            )
        )
        holds = busy_holds([member.pk], _midnight(days[0]), _midnight(days[-1] + timedelta(days=1)))
        slots = _slots(holds)
        gives_tours = (
            Member.objects.filter(pk=member.pk).filter(guide_filter()).exists()
            or any(hours.tour_hours.all() for hours in sets)
            or any(hold.source == TOUR for hold in holds)
        )
        kinds = _kinds(gives_tours, sets, slots.values())
        week = _week(days, today, sets, holds, slots, kinds)
        return cls(
            member=member,
            kinds=list(kinds.values()),
            week_offset=week_offset,
            week_label=_week_label(days[0], days[-1]),
            week=week,
            grid=week_grid(week),
            weekly=[_hours_set(hours, kinds) for hours in sets if hours.date is None],
            dated=sorted(
                (_hours_set(hours, kinds) for hours in sets if hours.date is not None and hours.date >= today),
                key=lambda row: (row.hours.date, row.hours.start_time),
            ),
            days_off=list(DayOff.objects.filter(member=member, ends_on__gte=today).order_by("starts_on")),
            gcal_configured=google.is_configured(),
            gcal=TourCalendarConnection.objects.filter(guide=member).first(),
            gives_tours=gives_tours,
            notice_line=_notice_line(member) if gives_tours else "",
        )


def _owner_key(orientation_type: OrientationType | None, guild_id: int | None) -> str:
    """The kind an orientation row or slot belongs to: its tool's, else its guild's."""
    if orientation_type is not None and orientation_type.equipment_id is not None:
        return f"equipment-{orientation_type.equipment_id}"
    if orientation_type is not None:
        return f"guild-{orientation_type.guild_id}"
    return f"guild-{guild_id}"


def _slot_owner(slot: OrientationSlot) -> str:
    """The guild or tool a slot's orientation belongs to, by name (a slot always has a type)."""
    return slot.orientation_type.owner_name


def _rule_key(rule: OrientationAvailability) -> str:
    return _owner_key(rule.orientation_type, rule.guild_id)


def _slot_key(slot: OrientationSlot) -> str:
    return _owner_key(slot.orientation_type, slot.guild_id)


def _kinds(gives_tours: bool, sets: list[Hours], slots: Iterable[OrientationSlot]) -> dict[str, Kind]:
    """Tours first when the person gives tours, then each guild or tool in the order their hours name them,
    then any other a booking this week belongs to; each takes the next colour in turn.
    """
    names: dict[str, str] = {}
    for hours in sets:
        for rule in hours.orientation_rules.all():
            names[_rule_key(rule)] = rule.owner_name
    for slot in slots:
        names[_slot_key(slot)] = _slot_owner(slot)
    kinds: dict[str, Kind] = {}
    if gives_tours:
        kinds[TOURS_KEY] = Kind(TOURS_KEY, "Tours", "pl-kind--tours")
    for index, (key, name) in enumerate(names.items()):
        kinds[key] = Kind(key, f"{name} orientations", KIND_CLASSES[index % len(KIND_CLASSES)])
    return kinds


def _slots(holds: list[Hold]) -> dict[int, OrientationSlot]:
    """The orientation slots among ``holds``, with their seat holding bookings and owner, by pk (one query plus one)."""
    pks = [hold.ref for hold in holds if hold.source == ORIENTATION]
    if not pks:
        return {}
    bookings = OrientationBooking.objects.filter(status__in=SEAT_HOLDING).select_related("member").order_by("pk")
    found = OrientationSlot.objects.filter(pk__in=pks).select_related(
        "guild", "orientation_type__guild", "orientation_type__equipment"
    )
    return {slot.pk: slot for slot in found.prefetch_related(Prefetch("bookings", queryset=bookings))}


def _slot_who(slot: OrientationSlot) -> str:
    """Who an orientation is with: their name, ``requested`` while it waits, or how many when several."""
    bookings = list(slot.bookings.all())
    if len(bookings) > 1:
        return f"{len(bookings)} booked"
    booking = bookings[0]
    if booking.status == OrientationBooking.Status.REQUESTED:
        return "requested"
    return booking.member.short_name


def _week(
    days: list[date],
    today: date,
    sets: list[Hours],
    holds: list[Hold],
    slots: dict[int, OrientationSlot],
    kinds: dict[str, Kind],
) -> list[WeekDay]:
    """Each day's chips: open hours unless it is a day off, then every booking and busy time on it."""
    days_off = [hold for hold in holds if hold.source == DAY_OFF]
    chips = _hold_chips(holds, slots, kinds)
    week: list[WeekDay] = []
    for day in days:
        first, after = _midnight(day), _midnight(day + timedelta(days=1))
        week_day = WeekDay(date=day, is_today=day == today)
        week_day.is_day_off = any(hold.starts_at < after and first < hold.ends_at for hold in days_off)
        if not week_day.is_day_off:
            week_day.chips += _open_chips(day, sets, kinds)
        for chip in chips:
            start, end = max(chip.starts_at, first), min(chip.ends_at, after)
            if start < end:
                week_day.chips.append(Chip(chip.style, start, end, chip.what, chip.kind))
        week_day.chips.sort(key=lambda chip: (chip.starts_at, _STYLE_ORDER[chip.style]))
        week.append(week_day)
    return week


def _span(chip: Chip, day: date) -> tuple[int, int]:
    """A chip's local start and end in minutes of ``day``; one running to midnight ends at midnight."""
    start, end = timezone.localtime(chip.starts_at), timezone.localtime(chip.ends_at)
    end_minute = end.hour * 60 + end.minute if end.date() == day else MINUTES_PER_DAY
    return start.hour * 60 + start.minute, end_minute


def week_grid(week: list[WeekDay], *, now: datetime | None = None) -> HourGridWeek:
    """The week as an hour grid (#1332): each day's chips as blocks, a booking inside the open hours holding it.

    Open hours, busy time and any booking no open hours hold sit side by side where they overlap
    (:func:`hub.hour_grid.lay_lanes`); bookings inside one set of open hours share its width the
    same way. A day off is striped with Day Off, and anything still on it (bookings already made)
    draws over the stripes.
    """
    moment = timezone.localtime(now)
    spans = {week_day.date: [_span(chip, week_day.date) for chip in week_day.chips] for week_day in week}
    first_hour, last_hour = hour_range([span for day_spans in spans.values() for span in day_spans])
    top = first_hour * 60
    now_minute = moment.hour * 60 + moment.minute - top
    days = [
        GridDay(
            day=week_day.date,
            is_today=week_day.date == moment.date(),
            blocks=_blocks(week_day.chips, spans[week_day.date], top),
            state="off" if week_day.is_day_off else "",
            note="Day Off" if week_day.is_day_off else "",
            now=now_minute
            if week_day.date == moment.date() and 0 <= now_minute <= (last_hour - first_hour) * 60
            else None,
        )
        for week_day in week
    ]
    return HourGridWeek(first_hour=first_hour, last_hour=last_hour, days=days, cell_template=GRID_DAY_TEMPLATE)


def _blocks(chips: list[Chip], spans: list[tuple[int, int]], top: int) -> list[Block]:
    """One day's blocks: open hours (each holding the bookings inside it), busy time, and bookings no open hours hold."""
    opens = [index for index, chip in enumerate(chips) if chip.style == OPEN]
    inside: dict[int, list[int]] = {index: [] for index in opens}
    tops: list[int] = []
    for index, chip in enumerate(chips):
        start, end = spans[index]
        holder = next((o for o in opens if chip.style == BOOKED and spans[o][0] <= start and end <= spans[o][1]), None)
        if holder is None:
            tops.append(index)
        else:
            inside[holder].append(index)
    blocks: list[Block] = []
    for index, (lane, lanes) in zip(tops, lay_lanes([spans[index] for index in tops])):
        chip, (start, end) = chips[index], spans[index]
        span_label = f"{clock(start)} to {clock(end)}"
        named = f"Open for {' and '.join(kind.label for kind in chip.kinds)}" if chip.style == OPEN else chip.what
        children = [chips[child] for child in inside.get(index, [])]
        child_spans = [spans[child] for child in inside.get(index, [])]
        blocks.append(
            Block(
                start=start - top,
                length=end - start,
                lane=lane,
                lanes=lanes,
                style=chip.style,
                when=clock(start) if chip.style == BOOKED else span_label,
                what=chip.what,
                title=f"{named}, {span_label}",
                css=_kind_css(chip),
                dots=tuple(kind.css for kind in chip.kinds),
                inner=[
                    Inner(
                        start=child_start - start,
                        length=child_end - child_start,
                        lane=child_lane,
                        lanes=child_lanes,
                        css=_kind_css(child),
                        when=clock(child_start),
                        what=child.what,
                        title=f"{child.what}, {clock(child_start)} to {clock(child_end)}",
                    )
                    for child, (child_start, child_end), (child_lane, child_lanes) in zip(
                        children, child_spans, lay_lanes(child_spans)
                    )
                ],
            )
        )
    return blocks


def _kind_css(chip: Chip) -> str:
    """The colour class of a booking's kind; open hours and busy time have none."""
    return chip.kind.css if chip.kind else ""


def _open_chips(day: date, sets: list[Hours], kinds: dict[str, Kind]) -> list[Chip]:
    """A dashed chip per set of hours offering anything on ``day``, dotted with the kinds it offers."""
    chips: list[Chip] = []
    for hours in sets:
        offered: dict[str, Kind] = {}
        if any(row.applies_on(day) for row in hours.tour_hours.all()):
            offered[TOURS_KEY] = kinds[TOURS_KEY]
        for rule in hours.orientation_rules.all():
            if rule.occurs_on(day):
                offered[_rule_key(rule)] = kinds[_rule_key(rule)]
        if offered:
            start = timezone.make_aware(datetime.combine(day, hours.start_time))
            end = timezone.make_aware(datetime.combine(day, hours.end_time))
            ordered = tuple(kind for kind in kinds.values() if kind.key in offered)
            chips.append(Chip(OPEN, start, end, "Open", kinds=ordered))
    return chips


def _hold_chips(holds: list[Hold], slots: dict[int, OrientationSlot], kinds: dict[str, Kind]) -> list[Chip]:
    """A chip per thing holding the time: a tour (its bookings together), an orientation, a class, Google busy."""
    refs: dict[str, list[int]] = defaultdict(list)
    for hold in holds:
        refs[hold.source].append(hold.ref)
    chips: list[Chip] = []
    tours: dict[tuple[datetime, datetime], int] = defaultdict(int)
    for starts_at, ends_at, party in TourBooking.objects.filter(pk__in=refs[TOUR]).values_list(
        "starts_at", "ends_at", "party_size"
    ):
        tours[(starts_at, ends_at)] += party
    for (starts_at, ends_at), party in tours.items():
        what = f"Tour · {party} visitor{'' if party == 1 else 's'}"
        chips.append(Chip(BOOKED, starts_at, ends_at, what, kinds[TOURS_KEY]))
    for pk in refs[ORIENTATION]:
        slot = slots[pk]
        what = f"{_slot_owner(slot)} · {_slot_who(slot)}"
        chips.append(Chip(BOOKED, slot.starts_at, slot.ends_at, what, kinds[_slot_key(slot)]))
    for session in ClassSession.objects.filter(pk__in=refs[CLASS]).select_related("class_offering"):
        chips.append(Chip(BUSY, session.starts_at, session.ends_at, f"Class: {session.class_offering.title}"))
    for hold in holds:
        if hold.source == GOOGLE:
            chips.append(Chip(BUSY, hold.starts_at, hold.ends_at, "Busy in Google"))
    return chips


def _week_label(first: date, last: date) -> str:
    """``Nov 24 to 30``, or ``Dec 29 to Jan 4`` across a month."""
    if first.month == last.month:
        return f"{first:%b} {first.day} to {last.day}"
    return f"{first:%b} {first.day} to {last:%b} {last.day}"


def _hours_set(hours: Hours, kinds: dict[str, Kind]) -> HoursSet:
    """One row of Every Week or Available On: ``Mon · 11:00 AM to 6:00 PM · Every week`` and its offers."""
    day = f"{hours.date:%a, %b} {hours.date.day}" if hours.date is not None else f"{hours.get_weekday_display()[:3]}"
    parts = [day, f"{_clock(hours.start_time)} to {_clock(hours.end_time)}"]
    if hours.repeat_label:
        parts.append(hours.repeat_label)
    offers: list[Offer] = [Offer(kinds[TOURS_KEY], f"Tours · {row.offer_summary}") for row in hours.tour_hours.all()]
    for rule in hours.orientation_rules.all():
        text = f"{rule.type_display} · {rule.style_display}"
        if not rule.is_open:
            text = f"{text} · {rule.seats} seat{'' if rule.seats == 1 else 's'}"
        offers.append(Offer(kinds[_rule_key(rule)], text))
    return HoursSet(hours=hours, when=" · ".join(parts), offers=tuple(offers))


def _notice_line(member: Member) -> str:
    """The Booking Notice card's sentence: how close to a tour visitors on the website can still book one."""
    house = FacilityTourSettings.load()
    minutes = TourGuideSettings.notice_minutes_by_guide([member.pk], house)[member.pk]
    if minutes == 0:
        return "Visitors on the website can book a tour right up to its start."
    return f"Visitors on the website can book a tour until {notice_label(minutes)} before it starts."
