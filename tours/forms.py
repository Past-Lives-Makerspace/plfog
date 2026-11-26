"""Forms for the Tours page's Settings tab (#947), Booking Notice and the calendar picker (on My Hours since
#1300), staff moves and notes, and the visitor's booking form (#948).

A guide's hours are set on My Hours (``hours.forms``) since #1300; the Tours page's own hours, dates
and day off forms went with its old My Hours tab. Named apart from ``hub.forms.TourSettingsForm``,
which is the guided tours' toggle.
"""

from __future__ import annotations

from typing import Any, cast

from django import forms

from hub.forms import (
    _SLOT_DURATION_CHOICES,
    ThreadMessageForm,
    _append_duration_choice,
)
from tours.models import (
    MAX_SEATS,
    FacilityTourSettings,
    FoundUs,
    TourCalendarConnection,
    TourGuideSettings,
    notice_label,
)

_NARROW = "pl-tours-input--narrow"
_TEXT = "pl-tours-input--text"


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
            "min_notice_hours": "Default minimum notice (hours)",
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
        self.fields["min_notice_hours"].help_text = (
            "How far ahead visitors must book a guide who has not set their own notice on My Hours. A guide may "
            "pick less or more."
        )
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


#: The steps a guide picks their own minimum notice from (#1312, Felix), in minutes.
NOTICE_STEPS: tuple[int, ...] = (0, 15, 30, 60, 120, 240, 720, 1440, 2880, 7 * 1440, 14 * 1440)


class TourNoticeForm(forms.ModelForm):
    """My Hours' Booking Notice card (#1312): how far ahead website visitors must book this guide.

    A select of :data:`NOTICE_STEPS`, from No notice to two weeks, with House default first (blank:
    Tour Settings' minimum notice, named with its value). The house value is a default, never a floor.
    """

    min_notice_minutes = forms.TypedChoiceField(coerce=int, empty_value=None, required=False, label="Booking notice")

    class Meta:
        model = TourGuideSettings
        fields = ("min_notice_minutes",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        house = FacilityTourSettings.load().min_notice_hours * 60
        field = cast(forms.TypedChoiceField, self.fields["min_notice_minutes"])
        field.choices = [
            ("", f"House default ({notice_label(house)})"),
            *(
                (str(minutes), "No notice, book me now" if minutes == 0 else notice_label(minutes))
                for minutes in NOTICE_STEPS
            ),
        ]
        field.widget.attrs.update({"class": "pl-tours-input--choice", "data-tour-notice": ""})
        field.help_text = (
            "How far ahead visitors on the website must book your tours. Pick No notice or 15 minutes when you "
            "are here and free now. The front desk kiosk and staff are never held to it."
        )


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
