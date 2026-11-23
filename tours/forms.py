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
    ThreadMessageForm,
    _append_duration_choice,
    _meeting_time_label,
    _seed_time_choice,
    half_hour_time_choices,
)
from membership.models import Member
from tours.models import (
    MAX_SEATS,
    FacilityTourSettings,
    FoundUs,
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
            # A new row opens with From and Until blank, like its Day: the autosave (#1279) saves a
            # row once every required field is picked, so a row must not look picked before it is.
            for name in ("start_time", "end_time"):
                cast(forms.ChoiceField, self.fields[name]).choices = half_hour_time_choices(required=False)
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

    Every overlap is the row's own error (#1279), never the formset's, so the autosave shows it on
    the row (``hours-<i>-__all__``); two rows that overlap each get one naming the other.
    """

    def get_form_kwargs(self, index: int | None) -> dict[str, Any]:
        """A saved row the page still shows but that is gone (deleted elsewhere, #1279) reads as absent.

        Its posted id names no weekly row of this guide any more, which Django would refuse
        ("Select a valid choice") on every later save from that tab. Built without its fields and
        allowed empty, it is skipped like a removed clone, the rest of the post saves, and
        :attr:`gone_rows` tells the autosave to take it off the page.
        """
        kwargs = super().get_form_kwargs(index)
        if index is not None and self.is_bound and index < self.initial_form_count() and self._is_gone(index):
            prefix = f"{self.add_prefix(index)}-"
            kwargs["data"] = {key: value for key, value in self.data.items() if not key.startswith(prefix)}
            kwargs["empty_permitted"] = True
        return kwargs

    def _is_gone(self, i: int) -> bool:
        raw = str(self.data.get(f"{self.add_prefix(i)}-id", ""))
        if not raw.isdigit():
            return False
        if not hasattr(self, "_weekly_pks"):
            self._weekly_pks = {row.pk for row in self.get_queryset()}
        return int(raw) not in self._weekly_pks

    @property
    def gone_rows(self) -> list[int]:
        """The posted indices of rows that were deleted elsewhere since the page loaded."""
        return [index for index in range(self.initial_form_count()) if self._is_gone(index)]

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        kept = [form for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE")]
        for index, form in enumerate(kept):
            for other in kept[index + 1 :]:
                if form.instance.overlaps(other.instance):
                    _refuse_row(form, _overlap_message(other.instance))
                    _refuse_row(other, _overlap_message(form.instance))
        self._refuse_clash_with_dated(kept)
        carried = {form.instance.pk for form in self.forms if form.instance.pk is not None}
        for saved in self.get_queryset().exclude(pk__in=carried):
            for form in kept:
                if form.instance.overlaps(saved):
                    _refuse_row(
                        form,
                        f"These hours overlap saved {saved.get_weekday_display()} hours of "
                        f"{_meeting_time_label(saved.start_time.hour, saved.start_time.minute)} to "
                        f"{_meeting_time_label(saved.end_time.hour, saved.end_time.minute)} that this page "
                        "does not show. Reload the page and try again.",
                    )

    def _refuse_clash_with_dated(self, kept: list[forms.ModelForm]) -> None:
        """Refuse a weekly row that overlaps upcoming dated hours (#1160) on its weekday, naming the date."""
        if not kept:
            return
        dated = TourHours.objects.upcoming_dated().filter(
            guide=self.instance, weekday__in={form.instance.weekday for form in kept}
        )
        for other in dated:
            for form in kept:
                row = form.instance
                if row.overlaps(other):
                    _refuse_row(
                        form,
                        f"Every {row.get_weekday_display()} {row.times_label} overlaps your hours on "
                        f"{day_label(cast(date, other.date))}, {other.times_label}.",
                    )


def _overlap_message(other: TourHours) -> str:
    """A row's error when it overlaps ``other`` among the rows posted together."""
    return f"These hours overlap your {other.get_weekday_display()} hours, {other.times_label}. A guide gives one tour at a time."


def _refuse_row(form: forms.BaseForm, message: str) -> None:
    """Put ``message`` on the row itself, once however many clashes name it."""
    if message not in form.non_field_errors():
        form.add_error(None, message)


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


#: The booking questions on :class:`TourBookingForm`, in order (feedback request #32). They are
#: :data:`tours.booking.ANSWER_FIELDS` too; a spec holds the two to the same names.
QUESTION_FIELDS = ("pronouns", "found_us_through", "referred_by", "guest_notes")


#: The answer that asks for a name (Morlock, 2026-11-22), for the template's x-show.
TOLD_ANSWER = FoundUs.TOLD.value


class TourBookingForm(forms.Form):
    """The visitor's details on tours.pastlives.space (#948): name, email, phone (required since
    feedback request #32), party size, then SimplyBook's optional questions (#32): pronouns,
    how they found us, who referred them, and what brings them here.

    ``tour`` is the chosen tour's :attr:`tours.availability.OpenTour.key`. The party size offers
    1 to ``max_party``: the tour's seats left when the form is shown. A POST passes the most a
    tour can hold instead, so a tour that filled up while the visitor typed is refused by the
    booking's own seat count under the lock ("That time just filled up") rather than by this
    form. It offers no account (Morlock, 2026-11-22): a tour finds its visitor's account by
    verified email when they sign in (:func:`tours.booking.claim_tours`).
    """

    tour = forms.CharField(widget=forms.HiddenInput)
    # The required three say so; nothing else is required, so nothing says "optional" (Morlock).
    name = forms.CharField(max_length=200, label="Your name (required)")
    email = forms.EmailField(label="Email (required)")
    phone = forms.CharField(max_length=40, label="Phone (required)")
    party_size = forms.TypedChoiceField(coerce=int, label="How many people")
    # SimplyBook's questions (feedback request #32), each optional and in its order, so the guide
    # knows who is coming and what to show them. Stairs is not asked: the freight elevator is
    # there for anyone who wants it (Morlock).
    pronouns = forms.CharField(max_length=60, required=False, label="Pronouns")
    found_us_through = forms.ChoiceField(
        required=False,
        choices=[("", "Pick one"), *FoundUs.choices],
        label="How did you learn about us?",
    )
    # Asked only once they pick "Someone told me about it!" (Morlock, 2026-11-22): _book_form.html shows it
    # then, and clean() drops a name typed before another answer was picked. The bonus is Morlock's words.
    referred_by = forms.CharField(
        max_length=200,
        required=False,
        label="What was their name?",
        help_text="If they're an active member here, they'll get a $50 referral bonus when you join.",
    )
    # One open question in place of "most interested in" and "anything else" (Morlock, 2026-11-22):
    # what they make and what they want from us, so the guide knows where to take them.
    guest_notes = forms.CharField(
        max_length=1000,
        required=False,
        label="What brings you to Past Lives?",
        help_text=(
            "Tell us a little about what you make, or want to make, and what you're hoping to find here. "
            "Your guide reads it before your tour."
        ),
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    wants_studio = forms.BooleanField(required=False, initial=False, label="I'm interested in a dedicated studio space")
    # Shown once the box above is ticked (Alpine, _book_form.html); kept only with the box.
    studio_description = forms.CharField(
        max_length=1000,
        required=False,
        label="Describe your ideal studio and what you'd do with it",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    # Unticked, as on the class registration form (feedback request #35).
    wants_newsletter = forms.BooleanField(
        required=False, initial=False, label="Sign me up for the Past Lives newsletter"
    )
    #: A honeypot: off screen for a person, filled by a bot that fills every field (the guest
    #: RSVP form's ``website``). A filled one books nothing.
    website = forms.CharField(
        required=False,
        label="Leave this field empty",
        widget=forms.TextInput(attrs={"autocomplete": "off", "tabindex": "-1", "aria-hidden": "true"}),
    )

    def __init__(self, *args: Any, max_party: int, kiosk: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        top = max(1, min(max_party, MAX_SEATS))
        party = cast(forms.TypedChoiceField, self.fields["party_size"])
        party.choices = [(str(size), str(size)) for size in range(1, top + 1)]
        # The Guide dropdown (#1255, _book_form.html) greys out a guide without room for this party.
        party.widget.attrs["x-model.number"] = "party"
        # At the front desk kiosk (#950) the browser must never offer the last visitor's details.
        self.fields["name"].widget.attrs.update({"autocomplete": "off" if kiosk else "name"})
        self.fields["email"].widget.attrs.update({"autocomplete": "off" if kiosk else "email"})
        self.fields["phone"].widget = forms.TextInput(attrs={"type": "tel", "autocomplete": "off" if kiosk else "tel"})
        # The ideal studio field follows the studio box (Morlock).
        self.fields["wants_studio"].widget.attrs["x-model"] = "studio"
        # The referral question follows "Someone told me about it!" (Morlock).
        self.fields["found_us_through"].widget.attrs["x-model"] = "found"
        if kiosk:
            for name in ("pronouns", "referred_by", "guest_notes", "studio_description"):
                self.fields[name].widget.attrs["autocomplete"] = "off"

    @property
    def told_answer(self) -> str:
        """The found us answer that opens the referral question, for ``_book_form.html``."""
        return TOLD_ANSWER

    @property
    def question_fields(self) -> list[forms.BoundField]:
        """The booking questions (feedback request #32), in order, for the template to draw."""
        return [self[name] for name in QUESTION_FIELDS]

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if not cleaned.get("wants_studio"):
            # Typed, then the box was unticked: the field was hidden, so it was not meant to send.
            cleaned["studio_description"] = ""
        if cleaned.get("found_us_through") != FoundUs.TOLD:
            # The same for a name typed before another way of learning about us was picked.
            cleaned["referred_by"] = ""
        return cleaned

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
    """The My Hours tab's Confirming Bookings card (#1166): Confirm First or Confirm Automatically,
    and under Confirm First how far ahead visitors must book."""

    class Meta:
        model = TourGuideSettings
        fields = ("confirm_mode", "min_notice_hours")
        labels = {"min_notice_hours": "Visitors book at least (hours ahead)"}
        widgets = {
            "confirm_mode": forms.RadioSelect,
            "min_notice_hours": forms.NumberInput(attrs={"class": "pl-tours-input--narrow"}),
        }

    def clean_min_notice_hours(self) -> int:
        """Never below Tour Settings' minimum notice, and over the 24 hours a request has to be confirmed by."""
        hours = int(self.cleaned_data["min_notice_hours"])
        house = FacilityTourSettings.load().min_notice_hours
        if hours < house:
            raise forms.ValidationError(f"At least {house} hours: the minimum notice in Tour Settings.")
        return hours


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


class TourNoteForm(ThreadMessageForm):
    """A note on a booking (#1274): the shared thread's box, and for whoever may act on it, Also Email the Visitor.

    The photos stay on the thread; the visitor's email carries the words alone.
    """

    email_visitor = forms.BooleanField(
        required=False,
        label="Also email this to the visitor",
        help_text="They get your words by email, signed with your first name. Photos stay here.",
    )

    def __init__(self, *args: Any, may_email: bool, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["body"].label = "Note"
        if not may_email:
            del self.fields["email_visitor"]
