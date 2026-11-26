"""Days Off on My Hours (#1300, part 4c): one list for every kind, and what is still booked on them.

A day off hides the person's unbooked times for every kind and cancels nothing (Felix,
2026-11-23): :func:`still_booked` lists the tours and orientations already booked in it, for the
person to move or cancel. While the old editors still write the old rows, a day off saved here is a
``tours.TourDayOff`` (a range, which ``hours.models.DayOffManager.recopy`` copies as it is), and
:func:`remove_day_off` deletes the old rows behind one ``DayOff``: its tour range and any orientation
day it covers, so nothing copies it back.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.db.models import Prefetch
from django.utils import timezone

from hours.busy import ORIENTATION, TOUR, busy_holds
from hours.models import DayOff
from membership.models import OrientationBooking, OrientationDayOff, OrientationSlot
from tours.models import TourBooking, TourDayOff

if TYPE_CHECKING:
    from collections.abc import Iterable

    from membership.models import Member

#: The longest run of days one day off may cover, as on the Tours page.
MAX_DAY_OFF_DAYS = 366


def _clock(moment: datetime) -> str:
    local = timezone.localtime(moment)
    return f"{local:%-I:%M %p}"


def days_off_by_member(members: Iterable[Member]) -> dict[str, list[DayOff]]:
    """Each listed person's days off not over yet, soonest first, for an Orientation Schedule's "Off ..." line.

    Keyed by the pk as a string, which is how the template's ``get_item`` filter looks a dict up.
    One list for every kind (#1300), so a day off added on My Hours shows on every card.
    """
    grouped: dict[str, list[DayOff]] = {}
    found = DayOff.objects.filter(member__in=list(members), ends_on__gte=timezone.localdate())
    for day_off in found.order_by("starts_on", "pk"):
        grouped.setdefault(str(day_off.member_id), []).append(day_off)
    return grouped


def still_booked(member: Member, first: date, last: date) -> list[str]:
    """Every tour and orientation booked with ``member`` from ``first`` to ``last``, soonest first, in words.

    ``Tour at 1:00 PM, 3 visitors``, ``Complete Beginner to Printmaking at 3:00 PM with Sam K.``;
    over more than one day each starts with its day (``Tue, Dec 23: ...``). The rows are
    :func:`hours.busy.busy_holds`, the one rule for what holds a person's time.
    """
    start = timezone.make_aware(datetime.combine(first, time.min))
    before = timezone.make_aware(datetime.combine(last + timedelta(days=1), time.min))
    holds = busy_holds([member.pk], start, before, google_busy=False)
    refs: dict[str, list[int]] = defaultdict(list)
    for hold in holds:
        refs[hold.source].append(hold.ref)
    lines: list[tuple[datetime, str]] = []
    tours: dict[datetime, int] = defaultdict(int)
    for starts_at, party in TourBooking.objects.filter(pk__in=refs[TOUR]).values_list("starts_at", "party_size"):
        tours[starts_at] += party
    for starts_at, party in tours.items():
        lines.append((starts_at, f"Tour at {_clock(starts_at)}, {party} visitor{'' if party == 1 else 's'}"))
    status = OrientationBooking.Status
    held = (
        OrientationBooking.objects.filter(status__in=(status.REQUESTED, status.CONFIRMED))
        .select_related("member")
        .order_by("pk")
    )
    slots = (
        OrientationSlot.objects.filter(pk__in=refs[ORIENTATION])
        .select_related("orientation_type")
        .prefetch_related(Prefetch("bookings", queryset=held, to_attr="held_bookings"))
    )
    for slot in slots:
        lines.append((slot.starts_at, f"{slot.orientation_type.name} at {_clock(slot.starts_at)} {_who(slot)}"))
    lines.sort(key=lambda line: line[0])
    if first == last:
        return [text for _, text in lines]
    return [f"{timezone.localtime(moment):%a, %b} {timezone.localtime(moment).day}: {text}" for moment, text in lines]


def _who(slot: OrientationSlot) -> str:
    """Who a slot is booked with, from its prefetched ``held_bookings`` (requested or confirmed)."""
    bookings: list[OrientationBooking] = slot.held_bookings  # type: ignore[attr-defined]
    if not bookings:
        return "(a checkout in progress)"
    if len(bookings) > 1:
        return f"with {len(bookings)} people"
    if bookings[0].status == OrientationBooking.Status.REQUESTED:
        return "(requested)"
    return f"with {bookings[0].member.short_name}"


class DayOffForm(forms.Form):
    """Add Day Off: one day, or a run of days, and a note. The person is the view's, never posted."""

    starts_on = forms.DateField(
        label="First day",
        widget=forms.DateInput(attrs={"type": "date", "onclick": "try { this.showPicker() } catch (e) {}"}),
    )
    ends_on = forms.DateField(
        label="Last day",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "onclick": "try { this.showPicker() } catch (e) {}"}),
    )
    note = forms.CharField(
        label="Note (optional)",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Only you and admins see it", "class": "pl-tours-input--text"}),
    )

    def __init__(self, *args: Any, member: Member, **kwargs: Any) -> None:
        kwargs.setdefault("prefix", "day_off")
        super().__init__(*args, **kwargs)
        self.member = member

    def chosen(self) -> tuple[date, date] | None:
        """The first and last day as typed so far (for the still booked line), or None before a first day."""
        if not self.is_bound:
            return None
        try:
            first = date.fromisoformat(str(self.data.get(self.add_prefix("starts_on"), "")))
        except ValueError:
            return None
        try:
            last = date.fromisoformat(str(self.data.get(self.add_prefix("ends_on"), "")))
        except ValueError:
            last = first
        return first, max(first, last)

    def clean_starts_on(self) -> date:
        day = cast(date, self.cleaned_data["starts_on"])
        if day < timezone.localdate():
            raise forms.ValidationError("Pick today or a day ahead.")
        return day

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        first: date | None = cleaned.get("starts_on")
        if first is None:
            return cleaned
        last: date = cleaned.get("ends_on") or first
        cleaned["ends_on"] = last
        if last < first:
            self.add_error("ends_on", "The last day must be on or after the first.")
        elif last - first >= timedelta(days=MAX_DAY_OFF_DAYS):
            self.add_error("ends_on", "Pick a run of a year or less.")
        else:
            clash = DayOff.objects.filter(member=self.member, starts_on__lte=last, ends_on__gte=first).first()
            if clash is not None:
                self.add_error("starts_on", f"Part of that is already off: {clash.label}.")
        return cleaned

    def save(self) -> DayOff:
        """Save the day off (through the tours range row while the old editors still write theirs) and return it."""
        cleaned = self.cleaned_data
        TourDayOff.objects.create(
            guide=self.member, starts_on=cleaned["starts_on"], ends_on=cleaned["ends_on"], note=cleaned["note"]
        )
        return DayOff.objects.get(member=self.member, starts_on=cleaned["starts_on"], ends_on=cleaned["ends_on"])


def remove_day_off(day_off: DayOff) -> None:
    """Delete one day off for every kind: its tour range and every orientation day it covers, then its copy follows."""
    member_id = day_off.member_id
    for row in TourDayOff.objects.filter(guide_id=member_id, starts_on=day_off.starts_on, ends_on=day_off.ends_on):
        row.delete()
    for day in OrientationDayOff.objects.filter(
        orienter_id=member_id, date__range=(day_off.starts_on, day_off.ends_on)
    ):
        day.delete()
    DayOff.objects.recopy(member_id)
