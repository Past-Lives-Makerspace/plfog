"""Forms for the Tours page's My Hours and Settings tabs (#947), and the visitor's booking form (#948).

The hours rows reuse the orientation editor's pieces: the shared half hour time choices and the
shared slot length list (FRONTEND rule 20), and the formset shape of ``OrientationAvailabilityFormSet``
(``extra=0`` plus an "+ Add hours" button). Named apart from ``hub.forms.TourSettingsForm``, which is
the guided tours' toggle.
"""

from __future__ import annotations

from datetime import date, time, timedelta
from typing import Any, cast

from django import forms
from django.db import transaction
from django.http import QueryDict
from django.utils import timezone

from hub.forms import (
    _SLOT_DURATION_CHOICES,
    _append_duration_choice,
    _meeting_time_label,
    _seed_time_choice,
    half_hour_time_choices,
)
from membership.models import Member
from tours.models import (
    MAX_SEATS,
    FacilityTourSettings,
    TourCalendarConnection,
    TourDayOff,
    TourGuideSettings,
    TourHours,
    day_label,
)

#: The longest run of days one day off may cover.
MAX_DAY_OFF_DAYS = 366
#: How far ahead dated hours may be added (#1160), in days: the calendar disables the days past it.
MAX_DATED_DAYS_AHEAD = 365
#: The shared tour lengths, shown in minutes, as the Available On field's label and its list ("60 minute tours") read.
_LENGTH_CHOICES: list[tuple[str, str]] = [(value, value) for value, _label in _SLOT_DURATION_CHOICES]

_NARROW = "pl-tours-input--narrow"
_TEXT = "pl-tours-input--text"


def _parse_half_hour(value: str) -> time:
    """An ``"HH:MM"`` choice as a ``datetime.time``."""
    return time.fromisoformat(value)


class TourHoursForm(forms.ModelForm):
    """One row of a guide's weekly tour hours: day, from, until, tour length and seats.

    Times are the shared half hour ``<select>`` (never a per minute input); a stored time off the
    grid keeps its own option until changed (``_seed_time_choice``), and so does a tour length off
    the shared list. A new row starts with Tour Settings' default length and seats.
    """

    start_time = forms.ChoiceField(choices=half_hour_time_choices(required=True), label="From")
    end_time = forms.ChoiceField(choices=half_hour_time_choices(required=True), label="Until")
    tour_minutes = forms.TypedChoiceField(coerce=int, choices=_SLOT_DURATION_CHOICES, label="Tour length")

    class Meta:
        model = TourHours
        fields = ["weekday", "start_time", "end_time", "tour_minutes", "seats"]
        labels = {"weekday": "Day", "seats": "Seats"}
        widgets = {"seats": forms.NumberInput(attrs={"min": "1", "max": str(MAX_SEATS), "class": _NARROW})}

    def __init__(self, *args: Any, defaults: FacilityTourSettings, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name in ("weekday", "start_time", "end_time", "tour_minutes", "seats"):
            self.fields[name].help_text = ""
        if self.instance.pk:
            _seed_time_choice(self, "start_time", self.instance.start_time)
            _seed_time_choice(self, "end_time", self.instance.end_time)
            _append_duration_choice(self.fields["tour_minutes"], self.instance.tour_minutes)
        else:
            _append_duration_choice(self.fields["tour_minutes"], defaults.default_tour_minutes)
            self.initial.setdefault("tour_minutes", defaults.default_tour_minutes)
            self.initial.setdefault("seats", defaults.default_seats)

    @property
    def is_absent(self) -> bool:
        """Whether a bound POST carried none of this row's fields (a clone removed before Save)."""
        return self.is_bound and not any(key.startswith(f"{self.prefix}-") for key in self.data)

    def has_changed(self) -> bool:
        """A row the POST carries none of (a "+ Add hours" clone removed before Save) is unchanged.

        The editors remove an unsaved row from the page and leave ``TOTAL_FORMS`` as it was, as
        the orientation and equipment hours editors do. Their forms have no initial values, so
        the missing row reads as unchanged and is skipped; this one starts from Tour Settings'
        defaults, which a missing row would otherwise "change", failing the save on a row the
        guide never sees.
        """
        if self.is_absent:
            return False
        return super().has_changed()

    @property
    def summary(self) -> str:
        """The collapsed row's line for a saved row: ``Tuesday · 4:00 PM to 6:00 PM · 1 hour each · 4 seats``.

        The browser rebuilds the same words from the row's controls as they change
        (``plTourHoursRow`` in ``tours/hours.html``), so this only has to be right for what is stored.
        """
        row = self.instance
        lengths = dict(cast("list[tuple[str, str]]", cast(forms.ChoiceField, self.fields["tour_minutes"]).choices))
        seats = f"{row.seats} seat{'' if row.seats == 1 else 's'}"
        return (
            f"{row.get_weekday_display()} · {_meeting_time_label(row.start_time.hour, row.start_time.minute)} to "
            f"{_meeting_time_label(row.end_time.hour, row.end_time.minute)} · {lengths[str(row.tour_minutes)]} each · {seats}"
        )

    def clean_start_time(self) -> time:
        return _parse_half_hour(self.cleaned_data["start_time"])

    def clean_end_time(self) -> time:
        return _parse_half_hour(self.cleaned_data["end_time"])


class _TourHoursBaseFormSet(forms.BaseInlineFormSet):
    """One guide's hours, saved together: no two rows may overlap, since a guide gives one tour at a time.

    Submitted rows are compared with each other as submitted, so moving one row out of another's
    way in the same save is fine, and with every saved row of the guide's the POST did not carry
    (a stale tab that never showed it, posting ``INITIAL_FORMS=0``). A row the POST carries, kept
    or deleted, is judged by what was posted. This keeps one guide and one start time to one row,
    which is what booking locks (``tours.availability.open_tour_at``).
    """

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        rows = [form.instance for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE")]
        for index, row in enumerate(rows):
            for other in rows[index + 1 :]:
                if row.overlaps(other):
                    raise forms.ValidationError(
                        f"Two of these rows overlap on {row.get_weekday_display()}. A guide gives one tour at a time."
                    )
        self._refuse_clash_with_dated(rows)
        carried = {form.instance.pk for form in self.forms if form.instance.pk is not None}
        for saved in self.get_queryset().exclude(pk__in=carried):
            for row in rows:
                if row.overlaps(saved):
                    raise forms.ValidationError(
                        f"These hours overlap saved {saved.get_weekday_display()} hours of "
                        f"{_meeting_time_label(saved.start_time.hour, saved.start_time.minute)} to "
                        f"{_meeting_time_label(saved.end_time.hour, saved.end_time.minute)} that this page "
                        "does not show. Reload the page and try again."
                    )

    def _refuse_clash_with_dated(self, rows: list[TourHours]) -> None:
        """Refuse a weekly row that overlaps upcoming dated hours (#1160) on its weekday, naming the date."""
        if not rows:
            return
        dated = TourHours.objects.upcoming_dated().filter(
            guide=self.instance, weekday__in={row.weekday for row in rows}
        )
        for other in dated:
            for row in rows:
                if row.overlaps(other):
                    raise forms.ValidationError(
                        f"Every {row.get_weekday_display()} {row.times_label} overlaps your hours on "
                        f"{day_label(cast(date, other.date))}, {other.times_label}."
                    )


TourHoursFormSet = forms.inlineformset_factory(
    Member,
    TourHours,
    fk_name="guide",
    form=TourHoursForm,
    formset=_TourHoursBaseFormSet,
    extra=0,
    can_delete=True,
)


class TourDatesForm(forms.Form):
    """Hours on picked dates (#1160): the Available On panel's calendar picks plus one set of hours.

    ``dates`` arrives as one ``YYYY-MM-DD`` value per picked day (the calendar writes a hidden
    input each). Every date is checked before anything is saved, and :meth:`save` writes them all
    in one transaction, so a refused save keeps nothing. A date is refused when it has passed, is
    more than a year ahead, falls on a day off, or overlaps the guide's weekly hours that weekday
    or hours already on that date; each problem is its own message, naming the date.
    """

    start_time = forms.ChoiceField(choices=half_hour_time_choices(required=True), label="From")
    end_time = forms.ChoiceField(choices=half_hour_time_choices(required=True), label="To")
    tour_minutes = forms.TypedChoiceField(coerce=int, choices=_LENGTH_CHOICES, label="Tour length (minutes)")
    seats = forms.IntegerField(
        min_value=1,
        max_value=MAX_SEATS,
        label="Seats",
        widget=forms.NumberInput(attrs={"min": "1", "max": str(MAX_SEATS), "class": _NARROW}),
        error_messages={"min_value": "Use 1 to 50 seats.", "max_value": "Use 1 to 50 seats."},
    )

    def __init__(self, *args: Any, guide: Member, defaults: FacilityTourSettings, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.guide = guide
        #: The rows :meth:`save` writes, one per picked date, set once :meth:`clean` passes.
        self.rows: list[TourHours] = []
        default = str(defaults.default_tour_minutes)
        if default not in dict(_LENGTH_CHOICES):
            cast(forms.TypedChoiceField, self.fields["tour_minutes"]).choices = [*_LENGTH_CHOICES, (default, default)]
        self.initial.setdefault("tour_minutes", defaults.default_tour_minutes)
        self.initial.setdefault("seats", defaults.default_seats)
        self.picked = self._read_dates()

    def _read_dates(self) -> list[date]:
        """The posted dates, each once and in order; a value that is not a date is dropped (the calendar never sends one)."""
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
        """The picked dates as ``YYYY-MM-DD``, for the calendar to show them picked again after a refused save."""
        return [day.isoformat() for day in self.picked]

    def clean_start_time(self) -> time:
        return _parse_half_hour(self.cleaned_data["start_time"])

    def clean_end_time(self) -> time:
        return _parse_half_hour(self.cleaned_data["end_time"])

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        if not self.picked:
            self.add_error(None, "Pick at least one date.")
        if self.errors:
            return cleaned
        rows = [self._row(day, cleaned) for day in self.picked]
        try:
            rows[0].clean()
        except forms.ValidationError as error:
            for field, messages in error.message_dict.items():
                self.add_error(field if field in self.fields else None, messages)
            return cleaned
        for problem in self._problems(rows):
            self.add_error(None, problem)
        self.rows = rows
        return cleaned

    def _row(self, day: date, cleaned: dict[str, Any]) -> TourHours:
        return TourHours(
            guide=self.guide,
            date=day,
            weekday=day.weekday(),
            start_time=cleaned["start_time"],
            end_time=cleaned["end_time"],
            tour_minutes=cleaned["tour_minutes"],
            seats=cleaned["seats"],
        )

    def _problems(self, rows: list[TourHours]) -> list[str]:
        """One message per refused date, in date order: out of range, a day off, or a clash."""
        today = timezone.localdate()
        last = today + timedelta(days=MAX_DATED_DAYS_AHEAD)
        first_day, last_day = cast(date, rows[0].date), cast(date, rows[-1].date)
        days_off = list(TourDayOff.objects.filter(guide=self.guide).touching(first_day, last_day))
        saved = list(
            TourHours.objects.filter(guide=self.guide, weekday__in={row.weekday for row in rows}).touching_days(
                first_day, last_day
            )
        )
        problems: list[str] = []
        for row in rows:
            day = cast(date, row.date)
            label = day_label(day)
            if day < today:
                problems.append(f"{label} has passed. Pick today or a day ahead.")
            elif day > last:
                problems.append(f"{label} is more than a year ahead.")
            elif any(day_off.covers(day) for day_off in days_off):
                problems.append(f"{label} is a day off. Delete the day off first.")
            else:
                for other in saved:
                    if not row.overlaps(other):
                        continue
                    if other.date is None:
                        problems.append(
                            f"{label}: overlaps your weekly {other.get_weekday_display()} hours, {other.times_label}."
                        )
                    else:
                        problems.append(f"{label}: overlaps hours already on that date, {other.times_label}.")
        return problems

    def save(self) -> list[TourHours]:
        """Write every picked date's hours at once: all of them or, on any failure, none.

        The view validates and saves under :func:`tours.models.lock_guide_hours`, in one
        transaction, so the overlap check still holds when the rows are written.
        """
        with transaction.atomic():
            return TourHours.objects.bulk_create(self.rows)


class TourDayOffForm(forms.ModelForm):
    """A day, or a run of days, one guide is away. The guide is fixed by the view, never posted.

    Leave the last day blank for a single day. A past first day, a last day before the first, a
    run longer than a year, and a run that overlaps a day off already saved are refused.
    """

    class Meta:
        model = TourDayOff
        fields = ["starts_on", "ends_on", "note"]
        labels = {"starts_on": "First day off", "ends_on": "Last day off", "note": "Note"}
        widgets = {
            # Rule 14: the whole field opens the picker. No .pl-slot-date: components.css already gives
            # date inputs in a .pl-form-group the dark color-scheme, and inverting on top of it turns the
            # icon black again on the dark theme.
            "starts_on": forms.DateInput(attrs={"type": "date", "onclick": "try { this.showPicker() } catch (e) {}"}),
            "ends_on": forms.DateInput(attrs={"type": "date", "onclick": "try { this.showPicker() } catch (e) {}"}),
            "note": forms.TextInput(attrs={"placeholder": "Optional: a word on why", "class": _TEXT}),
        }

    def __init__(self, *args: Any, guide: Member, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.guide = guide
        self.fields["ends_on"].required = False
        self.fields["starts_on"].help_text = ""
        self.fields["ends_on"].help_text = "Leave blank for a single day."
        self.fields["note"].help_text = "Visitors never see it."

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
        self.instance.ends_on = last
        if last < first:
            self.add_error("ends_on", "The last day must be on or after the first.")
        elif last - first >= timedelta(days=MAX_DAY_OFF_DAYS):
            self.add_error("ends_on", "Pick a run of a year or less.")
        else:
            clash = TourDayOff.objects.filter(guide=self.guide).touching(first, last).first()
            if clash is not None:
                self.add_error("starts_on", f"Part of that is already off: {clash.label}.")
        return cleaned

    def save(self, commit: bool = True) -> TourDayOff:
        self.instance.guide = self.guide
        return cast(TourDayOff, super().save(commit=commit))


class FacilityTourSettingsForm(forms.ModelForm):
    """The Tours page's Settings tab (admins only): the house rules every guide's hours follow.

    The closed message is asked for only while bookings are closed; left blank while they are
    open, the saved message stays as it was.
    """

    default_tour_minutes = forms.TypedChoiceField(
        coerce=int, choices=_SLOT_DURATION_CHOICES, label="Default tour length"
    )

    class Meta:
        model = FacilityTourSettings
        fields = [
            "accepting_bookings",
            "closed_message",
            "booking_horizon_days",
            "min_notice_hours",
            "cancel_cutoff_hours",
            "default_tour_minutes",
            "default_seats",
        ]
        labels = {
            "accepting_bookings": "Accepting tour bookings",
            "closed_message": "Closed message",
            "booking_horizon_days": "Booking horizon (days)",
            "min_notice_hours": "Minimum notice (hours)",
            "cancel_cutoff_hours": "Cancel and reschedule cutoff (hours)",
            "default_seats": "Default seats per tour",
        }
        widgets = {
            "closed_message": forms.TextInput(attrs={"class": _TEXT}),
            "booking_horizon_days": forms.NumberInput(attrs={"min": "1", "max": "365", "class": _NARROW}),
            "min_notice_hours": forms.NumberInput(attrs={"min": "0", "max": "336", "class": _NARROW}),
            "cancel_cutoff_hours": forms.NumberInput(attrs={"min": "0", "max": "336", "class": _NARROW}),
            "default_seats": forms.NumberInput(attrs={"min": "1", "max": str(MAX_SEATS), "class": _NARROW}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["closed_message"].required = False
        self.fields["accepting_bookings"].help_text = "Off, the tours site shows the closed message instead of times."
        self.fields[
            "booking_horizon_days"
        ].help_text = "How far ahead visitors can book. 30 means up to 30 days from today."
        self.fields["min_notice_hours"].help_text = "How close to a tour it can still be booked."
        self.fields[
            "cancel_cutoff_hours"
        ].help_text = "How close to a tour visitors can still cancel or move it from their email."
        self.fields["default_tour_minutes"].help_text = "What a new row of tour hours starts with."
        self.fields["default_seats"].help_text = "What a new row of tour hours starts with. Use 1 to 50."
        if self.instance.pk:
            _append_duration_choice(self.fields["default_tour_minutes"], self.instance.default_tour_minutes)

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        message = str(cleaned.get("closed_message") or "").strip()
        if message:
            cleaned["closed_message"] = message
        elif cleaned.get("accepting_bookings") is False:
            self.add_error("closed_message", "Write what visitors read while bookings are closed.")
        else:
            cleaned["closed_message"] = self.instance.closed_message
        return cleaned


#: The account toggle's words on the booking page (#948).
CREATE_ACCOUNT_LABEL = "Create a free Past Lives account (handy if you join later)"


class TourBookingForm(forms.Form):
    """The visitor's details on tours.pastlives.space (#948): name, email, phone, party size.

    ``tour`` is the chosen tour's :attr:`tours.availability.OpenTour.key`. The party size offers
    1 to ``max_party``: the tour's seats left when the form is shown. A POST passes the most a
    tour can hold instead, so a tour that filled up while the visitor typed is refused by the
    booking's own seat count under the lock ("That time just filled up") rather than by this
    form. ``create_account`` (the optional account toggle) is offered only to a signed out
    visitor.
    """

    tour = forms.CharField(widget=forms.HiddenInput)
    name = forms.CharField(max_length=200, label="Your name")
    email = forms.EmailField(label="Email")
    phone = forms.CharField(max_length=40, required=False, label="Phone (optional)")
    party_size = forms.TypedChoiceField(coerce=int, label="How many people")
    # Starts on: accounts are encouraged (Felix, #948), and the class booking form defaults its box on too.
    create_account = forms.BooleanField(required=False, initial=True, label=CREATE_ACCOUNT_LABEL)
    #: A honeypot: off screen for a person, filled by a bot that fills every field (the guest
    #: RSVP form's ``website``). A filled one books nothing.
    website = forms.CharField(
        required=False,
        label="Leave this field empty",
        widget=forms.TextInput(attrs={"autocomplete": "off", "tabindex": "-1", "aria-hidden": "true"}),
    )

    def __init__(self, *args: Any, max_party: int, offer_account: bool, kiosk: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        top = max(1, min(max_party, MAX_SEATS))
        party = cast(forms.TypedChoiceField, self.fields["party_size"])
        party.choices = [(str(size), str(size)) for size in range(1, top + 1)]
        # At the front desk kiosk (#950) the browser must never offer the last visitor's details.
        self.fields["name"].widget.attrs.update({"autocomplete": "off" if kiosk else "name"})
        self.fields["email"].widget.attrs.update({"autocomplete": "off" if kiosk else "email"})
        self.fields["phone"].widget = forms.TextInput(attrs={"type": "tel", "autocomplete": "off" if kiosk else "tel"})
        if not offer_account:
            del self.fields["create_account"]

    @property
    def honeypot_tripped(self) -> bool:
        """Whether the hidden field came back filled: a bot, answered as if nothing went wrong."""
        return bool(str(self.data.get("website", "")).strip())


#: The longest note staff can add to a visitor's moved or cancelled email (#949).
MAX_STAFF_MESSAGE = 1000


class StaffTourMessageForm(forms.Form):
    """The optional note to the visitor on a staff cancel (#949), from the confirm modal's input."""

    message = forms.CharField(
        required=False,
        max_length=MAX_STAFF_MESSAGE,
        label="Message to the visitor (optional)",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Added to the email we send them.",
    )


class TourConfirmModeForm(forms.ModelForm):
    """The My Hours tab's Confirming Bookings card (#1166): Confirm First or Confirm Automatically."""

    class Meta:
        model = TourGuideSettings
        fields = ("confirm_mode",)
        widgets = {"confirm_mode": forms.RadioSelect}


class StaffRescheduleForm(StaffTourMessageForm):
    """The staff reschedule's confirm (#949): the tour picked (an ``OpenTour.key``) and the optional note."""

    tour = forms.CharField(widget=forms.HiddenInput)


class TourCalendarChoiceForm(forms.Form):
    """Which of the guide's own Google calendars tours are written to (#951): the My Hours tab's picker.

    The choices are the calendars the guide owns, as the sync job last read them, main one first
    (its value is ``primary``). A value Google never listed is refused.
    """

    calendar = forms.ChoiceField(label="Write tours to", widget=forms.Select(attrs={"class": _TEXT}))

    def __init__(self, *args: Any, connection: TourCalendarConnection, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        choices = [
            (value, f"{label} (main calendar)" if value == "primary" else label)
            for value, label in connection.calendar_choices()
        ] or [("primary", "Your main calendar")]
        field = cast("forms.ChoiceField", self.fields["calendar"])
        field.choices = choices
        field.initial = connection.calendar_id
        field.help_text = "Times this calendar or your main one shows you busy are not offered to visitors."
