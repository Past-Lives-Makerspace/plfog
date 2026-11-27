"""The My Hours editor's forms (#1300, part 4b): one set of hours per row, and Add Dates.

:class:`HoursSetForm` is one set of hours as it is edited: Day, From, Until and Repeats (or its one
date), then Open To, a block per kind (:class:`Block`) with that kind's own settings. A block is
Tours, each saved orientation row of the set (a paused one unticked, while the person still gives
that kind), and each guild or tool the person orients for that the set does not offer yet (``hours.edit.held_owners``, Felix 7), all ticked on a new set. The form
turns what was posted into an :class:`hours.edit.HoursEdit` and lays its problems back on the
fields, so a refusal lands where the autosave shows it (``<prefix>-<i>-<field>``).

:class:`HoursSetFormSet` is Every Week or Available On, the Tours page's formset idiom (#1279): a
row is found by its posted set id, never by its place, a row deleted elsewhere since the page
loaded is skipped and named in :attr:`HoursSetFormSet.gone_rows`, and a new row saves once Day,
From and Until are picked. :class:`AddDatesForm` adds the same hours on every date picked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.db.models import Prefetch
from django.utils import timezone

from hours.edit import TIME, TOURS, HoursEdit, OrientationOffer, Owner, TourOffer
from hours.models import Hours, TimeKey
from hub.forms import (
    _ORIENTATION_BUFFER_CHOICES,
    _SLOT_DURATION_CHOICES,
    _append_duration_choice,
    _parse_time_choice,
    _seed_time_choice,
    half_hour_time_choices,
)
from membership.models import OrientationAvailability, OrientationType
from tours.models import MAX_SEATS, FacilityTourSettings, TourHours, day_label

if TYPE_CHECKING:
    from django.http import QueryDict

    from membership.models import Member

#: How far ahead Add Dates may pick, as the Tours page's Available On calendar allows.
MAX_DATES_AHEAD = 365
_TEXT = "pl-tours-input--text"
_NARROW = "pl-tours-input--narrow"
_CADENCE = OrientationAvailability.Cadence
_FIXED = OrientationAvailability.BookingStyle.FIXED
_OPEN = OrientationAvailability.BookingStyle.OPEN
#: The set's own time fields: a problem with the time lands on one of them, or on the row.
_TIME_FIELDS = ("weekday", "date", "cadence", "anchor_date", "last_of_month", "start_time", "end_time")


def _date_widget() -> forms.DateInput:
    # Rule 14: the whole field opens the picker.
    return forms.DateInput(attrs={"type": "date", "onclick": "try { this.showPicker() } catch (e) {}"})


@dataclass(frozen=True)
class Block:
    """One kind in Open To: Tours, a saved orientation row, or a guild or tool not offered here yet."""

    key: str
    label: str
    hint: str
    owner: Owner | None = None
    rule: OrientationAvailability | None = None

    @property
    def is_tours(self) -> bool:
        return self.key == TOURS

    @property
    def offers_open(self) -> bool:
        """Any time in the window is a guild's, on a person's own hours (the Edit Hours rule, #532)."""
        return self.owner is not None and self.owner.equipment is None


@dataclass(frozen=True)
class Context:
    """What every row of one person's editor shares: who, what they may offer, and the tour defaults."""

    member: Member
    gives_tours: bool
    owners: list[Owner]
    defaults: FacilityTourSettings
    #: Each owner's active orientations, read once for every row (``types_of``).
    _types: dict[str, list[OrientationType]] = field(default_factory=dict, compare=False)

    def types_of(self, owner: Owner) -> list[OrientationType]:
        """The orientations ``owner`` offers now, in the editors' order, read once per editor."""
        if owner.key not in self._types:
            self._types[owner.key] = list(owner.types())
        return self._types[owner.key]


class HoursSetForm(forms.Form):
    """One set of hours: its time, then a block per kind it is or may be open to."""

    id = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args: Any, context: Context, hours: Hours | None, dated: bool, **kwargs: Any) -> None:
        kwargs.pop("initial", None)  # a row's initial is its set as stored, never its place in the formset
        super().__init__(*args, **kwargs)
        self.context, self.hours, self.dated = context, hours, dated
        self.edit: HoursEdit | None = None
        self.type_choices: dict[str, dict[int, OrientationType]] = {}
        self._time_fields()
        self.blocks = self._blocks()
        for block in self.blocks:
            self._block_fields(block)
        if hours is not None:
            self.initial["id"] = hours.pk

    # --- Building -------------------------------------------------------------------------------

    def _time_fields(self) -> None:
        saved = self.hours is not None
        times = half_hour_time_choices(required=saved)
        self.fields["start_time"] = forms.ChoiceField(choices=times, label="From")
        self.fields["end_time"] = forms.ChoiceField(choices=list(times), label="Until")
        if self.dated:
            self.fields["date"] = forms.DateField(label="Date", widget=_date_widget())
        else:
            weekdays = list(OrientationAvailability.Weekday.choices)
            self.fields["weekday"] = forms.TypedChoiceField(
                coerce=int, choices=weekdays if saved else [("", "---------"), *weekdays], label="Day"
            )
            self.fields["cadence"] = forms.ChoiceField(choices=_CADENCE.choices, label="Repeats")
            self.fields["anchor_date"] = forms.DateField(required=False, label="Starting on", widget=_date_widget())
            self.fields["last_of_month"] = forms.BooleanField(required=False, label="Last of the month")
        self.initial.update({"cadence": _CADENCE.WEEKLY, "last_of_month": False})
        if self.hours is not None:
            hours = self.hours
            self.initial.update(
                {name: getattr(hours, name) for name in ("date", "weekday", "cadence", "anchor_date", "last_of_month")}
            )
            _seed_time_choice(self, "start_time", hours.start_time)
            _seed_time_choice(self, "end_time", hours.end_time)

    def _blocks(self) -> list[Block]:
        context = self.context
        blocks: list[Block] = []
        tour = _tour_row(self.hours)
        if context.gives_tours or tour is not None:
            blocks.append(Block(TOURS, "Tours", "Visitors book on tours.pastlives.space."))
        held = {owner.key for owner in context.owners}
        rules = [
            rule
            for rule in (self.hours.orientation_rules.all() if self.hours is not None else [])
            # A paused row shows, unticked, while the person still gives that kind (the old Active switch).
            if rule.is_active or Owner.of(rule).key in held
        ]
        offered: set[str] = set()
        for rule in rules:
            owner = Owner.of(rule)
            offered.add(owner.key)
            hint = _orientation_hint(owner) if rule.is_active else PAUSED_HINT
            blocks.append(Block(f"rule-{rule.pk}", owner.label, hint, owner, rule))
        for owner in context.owners:
            if owner.key not in offered:
                blocks.append(Block(owner.key, owner.label, _orientation_hint(owner), owner))
        return blocks

    def _block_fields(self, block: Block) -> None:
        name = f"{block.key}-"
        self.fields[f"{name}on"] = forms.BooleanField(
            required=False, label=block.label, widget=forms.CheckboxInput(attrs={"x-model": "on"})
        )
        if block.is_tours:
            self._tour_fields(name)
            return
        owner = cast(Owner, block.owner)
        rule = block.rule
        types = list(self.context.types_of(owner))
        if rule is not None and rule.orientation_type is not None and rule.orientation_type not in types:
            types.append(rule.orientation_type)  # a retired orientation a saved row still names
        first = types[0] if types else None
        self.type_choices[block.key] = {kind.pk: kind for kind in types}
        choices = [(str(kind.pk), kind.name) for kind in types]
        self.fields[f"{name}orientation_type"] = forms.TypedChoiceField(
            coerce=int,
            empty_value=None,
            required=False,
            label="Orientation",
            choices=[("", "Any orientation"), *choices] if block.offers_open else choices,
        )
        if block.offers_open:
            self.fields[f"{name}booking_style"] = forms.ChoiceField(
                choices=OrientationAvailability.BookingStyle.choices,
                widget=forms.RadioSelect(attrs={"x-model": "style"}),
                required=False,
                label="How members book",
            )
        self.fields[f"{name}seats"] = forms.IntegerField(
            min_value=1, required=False, label="Seats", widget=forms.NumberInput(attrs={"min": "1", "class": _NARROW})
        )
        self.fields[f"{name}slot_minutes"] = forms.TypedChoiceField(
            coerce=int,
            empty_value=None,
            required=False,
            choices=[("", "Whole window"), *_SLOT_DURATION_CHOICES],
            label="Slot length",
        )
        self.fields[f"{name}buffer_minutes"] = forms.TypedChoiceField(
            coerce=int, required=False, choices=_ORIENTATION_BUFFER_CHOICES, label="Break"
        )
        if rule is not None:
            if rule.slot_minutes is not None:
                _append_duration_choice(self.fields[f"{name}slot_minutes"], rule.slot_minutes)
            values: dict[str, Any] = {
                "on": rule.is_active,
                "orientation_type": rule.orientation_type_id,
                "booking_style": rule.booking_style,
                "seats": rule.seats,
                "slot_minutes": rule.slot_minutes,
                "buffer_minutes": rule.buffer_minutes,
            }
        else:
            values = {
                "on": self.hours is None,
                "orientation_type": first.pk if first is not None else None,
                "booking_style": _FIXED,
                "seats": first.default_seats if first is not None else 4,
                "slot_minutes": None,
                "buffer_minutes": 0,
            }
        self.initial.update({f"{name}{field}": value for field, value in values.items()})

    def _tour_fields(self, name: str) -> None:
        defaults = self.context.defaults
        tour = _tour_row(self.hours)
        self.fields[f"{name}tour_minutes"] = forms.TypedChoiceField(
            coerce=int, required=False, choices=list(_SLOT_DURATION_CHOICES), label="Tour length"
        )
        self.fields[f"{name}seats"] = forms.IntegerField(
            min_value=1,
            max_value=MAX_SEATS,
            required=False,
            label="Seats",
            widget=forms.NumberInput(attrs={"min": "1", "max": str(MAX_SEATS), "class": _NARROW}),
            error_messages={"min_value": "Use 1 to 50 seats.", "max_value": "Use 1 to 50 seats."},
        )
        minutes = tour.tour_minutes if tour is not None else defaults.default_tour_minutes
        _append_duration_choice(self.fields[f"{name}tour_minutes"], minutes)
        self.initial.update(
            {
                f"{name}on": tour is not None or self.hours is None,
                f"{name}tour_minutes": minutes,
                f"{name}seats": tour.seats if tour is not None else defaults.default_seats,
            }
        )

    # --- Reading --------------------------------------------------------------------------------

    @property
    def is_absent(self) -> bool:
        """A bound row the POST carries nothing for (a row deleted elsewhere, or a clone removed): it stays unchanged."""
        return self.is_bound and not any(key.startswith(f"{self.prefix}-") for key in self.data)

    def has_changed(self) -> bool:
        return not self.is_absent and super().has_changed()

    @property
    def block_rows(self) -> list[tuple[Block, dict[str, forms.BoundField]]]:
        """Each block with its own fields by short name (``on``, ``seats``, ...), for the template."""
        return [
            (
                block,
                {name[len(block.key) + 1 :]: self[name] for name in self.fields if name.startswith(f"{block.key}-")},
            )
            for block in self.blocks
        ]

    def clean_start_time(self) -> time:
        return _parse_time_choice(self.cleaned_data["start_time"])

    def clean_end_time(self) -> time:
        return _parse_time_choice(self.cleaned_data["end_time"])

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        if self.errors or cleaned.get("DELETE") or not self.has_changed():
            return cleaned
        self.edit = HoursEdit(
            self.context.member, self.hours, self.key(cleaned), self._tours(cleaned), self._offers(cleaned)
        )
        self.lay_problems(self.edit.problems())
        return cleaned

    def key(self, cleaned: dict[str, Any], day: date | None = None) -> TimeKey:
        """The set's time as posted (``day`` in place of the posted date, for Add Dates)."""
        day = day or cleaned.get("date")
        cadence = cleaned.get("cadence") or _CADENCE.WEEKLY
        return TimeKey(
            date=day,
            weekday=day.weekday() if day is not None else cleaned["weekday"],
            cadence=_CADENCE.WEEKLY if day is not None else cadence,
            anchor_date=None if day is not None else cleaned.get("anchor_date"),
            last_of_month=day is None
            and cadence in OrientationAvailability.MONTHS_BY_CADENCE
            and bool(cleaned.get("last_of_month")),
            start_time=cleaned["start_time"],
            end_time=cleaned["end_time"],
        )

    def _tours(self, cleaned: dict[str, Any]) -> TourOffer | None:
        if not cleaned.get(f"{TOURS}-on"):
            return None
        defaults = self.context.defaults
        return TourOffer(
            tour_minutes=cleaned.get(f"{TOURS}-tour_minutes") or defaults.default_tour_minutes,
            seats=cleaned.get(f"{TOURS}-seats") or defaults.default_seats,
        )

    def _offers(self, cleaned: dict[str, Any]) -> list[OrientationOffer]:
        offers: list[OrientationOffer] = []
        for block in self.blocks:
            if block.is_tours:
                continue
            name = f"{block.key}-"
            on = bool(cleaned.get(f"{name}on"))
            if not on and block.rule is None:
                continue
            offers.append(
                OrientationOffer(
                    owner=cast(Owner, block.owner),
                    on=on,
                    rule=block.rule,
                    orientation_type=self.type_choices[block.key].get(cleaned.get(f"{name}orientation_type") or 0),
                    booking_style=cleaned.get(f"{name}booking_style") or _FIXED,
                    seats=cleaned.get(f"{name}seats") or 4,
                    slot_minutes=cleaned.get(f"{name}slot_minutes"),
                    buffer_minutes=cleaned.get(f"{name}buffer_minutes") or 0,
                )
            )
        return offers

    def lay_problems(self, problems: dict[str, dict[str, list[str]]], *, lead: str = "") -> None:
        """Put each of :meth:`HoursEdit.problems` on its field: the time's on the time fields, a kind's in its block."""
        for where, fields in problems.items():
            for name, messages in fields.items():
                if where == TIME:
                    target = name if name in self.fields and name in _TIME_FIELDS else None
                else:
                    target = f"{where}-{name}" if f"{where}-{name}" in self.fields else f"{where}-on"
                for message in messages:
                    self.add_error(target, f"{lead}{message}")

    def save(self) -> Hours | None:
        """Write the row: remove a deleted set, save a changed one; None when there is nothing left to name."""
        if self.cleaned_data.get("DELETE"):
            if self.hours is not None:
                self.hours.remove()
            return None
        self.hours = cast(HoursEdit, self.edit).save()
        return self.hours


def _tour_row(hours: Hours | None) -> TourHours | None:
    """The set's tour row, read from its prefetched rows (``with_kind_rows``)."""
    return next(iter(hours.tour_hours.all()), None) if hours is not None else None


def with_kind_rows(sets: Any) -> Any:
    """``sets`` with each set's tour rows and orientation rows (owner and type) in two more queries."""
    return sets.prefetch_related(
        Prefetch("tour_hours", queryset=TourHours.objects.order_by("pk")),
        Prefetch(
            "orientation_rules",
            queryset=OrientationAvailability.objects.select_related(
                "guild", "orientation_type__guild", "orientation_type__equipment"
            ).order_by("pk"),
        ),
    )


#: The hint on a paused orientation row's block: ticking it offers the row again with its settings.
PAUSED_HINT = "Paused. Tick it to offer it again with these settings."


def _orientation_hint(owner: Owner) -> str:
    """The block's one line under its name, as the mockup words it."""
    if owner.equipment is not None:
        return "Members book you by name on the tool's page."
    return "Members book you by name on the guild page."


class HoursSetFormSet(forms.BaseFormSet):
    """Every Week (``dated=False``) or Available On (``dated=True``): one person's sets, saved as edited."""

    # What formset_factory would set: rows only for real sets, a + Add Hours button for new ones.
    form = HoursSetForm
    extra = 0
    min_num = 0
    max_num = 1000
    absolute_max = 2000
    can_order = False
    can_delete = True
    can_delete_extra = True
    validate_min = False
    validate_max = False
    renderer = None

    def __init__(self, data: Any = None, *, context: Context, dated: bool, prefix: str) -> None:
        self.context, self.dated = context, dated
        today = timezone.localdate()
        found = list(with_kind_rows(Hours.objects.filter(member=context.member, date__isnull=not dated)))
        self._by_pk = {hours.pk: hours for hours in found}
        if dated:
            found = sorted(
                (hours for hours in found if cast(date, hours.date) >= today), key=lambda h: (h.date, h.start_time)
            )
        self.sets = found
        super().__init__(data, prefix=prefix, initial=[{"id": hours.pk} for hours in self.sets])

    def _posted_hours(self, index: int) -> tuple[Hours | None, bool]:
        """The set a bound row names, and whether it named one that is gone (deleted elsewhere, or not theirs)."""
        raw = str(self.data.get(f"{self.add_prefix(index)}-id", ""))
        if not raw.isdigit():
            return None, False
        hours = self._by_pk.get(int(raw))
        return hours, hours is None

    def get_form_kwargs(self, index: int | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"context": self.context, "dated": self.dated, "hours": None}
        if index is None:
            return kwargs
        if not self.is_bound:
            kwargs["hours"] = self.sets[index] if index < len(self.sets) else None
            return kwargs
        hours, gone = self._posted_hours(index)
        kwargs["hours"] = hours
        if gone:
            prefix = f"{self.add_prefix(index)}-"
            kwargs["data"] = {key: value for key, value in self.data.items() if not key.startswith(prefix)}
            kwargs["empty_permitted"] = True
        return kwargs

    @property
    def gone_rows(self) -> list[int]:
        """The posted indices of rows whose set is gone since the page loaded."""
        return [index for index in range(self.initial_form_count()) if self._posted_hours(index)[1]]

    def rows(self) -> list[int | None]:
        """Each row's set id in posted order, None for a deleted row or a new one that did not save."""
        found: list[int | None] = []
        for form in self.forms:
            hours = cast(HoursSetForm, form).hours
            deleted = bool(getattr(form, "cleaned_data", {}).get("DELETE"))
            found.append(None if deleted or hours is None else hours.pk)
        return found

    def save(self) -> None:
        """Write every row that changed (after ``is_valid()``, under the person's lock)."""
        for form in self.forms:
            if form.has_changed():
                cast(HoursSetForm, form).save()


def hours_formset(context: Context, *, dated: bool, data: QueryDict | None = None) -> HoursSetFormSet:
    """Every Week's or Available On's formset for one person."""
    return HoursSetFormSet(data, context=context, dated=dated, prefix="dated" if dated else "weekly")


class AddDatesForm(HoursSetForm):
    """+ Add Dates: one set of hours on every date picked in the calendar, all or nothing.

    ``dates`` arrives as one ``YYYY-MM-DD`` per pick, as on the Tours page's Available On. Each
    date is its own :class:`HoursEdit`; a problem on any date is named with it and nothing is saved.
    """

    def __init__(self, *args: Any, context: Context, **kwargs: Any) -> None:
        super().__init__(*args, context=context, hours=None, dated=True, prefix="add_dates", **kwargs)
        del self.fields["date"]
        self.edits: list[HoursEdit] = []
        self.picked = self._read_dates()

    def _read_dates(self) -> list[date]:
        if not self.is_bound:
            return []
        days: set[date] = set()
        for raw in cast("QueryDict", self.data).getlist("dates"):
            try:
                days.add(date.fromisoformat(raw))
            except ValueError:
                continue
        return sorted(days)

    @property
    def picked_iso(self) -> list[str]:
        return [day.isoformat() for day in self.picked]

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], forms.Form.clean(self))
        if not self.picked:
            self.add_error(None, "Pick at least one date.")
        if self.errors:
            return cleaned
        today = timezone.localdate()
        tours, offers = self._tours(cleaned), self._offers(cleaned)
        for day in self.picked:
            label = f"{day_label(day)}: "
            if day < today:
                self.add_error(None, f"{label}it has passed. Pick today or a day ahead.")
                continue
            if day > today + timedelta(days=MAX_DATES_AHEAD):
                self.add_error(None, f"{label}it is more than a year ahead.")
                continue
            edit = HoursEdit(self.context.member, None, self.key(cleaned, day), tours, offers)
            self.lay_problems(edit.problems(), lead=label)
            self.edits.append(edit)
        return cleaned

    def save(self) -> list[Hours]:  # type: ignore[override]
        """Write every picked date's hours (after ``is_valid()``, under the person's lock)."""
        return [edit.save() for edit in self.edits]
