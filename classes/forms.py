"""Forms for the Classes app."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any, TypeGuard, cast
from urllib.parse import urlencode

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, validate_email
from django.db.models import BLANK_CHOICE_DASH, Q
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.forms.formsets import DELETION_FIELD_NAME
from django.utils import timezone
from django.utils.text import slugify

from billing.forms import RefundShareDecisionForm
from core.html_sanitize import clean_rich_body, clean_rich_html
from core.integrations.eventbrite import EVENTBRITE_RULES, ListingCheck
from core.widgets import PageContentEditorWidget, RichBodyEditorWidget, RichTextEditorWidget

from classes.eventbrite_categories import SUBCATEGORY_PARENT, EventbriteSubcategory, subcategory_fits
from classes.models import (
    DEFAULT_CLASS_FAQS,
    DEFAULT_SALE_BANNER_TEXT,
    LOCKED_CLASS_FAQS,
    READINESS_MIN_DESCRIPTION_CHARS,
    Category,
    ClassFaq,
    ClassImage,
    ClassOffering,
    ClassSession,
    ClassSettings,
    DiscountCode,
    DiscountCodeRequest,
    InstructorMessage,
    InstructorMessageRecipient,
    Registration,
    RegistrationAnswer,
    RegistrationQuestion,
    Waiver,
    _unique_slug,
    is_locked_class_faq,
)
from classes.questions import active_questions, collect_answers, inject_fields
from classes.video_providers import validate_video_url
from membership.forms import setup_location_field


def _assign_provisional_slug(offering: ClassOffering) -> None:
    """Give a not-yet-saved offering a unique title-based slug when it lacks one.

    The ``slug`` column is unique and NOT NULL, so a new offering must carry a
    valid slug before its first save — two blank slugs would collide on the
    unique constraint. Both create forms call this to stamp a provisional
    ``slugify(title)`` slug; the create view then calls
    :meth:`ClassOffering.finalize_recurring_slug` once the sessions are attached
    to upgrade it to the canonical date-stamped form. A no-op when the offering
    already has a slug, so it never re-slugs an existing offering on edit.
    """
    if offering.slug:
        return
    base = slugify(offering.title) or "class"
    offering.slug = _unique_slug(base, exclude_pk=offering.pk)


def _video_url_widget() -> forms.TextInput:
    """The composer's video link as a plain text control, never ``<input type="url">``.

    ``forms.URLField`` accepts a link typed without a scheme and normalises it
    (``assume_scheme="https"``), and :func:`validate_video_url` then takes
    ``youtube.com/watch?v=…`` exactly as it is handed over. Chromium's ``type="url"``
    refuses that same string in the browser. The composer's per step check reads the
    rendered control as its rule book (``static/js/composer_validation.js``), so a URL
    input would make Next block input the server accepts and normalises: the rendered
    DOM has to stay the single rule book. ``inputmode="url"`` keeps the URL keyboard on
    a phone without the constraint. A fresh widget per call, so two form classes never
    share one instance.
    """
    return forms.TextInput(attrs={"inputmode": "url"})


if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser, AnonymousUser, User

    from datetime import datetime

    from classes.inquiries import BoardReport
    from membership.models import Member, MemberQuerySet


STRIPE_MIN_CHARGE_CENTS = 50  # Stripe's minimum USD charge is $0.50.
MIN_PAID_PRICE_CENTS = 100  # Floor for every class ($1.00). There is no free option (#368 item 5).
PRICE_FLOOR_MESSAGE = "Classes cost at least $1.00."
CLASS_LOCATION_HINT = (
    "The area of the building it meets in. Its guild page shows the area in use during class. Optional."
)
PRICE_HELP_TEXT = "In dollars, e.g. 80.00 for $80. Every class costs at least $1.00."
# Under the description box on both composers (#425): the readiness minimum, from the one constant
# the rule reads, so the hint can never name a number the checklist would then contradict.
DESCRIPTION_HELP_TEXT = f"At least {READINESS_MIN_DESCRIPTION_CHARS} characters. Say what students make and take home."
# Under the Subtitle box on both composers and the live class edit page (#563).
SUBTITLE_HELP_TEXT = "Optional. A short line under the title, like Pick Your October Time."


class CentsAsDollarsField(forms.DecimalField):
    """Accepts dollar input, stores as cents. Model stays PositiveIntegerField."""

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("max_digits", 8)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", Decimal("0"))
        super().__init__(**kwargs)

    def prepare_value(self, value: int | str | None) -> Decimal | int | str | None:
        """Cents from the model render as dollars; anything else renders exactly as it came in.

        A bound field hands the raw POST string back through here on a failed save, and that
        string is already dollars: a typed "80" must re-render as 80, not as 0.8.
        """
        if isinstance(value, int):
            return Decimal(value) / 100
        return value

    def clean(self, value: str) -> int | None:
        dollars = super().clean(value)
        if dollars is None:
            return None
        return int((dollars * 100).to_integral_value())


class _HeroCropMixin:
    """Adds a hidden ``hero_crop`` JSON field bound to the four hero_crop_* ints.

    The Cropper.js glue in ``static/js/hero_cropper.js`` writes the crop box to
    this field as ``{"x": int, "y": int, "w": int, "h": int}`` (or an empty
    string when the user hasn't cropped). On ``save()``, the four pixel ints
    land on the model.
    """

    def add_hero_crop_field(self) -> None:
        instance = getattr(self, "instance", None)
        initial = ""
        # No box is offered on an imported photo, so none is pre-filled: posting it back
        # would overwrite a focal point set with Adjust on the preview.
        if (
            instance
            and instance.pk
            and instance.hero_crop_w
            and instance.hero_crop_h
            and not instance.has_imported_photo_only
        ):
            initial = json.dumps(
                {
                    "x": instance.hero_crop_x or 0,
                    "y": instance.hero_crop_y or 0,
                    "w": instance.hero_crop_w,
                    "h": instance.hero_crop_h,
                }
            )
        self.fields["hero_crop"] = forms.CharField(  # type: ignore[attr-defined]
            required=False,
            initial=initial,
            widget=forms.HiddenInput(attrs={"data-hero-crop-input": ""}),
        )

    def clean_hero_crop(self):
        raw = (self.cleaned_data.get("hero_crop") or "").strip()  # type: ignore[attr-defined]
        if not raw:
            return None
        try:
            data = json.loads(raw)
            x = int(data["x"])
            y = int(data["y"])
            w = int(data["w"])
            h = int(data["h"])
        except (ValueError, KeyError, TypeError):
            raise forms.ValidationError("Crop box is malformed; clear it and try again.") from None
        if w <= 0 or h <= 0 or x < 0 or y < 0:
            raise forms.ValidationError("Crop box must be a positive rectangle.")
        return {"x": x, "y": y, "w": w, "h": h}

    def apply_hero_crop_to_instance(self, offering: ClassOffering) -> None:
        crop = self.cleaned_data.get("hero_crop")  # type: ignore[attr-defined]
        if crop is None:
            return
        offering.hero_crop_x = crop["x"]
        offering.hero_crop_y = crop["y"]
        offering.hero_crop_w = crop["w"]
        offering.hero_crop_h = crop["h"]


class _CardFocusMixin:
    """Adds a hidden ``card_focus`` JSON field bound to ``card_focus_x`` / ``card_focus_y``.

    The card focus tool in ``static/js/card_focus.js`` writes ``{"x": int, "y": int}``
    (percentages, 0 to 100) on every slider move, and an empty string when the
    instructor chooses Match the Banner. Empty clears both columns to null, which the
    model reads as "follow the banner". Saves with the form, exactly like ``hero_crop``,
    so the Photos step has one save rule rather than two.
    """

    def add_card_focus_field(self) -> None:
        instance = getattr(self, "instance", None)
        initial = ""
        if instance and instance.pk and instance.card_focus_x is not None and instance.card_focus_y is not None:
            initial = json.dumps({"x": instance.card_focus_x, "y": instance.card_focus_y})
        self.fields["card_focus"] = forms.CharField(  # type: ignore[attr-defined]
            required=False,
            initial=initial,
            widget=forms.HiddenInput(attrs={"data-card-focus-input": ""}),
        )

    def clean_card_focus(self) -> dict[str, int] | None:
        raw = (self.cleaned_data.get("card_focus") or "").strip()  # type: ignore[attr-defined]
        if not raw:
            return None
        try:
            data = json.loads(raw)
            x = int(data["x"])
            y = int(data["y"])
        except (ValueError, KeyError, TypeError):
            raise forms.ValidationError("Focal point is malformed; clear it and try again.") from None
        if not (0 <= x <= 100 and 0 <= y <= 100):
            raise forms.ValidationError("Focal point must be between 0 and 100.")
        return {"x": x, "y": y}

    def apply_card_focus_to_instance(self, offering: ClassOffering) -> None:
        focus = self.cleaned_data.get("card_focus")  # type: ignore[attr-defined]
        if focus is None:
            offering.card_focus_x = None
            offering.card_focus_y = None
            return
        offering.card_focus_x = focus["x"]
        offering.card_focus_y = focus["y"]


GALLERY_FOCUS_INVALID = 'Send {"x": 0 to 100, "y": 0 to 100}, or both as null to reset.'


def _is_percent(value: object) -> TypeGuard[int]:
    """A whole number from 0 to 100. ``True`` is an int to Python and is refused here on purpose."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100


def parse_gallery_focus(body: bytes) -> tuple[int, int] | None:
    """The gallery Set focus tool's JSON body, checked: a point, or None for Reset.

    Stricter than ``_CardFocusMixin.clean_card_focus`` on purpose: that field rides a form
    the composer itself fills, while this body arrives on its own route, so a string, a
    float, a bool, a missing or extra key, or one null beside a number is refused rather
    than coerced.

    Args:
        body: The raw request body.

    Returns:
        ``(x, y)`` percentages, or None when both are null.

    Raises:
        ValidationError: Anything other than ``{"x": int, "y": int}`` in range or ``{"x": null, "y": null}``.
    """
    try:
        data = json.loads(body)
    except ValueError:
        raise ValidationError(GALLERY_FOCUS_INVALID) from None
    if not isinstance(data, dict) or set(data) != {"x", "y"}:
        raise ValidationError(GALLERY_FOCUS_INVALID)
    x, y = data["x"], data["y"]
    if x is None and y is None:
        return None
    if not (_is_percent(x) and _is_percent(y)):
        raise ValidationError(GALLERY_FOCUS_INVALID)
    return (x, y)


class _PricingRulesMixin:
    """The pricing rules both composer forms share, from either portal.

    Every class costs at least $1.00 (:data:`MIN_PAID_PRICE_CENTS`). There is no free
    option: a $0 total is something a discount reaches at registration, never a price an
    instructor or admin can set. ``price_cents`` is a required field, so a blank is refused
    by Django before this runs; this refuses anything typed under the floor, on the price
    field, in plain words. Django finds ``clean_<field>`` through the MRO.
    """

    def clean_price_cents(self) -> int:
        price: int = self.cleaned_data["price_cents"]  # type: ignore[attr-defined]
        if price < MIN_PAID_PRICE_CENTS:
            raise forms.ValidationError(PRICE_FLOOR_MESSAGE)
        return price


class _SaleMixin:
    """Declares sale_amount_cents as dollars and validates the Sale section.

    Enabling a sale needs a kind + the matching amount; percent must be 1–99; a
    fixed amount must be less than the price; a class with no price can't be put on
    sale; and a blank banner falls back to the catchy default (never blocks save).
    The declared CentsAsDollarsField shadows the model's integer-cents column, and
    clean_sale_fields() is invoked from clean().
    """

    def clean_sale_fields(self) -> None:
        cleaned = self.cleaned_data  # type: ignore[attr-defined]
        if not cleaned.get("sale_enabled"):
            return
        # ERROR VISIBILITY: sale_enabled renders through toggle.html, which shows
        # NO field.errors — so an error attached to sale_enabled is silently
        # swallowed. Every error here targets a VISIBLE non-toggle field
        # (price_cents, sale_percent, sale_amount_cents); the edit templates also
        # render a non_field_errors block for any future cross-field case.
        price = cleaned.get("price_cents")
        if not price:  # None / "" / 0: a legacy $0 row has nothing to discount
            self.add_error("price_cents", "Set a price before putting this class on sale.")  # type: ignore[attr-defined]
            return
        self._validate_sale_amount(cleaned, price)
        self._validate_sale_stripe_floor(cleaned, price)
        if not (cleaned.get("sale_banner_text") or "").strip():
            cleaned["sale_banner_text"] = DEFAULT_SALE_BANNER_TEXT  # required-with-default

    def _validate_sale_amount(self, cleaned: dict, price: int) -> None:
        """Require the amount matching the chosen kind; percent 1–99, fixed < price."""
        if cleaned.get("sale_kind") == ClassOffering.SaleKind.PERCENT:
            pct = cleaned.get("sale_percent")
            if not pct:
                self.add_error("sale_percent", "Enter the percent off (1–99).")  # type: ignore[attr-defined]
            elif not (1 <= pct <= 99):
                self.add_error("sale_percent", "Percent off must be between 1 and 99.")  # type: ignore[attr-defined]
        else:  # FIXED
            amt = cleaned.get("sale_amount_cents")
            if not amt:
                self.add_error("sale_amount_cents", "Enter the dollar amount off.")  # type: ignore[attr-defined]
            elif amt >= price:
                self.add_error(  # type: ignore[attr-defined]
                    "sale_amount_cents", "The amount off must be less than the price."
                )

    def _validate_sale_stripe_floor(self, cleaned: dict, price: int) -> None:
        """Reject a sale landing in the 1–49¢ dead-zone — it can't be charged online
        and the buyer has no code to remove. Only meaningful once the amount fields
        are valid (guarded on no prior errors for those fields)."""
        if self.errors.get("sale_percent") or self.errors.get("sale_amount_cents"):  # type: ignore[attr-defined]
            return
        resulting = self._resulting_sale_price_cents(cleaned, price)
        if resulting is not None and 0 < resulting < STRIPE_MIN_CHARGE_CENTS:
            target = (
                "sale_percent" if cleaned.get("sale_kind") == ClassOffering.SaleKind.PERCENT else "sale_amount_cents"
            )
            self.add_error(  # type: ignore[attr-defined]
                target,
                "This sale would drop the price below the $0.50 minimum we can charge online.",
            )

    @staticmethod
    def _resulting_sale_price_cents(cleaned: dict, price: int) -> int | None:
        """The public (non-member) sale price these cleaned values would produce,
        or None when the matching amount is absent. Mirrors ClassOffering.sale_price_cents."""
        if cleaned.get("sale_kind") == ClassOffering.SaleKind.PERCENT:
            pct = cleaned.get("sale_percent")
            return int(price * (100 - pct) / 100) if pct else None
        amt = cleaned.get("sale_amount_cents")
        return max(0, price - amt) if amt else None


class _LiveSaleGuardMixin:
    """Refuses a price edit that would break a sale already stored on the class.

    The six ``sale_*`` fields live in the sale modal now, so the composer never sees them;
    without this, a class on a fixed $80 sale could be re-priced to $50 and sell for $0
    (or a 99% sale re-priced to $1.00 and land under Stripe's floor). The check re-runs
    :class:`_SaleMixin`'s amount and Stripe floor rules against the STORED sale and the
    NEW price, and refuses the price change rather than touching the sale: the sale is
    the instructor's decision, made on the manage page, and only that page ends it.
    """

    def clean_price_against_live_sale(self) -> None:
        instance = self.instance  # type: ignore[attr-defined]
        if not instance.pk or not instance.sale_is_active:
            return
        if self.errors.get("price_cents"):  # type: ignore[attr-defined]
            return  # the price is already refused for a reason of its own (blank, or under the floor)
        price: int = self.cleaned_data["price_cents"]  # type: ignore[attr-defined]
        savings = instance.sale_savings_display
        stored = {
            "sale_kind": instance.sale_kind,
            "sale_percent": instance.sale_percent,
            "sale_amount_cents": instance.sale_amount_cents,
        }
        fixed_too_deep = (
            instance.sale_kind == ClassOffering.SaleKind.FIXED and (instance.sale_amount_cents or 0) >= price
        )
        resulting = _SaleMixin._resulting_sale_price_cents(stored, price)
        under_floor = resulting is not None and 0 < resulting < STRIPE_MIN_CHARGE_CENTS
        if fixed_too_deep or under_floor:
            self.add_error(  # type: ignore[attr-defined]
                "price_cents",
                f"This class is on sale for {savings}. Turn the sale off or change it from the manage page "
                "before setting a price this low.",
            )


class _SchedulingTypeMixin:
    """Renders ``scheduling_type`` as a guided two-option radio choice.

    The model stores ``single_session`` / ``series_package``; here we swap the
    widget to radio buttons and relabel them in plain language so the create/edit
    form reads as a direct "one-off class vs multi-session series" decision rather
    than a bare dropdown. Templates iterate the radio to render the option cards.
    """

    def setup_scheduling_type_field(self) -> None:
        field = self.fields["scheduling_type"]  # type: ignore[attr-defined]
        field.widget = forms.RadioSelect()
        field.choices = [  # type: ignore[attr-defined]  # ChoiceField setter propagates to the new widget
            (ClassOffering.SchedulingType.SINGLE_SESSION, "Single class (one date)"),
            (ClassOffering.SchedulingType.SERIES_PACKAGE, "Multi-session series"),
        ]
        field.label = "How does this class run?"


FLEXIBLE_WINDOW_ORDER_MESSAGE = "The last day is before the first day."


def _window_day_widget() -> forms.DateInput:
    """One day picker of a flexible class's window: a date, never a time (FRONTEND.md rule 20).

    It wears the session scheduler's input class, which carries the rule 14 dark mode picker
    fix, and the scheduler's click handler, so the whole field opens the picker. The ISO
    format is what a native date input reads and writes.
    """
    return forms.DateInput(
        attrs={
            "type": "date",
            "class": "session-cal__input",
            "@click": "(() => { try { $el.showPicker() } catch (e) {} })()",
        },
        format="%Y-%m-%d",
    )


def _booking_text_widget() -> forms.Textarea:
    return forms.Textarea(attrs={"rows": 3})


def setup_flexible_booking_text(form: forms.ModelForm) -> None:
    """Label the booking text box and pre-fill it with the line the class page shows today.

    The instructor edits the page's own words, not a blank box with a placeholder to decode.
    The pre-fill is only shown, never stored on its own: :func:`clean_flexible_booking_text`
    stores blank when what comes back is still the standard line, so the page keeps building
    it live (a renamed instructor, a window added later). ``form.initial`` is read at render
    time, so setting it here, after the ModelForm has copied the instance in, is enough.

    The line the box was filled with rides along in a hidden field, so the compare on the way
    back is against what the instructor actually saw: an instructor renamed while the composer
    sat open (58 classes moved from "Billy" to his full name on 2026-09-30) still counts an
    untouched box as untouched, instead of freezing the old name as their own words.
    """
    field = form.fields["flexible_booking_text"]
    field.label = "How booking works"
    field.help_text = "Shown on the class page under Flexible Scheduling. Change it to say how students book with you."
    form.fields["flexible_booking_text_default"] = forms.CharField(required=False, widget=forms.HiddenInput())
    form.initial["flexible_booking_text_default"] = form.instance.default_flexible_booking_line
    if not form.instance.flexible_booking_text:
        form.initial["flexible_booking_text"] = form.instance.default_flexible_booking_line


def _folded(text: str) -> str:
    return " ".join(text.split())


def clean_flexible_booking_text(form: forms.ModelForm) -> str:
    """The posted text, or blank when it is still the standard line the box was pre-filled with.

    Untouched means equal to the line the box was rendered with (the hidden field) or to
    today's standard line, both with the spacing folded so a wrapped paste of the same words
    still counts; a kept text is stored as typed, line breaks included. The hidden field is
    read from the raw POST because Django cleans it after this field; a POST without it (an
    older page still open) falls back to today's line alone.
    """
    text: str = form.cleaned_data["flexible_booking_text"]
    rendered = form.data.get(form.add_prefix("flexible_booking_text_default"), "")
    untouched = {_folded(rendered), _folded(form.instance.default_flexible_booking_line)}
    return "" if _folded(text) in untouched else text


class _FlexibleWindowMixin:
    """The optional date window of a flexible class, on both composer forms.

    ``scheduling_model`` binds the composer's Alpine state so step 3 swaps the scheduler for
    the window as the select changes; the two day fields and the note take their member
    facing labels here. ``clean_flexible_window`` keeps the stored row honest: a last day
    never precedes the first, and a class saved as Fixed sessions carries no window at all,
    whatever the hidden block posted.
    """

    def setup_flexible_window_fields(self) -> None:
        fields = self.fields  # type: ignore[attr-defined]
        model = fields["scheduling_model"]
        model.widget.attrs["x-model"] = "schedulingModel"
        model.help_text = "Fixed sessions: you set the dates and times. Flexible: each student books a day with you."
        fields["flexible_starts_on"].label = "First day"
        fields["flexible_ends_on"].label = "Last day"
        note = fields["flexible_note"]
        note.label = "Note for students"
        # The model field's help text is developer wording; the page carries the sentence that says
        # what Flexible means, so the note is the instructor's extra.
        note.help_text = (
            "Optional. Hours you teach, what to bring to the first meeting, "
            "anything students should know before they book."
        )
        setup_flexible_booking_text(self)  # type: ignore[arg-type]

    def clean_flexible_booking_text(self) -> str:
        return clean_flexible_booking_text(self)  # type: ignore[arg-type]

    def clean_flexible_window(self) -> None:
        data = self.cleaned_data  # type: ignore[attr-defined]
        # ``.get``: a field that failed its own validation is absent from cleaned_data.
        if data.get("scheduling_model") == ClassOffering.SchedulingModel.FIXED:
            data["flexible_starts_on"] = None
            data["flexible_ends_on"] = None
            return
        starts_on, ends_on = data.get("flexible_starts_on"), data.get("flexible_ends_on")
        if starts_on is not None and ends_on is not None and ends_on < starts_on:
            self.add_error("flexible_ends_on", FLEXIBLE_WINDOW_ORDER_MESSAGE)  # type: ignore[attr-defined]


REGISTRATION_CUTOFF_DEFAULT_HOURS = 48
REGISTRATION_CUTOFF_MIN_HOURS = 1
REGISTRATION_CUTOFF_MAX_HOURS = 720
REGISTRATION_CUTOFF_REQUIRED_MESSAGE = "Enter how many hours before the class registration should close."


class _RegistrationCutoffMixin:
    """The registration cutoff on both composer forms: a toggle and an hours box.

    ``registration_cutoff_enabled`` is a form-only switch bound to the composer's Alpine state so
    the hours box shows only while it is on; the row stores just ``registration_cutoff_hours``,
    where null is "off". ``clean_registration_cutoff`` writes that null when the switch is off
    and refuses an empty box when it is on; the bounds live on the field as attributes so the
    browser refuses them first.
    """

    def setup_registration_cutoff_fields(self) -> None:
        """Add the switch and shape the hours box; the form's metaclass never collects a mixin's fields."""
        fields = self.fields  # type: ignore[attr-defined]
        instance: ClassOffering = self.instance  # type: ignore[attr-defined]
        saved_hours = instance.registration_cutoff_hours
        fields["registration_cutoff_enabled"] = forms.BooleanField(
            required=False,
            initial=saved_hours is not None,
            label="Close registration before the class starts",
            help_text=(
                "Students cannot register once this many hours remain before the first session. "
                "Turn it off to take sign-ups right up to the start."
            ),
            widget=forms.CheckboxInput(attrs={"x-model": "registrationCutoff"}),
        )
        hours = fields["registration_cutoff_hours"]
        hours.label = "Hours before the first session"
        hours.help_text = ""
        hours.required = False
        hours.initial = saved_hours if saved_hours is not None else REGISTRATION_CUTOFF_DEFAULT_HOURS
        hours.validators = [
            MinValueValidator(REGISTRATION_CUTOFF_MIN_HOURS),
            MaxValueValidator(REGISTRATION_CUTOFF_MAX_HOURS),
        ]
        hours.widget.attrs.update({"min": REGISTRATION_CUTOFF_MIN_HOURS, "max": REGISTRATION_CUTOFF_MAX_HOURS})

    def clean_registration_cutoff(self) -> None:
        data = self.cleaned_data  # type: ignore[attr-defined]
        # A flexible class has no start to count from, so it stores no cutoff, whatever the
        # hidden block posted (the mirror of ``clean_flexible_window``).
        flexible = data.get("scheduling_model") == ClassOffering.SchedulingModel.FLEXIBLE
        if flexible or not data.get("registration_cutoff_enabled"):
            data["registration_cutoff_hours"] = None
            return
        # ``in``: a box that failed its own bounds is absent from cleaned_data and already carries its error.
        if "registration_cutoff_hours" in data and data["registration_cutoff_hours"] is None:
            self.add_error("registration_cutoff_hours", REGISTRATION_CUTOFF_REQUIRED_MESSAGE)  # type: ignore[attr-defined]


class _RichDescriptionMixin:
    """The description is written in the rich-text editor and stored as its sanitized HTML.

    A description saved before the editor existed is plain text, and a client without the editor
    still posts plain text; :func:`core.html_sanitize.clean_rich_body` keeps that as typed and
    sanitizes only editor HTML, so the stored value is always one the page can render.
    """

    cleaned_data: dict[str, Any]

    def clean_description(self) -> str:
        return clean_rich_body(self.cleaned_data["description"])


_SUBCATEGORY_MISMATCH = "Pick a subcategory from the chosen Eventbrite category."


class _EventbriteSubcategorySelect(forms.Select):
    """The subcategory dropdown: each option is offered only under its parent category (#716).

    Every option carries Alpine bindings on ``ebCategory`` (set by the category dropdown, see
    :class:`_EventbriteCategoryMixin`), so the list narrows in the browser without a request.
    The blank option stays offered. The form refuses a mismatched pair whatever the browser sent.
    """

    def create_option(
        self,
        name: str,
        value: Any,
        label: Any,
        selected: bool,
        index: int,
        subindex: int | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        if value:
            elsewhere = f"ebCategory !== '{SUBCATEGORY_PARENT[str(value)]}'"
            option["attrs"].update({":disabled": elsewhere, ":hidden": elsewhere})
        return option


class _EventbriteCategoryMixin:
    """Eventbrite's category and subcategory (#716), on the composer and the published edit page.

    Both render through ``classes/_components/eventbrite_category_fields.html``, whose ``x-data``
    holds ``ebCategory``. Picking a category clears the subcategory, because no subcategory has two
    parents. A form whose Eventbrite opt in is gone drops these two with it.
    """

    fields: dict[str, forms.Field]
    cleaned_data: dict[str, Any]

    def setup_eventbrite_category_fields(self) -> None:
        self.fields["eventbrite_category"].widget.attrs.update(
            {"x-init": "ebCategory = $el.value", "@change": "ebCategory = $el.value; $refs.ebSubcategory.value = ''"}
        )
        self.fields["eventbrite_subcategory"].widget = _EventbriteSubcategorySelect(
            attrs={"x-ref": "ebSubcategory"}, choices=[*BLANK_CHOICE_DASH, *EventbriteSubcategory.choices]
        )

    def drop_eventbrite_category_fields(self) -> None:
        del self.fields["eventbrite_category"], self.fields["eventbrite_subcategory"]

    def check_eventbrite_category_pair(self) -> None:
        """Refuse a subcategory that is not a child of the chosen category (or comes with none)."""
        if "eventbrite_category" not in self.fields:
            return
        category = self.cleaned_data.get("eventbrite_category", "")
        subcategory = self.cleaned_data.get("eventbrite_subcategory", "")
        if not subcategory_fits(category, subcategory):
            self.add_error("eventbrite_subcategory", _SUBCATEGORY_MISMATCH)  # type: ignore[attr-defined]


_SUBMIT_LABEL = "Submit to Eventbrite"
_AGREEMENT_HELP = (
    "Ticking this is my agreement to keep the listing within Eventbrite's rules below. "
    "The class lists on Eventbrite when it goes live, with seats in step with this site."
)
# Ticked, the partial (classes/_components/eventbrite_rules.html) reveals the fee and category.
_SUBMIT_ALPINE = {"x-model": "ebOn"}


class _EventbriteRulesMixin:
    """Eventbrite's selling rules (#725): Submit to Eventbrite, the agreement it records, and the check.

    Ticking Submit to Eventbrite is the agreement (its hint says so); a save with it ticked records
    who and when, once per class. The page checks the text as it is typed (``EventbriteListingCheckForm``). The listing check runs after validation, through
    :meth:`accepts_eventbrite_listing`, because it reads the posted FAQ rows, which live on their
    own formset. ``eventbrite_check`` holds the last check for the template: the saved class's on
    a fresh page, the posted text's after a save attempt.
    """

    fields: dict[str, forms.Field]
    instance: ClassOffering
    cleaned_data: dict[str, Any]
    is_bound: bool
    eventbrite_rules_text = EVENTBRITE_RULES
    eventbrite_check: ListingCheck | None = None

    def setup_eventbrite_rules(self) -> None:
        """Name the opt in Submit to Eventbrite, say it is the agreement, and check a saved opted-in class."""
        switch = self.fields["eventbrite_enabled"]
        switch.label, switch.help_text = _SUBMIT_LABEL, _AGREEMENT_HELP
        switch.widget.attrs.update(_SUBMIT_ALPINE)
        if not self.is_bound and self.instance.pk and self.instance.eventbrite_enabled:
            self.eventbrite_check = self.instance.eventbrite_listing_check()

    def accepts_eventbrite_listing(self, agreed_by: User, faq_formset: BaseClassFaqFormSet | None = None) -> bool:
        """After ``is_valid``: check a class with Eventbrite on against its rules, and record the agreement.

        Refuses with every problem on the switch, so the save does not happen; otherwise, the
        first time the class is saved with the switch on, records who agreed, for the form's
        save to write.

        Args:
            agreed_by: The user saving, recorded as agreeing when nobody has yet.
            faq_formset: The posted FAQ rows; omitted on a new class, which has none yet.

        Returns:
            True when the save may go ahead.
        """
        if "eventbrite_enabled" not in self.fields or not self.cleaned_data["eventbrite_enabled"]:
            return True
        faqs = faq_formset.posted_faqs() if faq_formset is not None else []
        self.eventbrite_check = check = self.instance.eventbrite_listing_check(faqs)
        if check.problems:
            self.add_error("eventbrite_enabled", ValidationError(check.refusal_lines))  # type: ignore[attr-defined]
            return False
        if self.instance.needs_eventbrite_agreement:
            self.instance.agree_to_eventbrite_rules(agreed_by)
        return True


class EventbriteListingCheckForm(forms.Form):
    """What the Eventbrite section posts as the class is typed (#725): the class's text, never saved.

    The section sends the whole page form, so a field the page does not carry (the live class
    edit page has no title box) is read from the saved class instead. The FAQ comes from the
    posted formset when the page has one, else from the saved rows; a new class has none.
    """

    title = forms.CharField(required=False)
    subtitle = forms.CharField(required=False)
    description = forms.CharField(required=False)

    def __init__(self, data: Mapping[str, Any], *, offering: ClassOffering | None) -> None:
        super().__init__(data)
        self.offering = offering

    def listing_check(self) -> ListingCheck:
        """The listing check over the posted text, falling back field by field to the saved class."""
        self.is_valid()  # every field is optional, so cleaning always succeeds
        offering = self.offering if self.offering is not None else ClassOffering()
        for name in ("title", "subtitle", "description"):
            if name in self.data:
                setattr(offering, name, self.cleaned_data[name])
        return offering.eventbrite_listing_check(self._faqs(offering))

    def _faqs(self, offering: ClassOffering) -> list[dict[str, str]]:
        if not offering.pk:
            return []
        if "faq-TOTAL_FORMS" not in self.data:
            return offering.own_faqs()
        formset = build_class_faq_formset(self.data, offering)
        formset.is_valid()  # cleans every row; a row the save would refuse is still checked
        return formset.posted_faqs()


class _EventbriteMixin(_EventbriteRulesMixin, _EventbriteCategoryMixin):
    """The Eventbrite fields (#652): the opt-in, who pays Eventbrite's fee, and the category (#716).

    The fee's help text works the example at the class's own price (a new class shows $50). A
    flexible class has no dates to list, so the opt-in is cleared whatever was posted. While the
    integration is off (site toggle or credentials), both fields leave the form, so nobody ticks
    a box that does nothing and a post cannot set them.
    """

    fields: dict[str, forms.Field]
    instance: ClassOffering
    cleaned_data: dict[str, Any]

    def setup_eventbrite_fields(self) -> None:
        from classes.templatetags.classes_tags import cents_as_price
        from core.integrations.eventbrite import EventbriteClient, estimate_fee_cents

        if not EventbriteClient.from_settings().enabled:
            del self.fields["eventbrite_enabled"], self.fields["eventbrite_fee_payer"]
            self.drop_eventbrite_category_fields()
            return
        self.setup_eventbrite_category_fields()
        self.setup_eventbrite_rules()
        price = self.instance.price_cents or 5000
        fee = estimate_fee_cents(price)
        # Optional so a post without it (an older client, the opt-in left off) keeps the default.
        self.fields["eventbrite_fee_payer"].required = False
        self.fields["eventbrite_fee_payer"].help_text = (
            "Eventbrite charges 3.7% + $1.79 per ticket, plus 2.9% payment processing. "
            f"On a {cents_as_price(price)} ticket that is about {cents_as_price(fee)}. "
            f"Buyer pays it on top: the buyer pays about {cents_as_price(price + fee)} and the class gets "
            f"{cents_as_price(price)}. Included in my price: the buyer pays {cents_as_price(price)} and the "
            f"class gets about {cents_as_price(price - fee)}."
        )

    def clean_eventbrite(self) -> None:
        if "eventbrite_enabled" not in self.fields:
            return
        self.check_eventbrite_category_pair()
        if not self.cleaned_data.get("eventbrite_fee_payer"):
            self.cleaned_data["eventbrite_fee_payer"] = ClassOffering.EventbriteFeePayer.BUYER
        if self.cleaned_data.get("scheduling_model") == ClassOffering.SchedulingModel.FLEXIBLE:
            self.cleaned_data["eventbrite_enabled"] = False


class ClassOfferingForm(
    _EventbriteMixin,
    _RichDescriptionMixin,
    _HeroCropMixin,
    _CardFocusMixin,
    _PricingRulesMixin,
    _LiveSaleGuardMixin,
    _SchedulingTypeMixin,
    _FlexibleWindowMixin,
    _RegistrationCutoffMixin,
    forms.ModelForm,
):
    """The admin composer form. The six ``sale_*`` fields live on :class:`ClassSaleForm`."""

    price_cents = CentsAsDollarsField(label="Price", help_text=PRICE_HELP_TEXT)

    class Meta:
        model = ClassOffering
        fields = [
            "title",
            "subtitle",
            "category",
            "instructor",
            "description",
            "prerequisites",
            "materials_included",
            "materials_to_bring",
            "safety_requirements",
            "age_minimum",
            "age_guardian_note",
            "price_cents",
            "capacity",
            "scheduling_model",
            "scheduling_type",
            "flexible_note",
            "flexible_booking_text",
            "flexible_starts_on",
            "flexible_ends_on",
            "registration_cutoff_hours",
            "eventbrite_enabled",
            "eventbrite_fee_payer",
            "eventbrite_category",
            "eventbrite_subcategory",
            "area",
            "is_private",
            "private_for_name",
            "image",
            "video_url",
        ]
        # Four rows, not the widget default of ten: the readiness minimum is 40 characters, so the
        # box only has to invite a short paragraph, and the live count sits right under it.
        widgets = {
            "video_url": _video_url_widget(),
            "description": RichBodyEditorWidget(attrs={"rows": 4}),
            "flexible_starts_on": _window_day_widget(),
            "flexible_ends_on": _window_day_widget(),
            "flexible_booking_text": _booking_text_widget(),
        }
        # The window's one hint sits under the pair on step 3, so neither day repeats it.
        help_texts = {
            "subtitle": SUBTITLE_HELP_TEXT,
            "description": DESCRIPTION_HELP_TEXT,
            "flexible_starts_on": "",
            "flexible_ends_on": "",
        }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["category"].label = "Class Type"
        self.add_hero_crop_field()
        self.add_card_focus_field()
        self.setup_scheduling_type_field()
        self.setup_flexible_window_fields()
        self.setup_registration_cutoff_fields()
        self.setup_eventbrite_fields()
        setup_location_field(self, hint=CLASS_LOCATION_HINT)

    def clean_video_url(self) -> str:
        return validate_video_url(self.cleaned_data.get("video_url", ""))

    def clean(self) -> dict:
        data = super().clean() or {}
        self.clean_price_against_live_sale()
        self.clean_flexible_window()
        self.clean_registration_cutoff()
        self.clean_eventbrite()
        return data

    def save(self, commit: bool = True) -> ClassOffering:
        offering = super().save(commit=False)
        self.apply_hero_crop_to_instance(offering)
        self.apply_card_focus_to_instance(offering)
        _assign_provisional_slug(offering)
        if commit:
            offering.save()
            self.save_m2m()
        return offering


class TeachClassOfferingForm(
    _EventbriteMixin,
    _RichDescriptionMixin,
    _HeroCropMixin,
    _CardFocusMixin,
    _PricingRulesMixin,
    _LiveSaleGuardMixin,
    _SchedulingTypeMixin,
    _FlexibleWindowMixin,
    _RegistrationCutoffMixin,
    forms.ModelForm,
):
    """Class form for teaching members — no `instructor`, no `is_private`, slug auto-generated.

    The six ``sale_*`` fields live on :class:`ClassSaleForm` (the Manage Class sale modal).
    """

    price_cents = CentsAsDollarsField(label="Price", help_text=PRICE_HELP_TEXT)

    class Meta:
        model = ClassOffering
        fields = [
            "title",
            "subtitle",
            "category",
            "description",
            "prerequisites",
            "materials_included",
            "materials_to_bring",
            "safety_requirements",
            "age_minimum",
            "age_guardian_note",
            "price_cents",
            "capacity",
            "scheduling_model",
            "scheduling_type",
            "flexible_note",
            "flexible_booking_text",
            "flexible_starts_on",
            "flexible_ends_on",
            "registration_cutoff_hours",
            "eventbrite_enabled",
            "eventbrite_fee_payer",
            "eventbrite_category",
            "eventbrite_subcategory",
            "area",
            "image",
            "video_url",
        ]
        # Four rows, not the widget default of ten: the readiness minimum is 40 characters, so the
        # box only has to invite a short paragraph, and the live count sits right under it.
        widgets = {
            "video_url": _video_url_widget(),
            "description": RichBodyEditorWidget(attrs={"rows": 4}),
            "flexible_starts_on": _window_day_widget(),
            "flexible_ends_on": _window_day_widget(),
            "flexible_booking_text": _booking_text_widget(),
        }
        # The window's one hint sits under the pair on step 3, so neither day repeats it.
        help_texts = {
            "subtitle": SUBTITLE_HELP_TEXT,
            "description": DESCRIPTION_HELP_TEXT,
            "flexible_starts_on": "",
            "flexible_ends_on": "",
        }

    def __init__(self, *args, teaching_member: "Member | None" = None, **kwargs) -> None:
        self.teaching_member = teaching_member
        super().__init__(*args, **kwargs)
        # What save() will set, set now, so the booking line box on a new class names the
        # instructor and not "your instructor". The instructor is not one of this form's fields,
        # so nothing posted can overwrite it.
        if teaching_member is not None and not self.instance.instructor_id:
            self.instance.instructor = teaching_member
        self.fields["category"].label = "Class Type"
        self.add_hero_crop_field()
        self.add_card_focus_field()
        self.setup_scheduling_type_field()
        self.setup_flexible_window_fields()
        self.setup_registration_cutoff_fields()
        self.setup_eventbrite_fields()
        setup_location_field(self, hint=CLASS_LOCATION_HINT)

    def clean_video_url(self) -> str:
        return validate_video_url(self.cleaned_data.get("video_url", ""))

    def clean(self) -> dict:
        data = super().clean() or {}
        self.clean_price_against_live_sale()
        self.clean_flexible_window()
        self.clean_registration_cutoff()
        self.clean_eventbrite()
        return data

    def save(self, commit: bool = True) -> ClassOffering:
        offering = super().save(commit=False)
        self.apply_hero_crop_to_instance(offering)
        self.apply_card_focus_to_instance(offering)
        if self.teaching_member is not None and not offering.instructor_id:
            offering.instructor = self.teaching_member
            if not offering.created_by_id:
                offering.created_by = self.teaching_member
        _assign_provisional_slug(offering)
        if commit:
            offering.save()
        return offering


class ClassSaleForm(_SaleMixin, forms.ModelForm):
    """The Put This Class On Sale modal on the Manage Class page (teach and admin).

    Carries the five sale amount fields alone. ``sale_enabled`` is not a field: the modal's
    buttons carry it. Turning a sale on or saving its changes validates through
    :meth:`_SaleMixin.clean_sale_fields` unchanged (the Stripe floor and the no price check
    are the reason that validation exists), fed the price from the saved class since the
    modal has no price field of its own. Turning a sale off never goes
    through this form: :meth:`ClassOffering.turn_sale_off` skips validation on purpose.
    """

    sale_amount_cents = CentsAsDollarsField(
        required=False, label="Amount off ($)", help_text="Flat dollars off, e.g. 15.00 for $15 off."
    )

    class Meta:
        model = ClassOffering
        fields = ["sale_kind", "sale_percent", "sale_amount_cents", "sale_banner_text", "sale_allow_discount_codes"]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["sale_kind"].label = "How Much Off?"
        self.fields["sale_percent"].label = "Percent off"
        self.fields["sale_percent"].help_text = ""
        self.fields["sale_banner_text"].label = "Banner text"
        self.fields["sale_banner_text"].help_text = "Leave it blank to use the standard sale banner."
        self.fields["sale_allow_discount_codes"].label = "Allow discount codes on top"
        self.fields[
            "sale_allow_discount_codes"
        ].help_text = "Off by default, so a sale price cannot be stacked with another offer."

    def add_error(self, field: str | None, error: Any) -> None:
        """Route the mixin's ``price_cents`` errors to the form level: this form has no price field."""
        super().add_error(None if field == "price_cents" else field, error)

    def clean(self) -> dict:
        data = super().clean() or {}
        # The mixin reads the switch and the price from cleaned_data; the modal has neither
        # field, so they come from the class being edited.
        data["sale_enabled"] = True
        data["price_cents"] = self.instance.price_cents
        self.clean_sale_fields()
        return data

    def save(self, commit: bool = True) -> ClassOffering:
        offering = super().save(commit=False)
        offering.sale_enabled = True
        if commit:
            offering.save()
        return offering


def _teaching_contact_method_choices() -> list[tuple[str, str]]:
    """The Best way to reach you options, behind a blank so a missed pick is a field error.

    A callable so the form module never imports ``membership.models`` at load time
    (the two apps import each other).
    """
    from membership.models import Member

    return [("", "Pick one"), *Member.TeachingContactMethod.choices]


def _teaching_experience_choices() -> list[tuple[str, str]]:
    """The Teaching experience options, behind a blank so a missed pick is a field error."""
    from membership.models import Member

    return [("", "Pick one"), *Member.TeachingExperience.choices]


class TeachingApplicationForm(forms.Form):
    """The I'm Interested modal: the note, how to reach the member, and where.

    Validation lives here, not the view: the note is what an admin reads when they
    decide, so a blank submit gets the field error rather than filing an empty ask.
    ``strip`` is Django's default, so a note of only whitespace fails ``required``.
    The detail is checked against the method in ``clean``: Email must be an address;
    Text message and Phone call need a phone number with at least seven digits,
    however it is punctuated. What the member typed is stored as typed.

    Website, socials and experience (#690) are what the Instructor Inquiries page
    shows; experience is required and the other two are optional.

    The widget attributes on the two contact fields are what the modal's Alpine
    component hooks: the select reports a change and the detail input takes the
    prefill (``templates/classes/teach/partials/apply_form.html``).
    """

    note = forms.CharField(
        required=True,
        max_length=2000,
        label="What Would You Like to Host?",
        help_text=(
            "A sentence or two is plenty. Tell us the subject, roughly how long it would run, "
            "and anything you have taught before."
        ),
        widget=forms.Textarea(attrs={"rows": 5}),
        error_messages={
            "required": "Tell us a little about what you want to host.",
            "max_length": "That is longer than we can store. Trim it to 2000 characters or fewer.",
        },
    )
    contact_method = forms.ChoiceField(
        required=True,
        choices=_teaching_contact_method_choices,
        label="Best way to reach you",
        widget=forms.Select(attrs={"x-ref": "method", "@change": "pick($event.target.value)"}),
        error_messages={
            "required": "Pick how you would like us to reach you.",
            "invalid_choice": "Pick how you would like us to reach you.",
        },
    )
    contact_detail = forms.CharField(
        required=True,
        max_length=254,
        label="Where to reach you",
        widget=forms.TextInput(attrs={"x-ref": "detail"}),
        error_messages={
            "required": "Tell us where to reach you: an email address or a phone number.",
            "max_length": "That is longer than we can store. Keep it to 254 characters or fewer.",
        },
    )
    experience = forms.ChoiceField(
        required=True,
        choices=_teaching_experience_choices,
        label="Teaching experience",
        error_messages={
            "required": "Pick how much teaching you have done.",
            "invalid_choice": "Pick how much teaching you have done.",
        },
    )
    website = forms.URLField(
        required=False,
        max_length=200,
        label="Website",
        help_text="Optional. Your own site or a portfolio.",
        error_messages={
            "invalid": "That does not look like a web address. Check it and try again.",
            "max_length": "That is longer than we can store. Keep it to 200 characters or fewer.",
        },
    )
    socials = forms.CharField(
        required=False,
        max_length=500,
        label="Socials",
        help_text="Optional. One or more handles or links, such as @yourname on Instagram.",
        error_messages={
            "max_length": "That is longer than we can store. Keep it to 500 characters or fewer.",
        },
    )

    def clean(self) -> dict:
        data = super().clean() or {}
        method = data.get("contact_method")
        detail = data.get("contact_detail")
        if not method or not detail:
            return data  # the field errors already say which one is missing
        from membership.models import Member

        if method == Member.TeachingContactMethod.EMAIL:
            try:
                validate_email(detail)
            except ValidationError:
                self.add_error("contact_detail", "That does not look like an email address. Check it and try again.")
        elif sum(ch.isdigit() for ch in detail) < 7:
            self.add_error("contact_detail", "That does not look like a phone number. It needs at least seven digits.")
        return data


_INQUIRY_DATE_ATTRS = {"type": "date", "@click": "(() => { try { $el.showPicker() } catch (e) {} })()"}


class InstructorInquiryFilterForm(forms.Form):
    """The Instructor Inquiries filter bar: an applied date range and a status (#690).

    Read from GET, so the same query string drives the page and its CSV export. Every
    field is optional; a To date before the From date is a field error rather than an
    empty list, so the admin sees why nothing matched.
    """

    STATUS_CHOICES = [("", "All"), ("pending", "Pending"), ("approved", "Approved"), ("declined", "Declined")]

    date_from = forms.DateField(required=False, label="Date from", widget=forms.DateInput(attrs=_INQUIRY_DATE_ATTRS))
    date_to = forms.DateField(required=False, label="Date to", widget=forms.DateInput(attrs=_INQUIRY_DATE_ATTRS))
    status = forms.ChoiceField(required=False, choices=STATUS_CHOICES, label="Status")

    def clean(self) -> dict:
        data = super().clean() or {}
        date_from = data.get("date_from")
        date_to = data.get("date_to")
        if date_from and date_to and date_to < date_from:
            self.add_error("date_to", "Date to is before Date from. Pick a later date.")
        return data

    def in_range(self) -> MemberQuerySet:
        """Every inquiry in a valid filter's date range, whatever its status."""
        from membership.models import Member

        data = self.cleaned_data
        return Member.objects.teaching_inquiries(applied_from=data["date_from"], applied_to=data["date_to"])

    def inquiries(self) -> MemberQuerySet:
        """The members who asked to teach that match a valid filter, newest ask first."""
        from membership.models import Member

        inquiries = self.in_range()
        if self.cleaned_data["status"]:
            inquiries = inquiries.in_teaching_state(Member.TeachingApplicationState(self.cleaned_data["status"]))
        return inquiries

    def board_report(self, now: datetime) -> BoardReport:
        """The Board Report charts for a valid filter's date range; the status filter does not apply."""
        from classes.inquiries import board_report

        data = self.cleaned_data
        return board_report(self.in_range(), applied_from=data["date_from"], applied_to=data["date_to"], now=now)


class ClassSessionForm(forms.ModelForm):
    class Meta:
        model = ClassSession
        fields = ["starts_at", "ends_at"]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def clean(self) -> dict:
        data = super().clean() or {}
        starts_at = data.get("starts_at")
        ends_at = data.get("ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            raise forms.ValidationError("Session end time must be after start time.")
        return data


ClassSessionFormSet = inlineformset_factory(
    ClassOffering,
    ClassSession,
    form=ClassSessionForm,
    extra=1,
    can_delete=True,
)


class ClassImageForm(forms.ModelForm):
    class Meta:
        model = ClassImage
        fields = ["image", "alt_text", "sort_order"]
        widgets = {
            "alt_text": forms.TextInput(attrs={"placeholder": "Short description (optional)"}),
            "sort_order": forms.NumberInput(attrs={"min": 0, "step": 1, "style": "width:5rem"}),
        }


ClassImageFormSet = inlineformset_factory(
    ClassOffering,
    ClassImage,
    form=ClassImageForm,
    extra=3,
    can_delete=True,
)


LOCKED_FAQ_ERROR = "Past Lives already shows this question on every class. Delete this row or ask something else."


class ClassFaqForm(forms.ModelForm):
    """A single FAQ question/answer row on the class edit form."""

    class Meta:
        model = ClassFaq
        fields = ["question", "answer"]
        widgets = {
            "answer": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_question(self) -> str:
        """Refuse a row asking a locked question, so its copy changes only in code (admins too)."""
        question: str = self.cleaned_data["question"]
        if is_locked_class_faq(question):
            raise forms.ValidationError(LOCKED_FAQ_ERROR)
        return question


class BaseClassFaqFormSet(BaseInlineFormSet):
    """The class FAQ formset: the class's own rows, never a locked one.

    A row asking a locked question is left out of the queryset, so it never renders as
    editable, and a successful save deletes any such row the class still holds (one saved
    by the previous release while a deploy was going out, say).

    A tab opened before the deploy can post a saved row this formset no longer holds: its
    id was deleted by migration 0086, or it is a locked row left out of the queryset.
    Django would build that row as an unsaved instance, fail its hidden id with an error
    nobody sees, and drop it from the save. Here such a "stale" row asking a locked question
    is dropped quietly (the locked copy shows on the page anyway), and any other stale row
    is saved as a new row, so a stale tab still saves what was typed in it.
    """

    def add_fields(self, form: forms.ModelForm, index: int | None) -> None:
        super().add_fields(form, index)
        if self._is_stale(form, index):
            # The posted id names no row this formset can save; carry it as plain text so
            # it cannot fail as an invalid choice under a hidden input.
            form.fields["id"] = forms.CharField(required=False, widget=forms.HiddenInput)

    def _is_stale(self, form: forms.ModelForm, index: int | None) -> bool:
        """A bound row posted as saved whose id this formset could not load."""
        return form.is_bound and index is not None and index < self.initial_form_count() and form.instance._state.adding

    def _stale_forms(self) -> list[forms.ModelForm]:
        return [form for i, form in enumerate(self.forms) if self._is_stale(form, i)]

    def _should_delete_form(self, form: forms.ModelForm) -> bool:
        """A row with Delete ticked (Django's own rule), or a stale row asking a locked question."""
        if form.cleaned_data.get(DELETION_FIELD_NAME, False):
            return True
        return form in self._stale_forms() and is_locked_class_faq(form["question"].value() or "")

    def save(self, commit: bool = True) -> list[ClassFaq]:
        """Save the rows, keep a stale row's text as a new row, and clear any locked row left behind."""
        saved: list[ClassFaq] = super().save(commit=commit)
        deleted = self.deleted_forms
        saved += [
            self.save_new(form, commit=commit)
            for form in self._stale_forms()
            if form not in deleted and form.has_changed()
        ]
        locked = [faq.pk for faq in self.instance.faqs.all() if is_locked_class_faq(faq.question)]
        ClassFaq.objects.filter(pk__in=locked).delete()
        return saved

    def posted_faqs(self) -> list[dict[str, str]]:
        """After ``is_valid``: the rows the save keeps, as ``question`` and ``answer``, for the Eventbrite check (#725).

        A row with a question or answer that failed cleaning is left out, valid formset or not.
        """
        return [
            {"question": form.cleaned_data["question"], "answer": form.cleaned_data["answer"]}
            for form in self.forms
            if {"question", "answer"} <= form.cleaned_data.keys() and not self._should_delete_form(form)
        ]

    @property
    def locked_faqs(self) -> list[dict]:
        """The ``LOCKED_CLASS_FAQS``, for the template to render without inputs."""
        return LOCKED_CLASS_FAQS


def build_class_faq_formset(data: Any, offering: ClassOffering) -> Any:
    """FAQ formset for the class edit form.

    The formset holds the class's own rows only (a row asking a locked question is left
    out). When the class has none yet, the unbound (GET) formset renders the site-wide
    ``DEFAULT_CLASS_FAQS`` as prefilled extra rows: the instructor's editable starting
    point. Saving materializes whatever rows come back as the class's own list (bound
    extra forms carry data against empty initial, so untouched defaults still save). The
    ``LOCKED_CLASS_FAQS`` are never rows; the editor shows them read only (``locked_faqs``)
    and a new or edited row asking one fails validation.
    """
    locked = [faq.pk for faq in offering.faqs.all() if is_locked_class_faq(faq.question)]
    own = offering.faqs.exclude(pk__in=locked)
    seed = data is None and not own.exists()
    formset_cls = inlineformset_factory(
        ClassOffering,
        ClassFaq,
        form=ClassFaqForm,
        formset=BaseClassFaqFormSet,
        extra=len(DEFAULT_CLASS_FAQS) if seed else 0,
        can_delete=True,
    )
    return formset_cls(
        data,
        instance=offering,
        prefix="faq",
        queryset=own,
        initial=[dict(faq) for faq in DEFAULT_CLASS_FAQS] if seed else None,
    )


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "slug", "sort_order", "hero_image"]


class TeachPublishedClassForm(
    _EventbriteRulesMixin, _EventbriteCategoryMixin, _RichDescriptionMixin, _HeroCropMixin, forms.ModelForm
):
    """Light edits an instructor may make to a LIVE class without re-review.

    Only fields that do not change what registrants booked on: the subtitle (#563), description,
    prep notes, materials, safety, guardian note, the flexible-scheduling note, the video, and the
    banner photo's crop box (the photo itself saves instantly through the hero upload route).
    The subtitle leads ``Meta.fields`` because the template renders them in this order, and it
    belongs above the description. Title, class type, price, capacity, dates, and scheduling
    model stay admin-only after publish (the instructor asks through
    :class:`ClassChangeRequestForm`). A sale is not one of
    those: the instructor sets, changes, or ends one on a live class from the manage page's
    sale modal (:class:`ClassSaleForm`). A crafted POST carrying locked fields is simply
    ignored: a ModelForm saves only its declared fields.
    """

    class Meta:
        model = ClassOffering
        fields = [
            "subtitle",
            "description",
            "prerequisites",
            "materials_included",
            "materials_to_bring",
            "safety_requirements",
            "age_guardian_note",
            "flexible_booking_text",
            "flexible_note",
            "video_url",
            "eventbrite_enabled",
            "eventbrite_category",
            "eventbrite_subcategory",
        ]
        widgets = {
            "video_url": _video_url_widget(),
            "description": RichBodyEditorWidget(attrs={"rows": 4}),
            "flexible_booking_text": _booking_text_widget(),
        }
        help_texts = {"subtitle": SUBTITLE_HELP_TEXT}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # The booking line exists only on a flexible class's page, so a fixed class does not
        # get the box (the page renders every field of this form).
        # Eventbrite is the reverse: a flexible class has no dates to list, so it gets no switch.
        if self.instance.is_flexible:
            setup_flexible_booking_text(self)
        else:
            del self.fields["flexible_booking_text"]
        from core.integrations.eventbrite import EventbriteClient

        if self.instance.is_flexible or not EventbriteClient.from_settings().enabled:
            del self.fields["eventbrite_enabled"]
            self.drop_eventbrite_category_fields()
        else:
            self.setup_eventbrite_category_fields()
            self.setup_eventbrite_rules()
        self.add_hero_crop_field()

    def clean(self) -> dict:
        data = super().clean() or {}
        self.check_eventbrite_category_pair()
        return data

    def clean_flexible_booking_text(self) -> str:
        return clean_flexible_booking_text(self)

    def clean_video_url(self) -> str:
        return validate_video_url(self.cleaned_data.get("video_url", ""))

    def save(self, commit: bool = True) -> ClassOffering:
        offering = super().save(commit=False)
        self.apply_hero_crop_to_instance(offering)
        if commit:
            offering.save()
            self.save_m2m()
        return offering


class ClassChangeRequestForm(forms.Form):
    """The Request a change modal on a live class: one note for the admins."""

    note = forms.CharField(
        max_length=1000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Move the price to $85 and add one more seat."}),
        label="What needs to change?",
        help_text="An admin makes the change and can reach you with questions.",
    )

    def clean_note(self) -> str:
        note = " ".join((self.cleaned_data.get("note") or "").split())
        if not note:
            raise ValidationError("Say what needs to change.")
        return note


class ClassCancelForm(forms.Form):
    """The Cancel class modal: one required reason, emailed to everyone registered."""

    reason = forms.CharField(
        max_length=300,
        required=False,
        widget=forms.Textarea(
            attrs={"rows": 3, "placeholder": "The instructor is unwell and we could not find a date."}
        ),
        label="Reason",
        help_text="Your reason is emailed to everyone registered.",
    )

    def clean_reason(self) -> str:
        reason = (self.cleaned_data.get("reason") or "").strip()
        if not reason:
            raise ValidationError("Please tell people why.")
        return reason


class ClassReviewDecisionForm(forms.Form):
    """Reviewer's decision form on the tokenized review page.

    Notes are required when the decision is changes_requested or denied so
    the instructor gets actionable feedback. They're optional on approve.
    """

    decision = forms.ChoiceField(
        choices=[
            ("approved", "Approve"),
            ("changes_requested", "Ask for changes"),
            ("denied", "Decline"),
        ],
        widget=forms.RadioSelect,
        label="Decision",
    )
    notes = forms.CharField(
        widget=forms.Textarea(
            attrs={"rows": 4, "placeholder": "Optional when you approve. Required when you ask for changes or decline."}
        ),
        required=False,
        label="Notes for the instructor",
        help_text="Optional when you approve. Required when you ask for changes or decline, "
        "so the instructor knows what to work on.",
    )

    def clean(self) -> dict:
        data = super().clean() or {}
        decision = data.get("decision")
        notes = (data.get("notes") or "").strip()
        if decision in ("changes_requested", "denied") and not notes:
            self.add_error("notes", "Please leave a note so the instructor knows what to change.")
        return data


class DiscountCodeForm(forms.ModelForm):
    discount_fixed_cents = CentsAsDollarsField(
        required=False,
        label="Fixed discount ($)",
        help_text="Flat dollar amount off, e.g. 20.00 for $20 off.",
    )

    class Meta:
        model = DiscountCode
        fields = [
            "code",
            "description",
            "discount_pct",
            "discount_fixed_cents",
            "valid_from",
            "valid_until",
            "max_uses",
            "is_active",
        ]
        help_texts = {
            "max_uses": "Leaving the 'uses' field blank indicates unlimited uses.",
        }
        widgets = {
            "code": forms.TextInput(
                attrs={
                    "style": "text-transform:uppercase;",
                    "oninput": "this.value = this.value.toUpperCase()",
                }
            ),
        }

    def __init__(self, *args, scoped_to: ClassOffering | None = None, created_by=None, **kwargs) -> None:
        """Optionally bind this code to a single class and an audit user.

        Passing ``scoped_to`` makes a class-scoped code: registrations for any
        other class won't honor it. Passing ``created_by`` records who made it
        (and lets that member manage — and, with the self-approve permission,
        approve — their own codes). Every new code starts unapproved regardless
        of who creates it.
        """
        super().__init__(*args, **kwargs)
        self._scoped_to = scoped_to
        self._created_by = created_by

    def clean(self) -> dict:
        data = super().clean() or {}
        if not data.get("discount_pct") and not data.get("discount_fixed_cents"):
            raise forms.ValidationError("Set either a percent OR a fixed-cents discount.")
        return data

    def save(self, commit: bool = True) -> DiscountCode:
        code = super().save(commit=False)
        if self._scoped_to is not None and not code.class_offering_id:
            code.class_offering = self._scoped_to
        if self._created_by is not None and not code.created_by_id:
            code.created_by = self._created_by
        if commit:
            code.save()
            self.save_m2m()
        return code


class DiscountCodeRequestForm(forms.ModelForm):
    """An instructor's ask for a class code under approval mode.

    One form serves both the global Discount Codes page and the per-class tab:
    ``class_offering`` is a required choice limited to the instructor's own live classes,
    preselected from ``?class=<pk>`` when the tab sent them here. The code itself is made by
    ``DiscountCodeRequest.approve``, never here.
    """

    discount_fixed_cents = CentsAsDollarsField(
        required=False,
        label="Fixed discount ($)",
        help_text="Flat dollar amount off, for example 20.00 for $20 off.",
    )

    class Meta:
        model = DiscountCodeRequest
        fields = [
            "class_offering",
            "code",
            "discount_pct",
            "discount_fixed_cents",
            "valid_from",
            "valid_until",
            "max_uses",
            "reason",
        ]
        labels = {"class_offering": "Class"}
        help_texts = {
            "code": "Letters and numbers, for example EARLYBIRD. It is uppercased for you.",
            "max_uses": "Leaving the 'uses' field blank indicates unlimited uses.",
            "reason": "Why you want this code. The admin who decides reads it.",
        }
        widgets = {
            "code": forms.TextInput(attrs={"oninput": "this.value = this.value.toUpperCase()"}),
            "reason": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args: Any, teaching_member: Member, initial_class: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._teaching_member = teaching_member
        class_field = cast(forms.ModelChoiceField, self.fields["class_offering"])
        class_field.queryset = (
            ClassOffering.objects.filter(instructor=teaching_member)
            .exclude(status__in=[ClassOffering.Status.CANCELLED, ClassOffering.Status.ARCHIVED])
            .order_by("title")
        )
        if initial_class:
            # An unknown or foreign pk is ignored, as teach_discount_code_create does with ?class=.
            try:
                class_field.initial = class_field.queryset.get(pk=int(initial_class)).pk
            except (ClassOffering.DoesNotExist, ValueError, TypeError):
                pass

    def clean_code(self) -> str:
        """Refuse a code that exists or is already asked for; better here than at approval."""
        code = self.cleaned_data["code"].strip().upper()
        if (
            DiscountCode.objects.filter(code=code).exists()
            or DiscountCodeRequest.objects.pending().filter(code=code).exists()
        ):
            raise forms.ValidationError("That code is already taken. Pick another.")
        return code

    def clean_discount_pct(self) -> int | None:
        """A zero percent is no discount: the constraint only tests null, so this refuses 0 here."""
        pct = self.cleaned_data["discount_pct"]
        if pct is not None and pct < 1:
            raise forms.ValidationError("Percent off must be at least 1.")
        return pct

    # No clean(): the "percent or fixed amount" rule is the model's CheckConstraint, whose
    # violation_error_message ModelForm validation renders as the one non-field error. A form
    # check here as well rendered two errors for one gap.

    def save(self, commit: bool = True) -> DiscountCodeRequest:
        self.instance.requested_by = self._teaching_member
        return super().save(commit=commit)


class DiscountCodeRequestDeclineForm(forms.Form):
    """The one thing a decline needs: a note the instructor will read.

    ``CharField`` strips by default and refuses a whitespace-only value with its required
    message, so no ``clean_note`` is needed; ``DiscountCodeRequest.decline`` is the last gate.
    """

    note = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Why it was declined",
        help_text="The instructor sees this note.",
    )


class RegistrationQuestionForm(forms.ModelForm):
    """Admin form for creating/editing global registration questions.

    The ``choices_json`` list is presented as a Textarea where each line
    is one option. Lines are converted to/from a JSON list on clean/init.
    """

    choices_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Choices (one per line)",
        help_text="Only used for Single Choice questions. Enter one option per line.",
    )

    class Meta:
        model = RegistrationQuestion
        fields = [
            "prompt",
            "question_type",
            "is_required",
            "is_active",
            "sort_order",
            "mailchimp_tag",
        ]
        help_texts = {
            "mailchimp_tag": (
                "Optional. For Yes/No and Single Choice questions, the newsletter tag prefix sent to Mailchimp "
                "when someone opts in. Leave blank to auto-name it from the question."
            ),
        }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.choices_json:
            self.fields["choices_text"].initial = "\n".join(self.instance.choices_json)

    def clean_choices_text(self) -> list[str]:
        raw = self.cleaned_data.get("choices_text", "")
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        return lines

    def clean(self) -> dict:
        data = super().clean() or {}
        qtype = data.get("question_type")
        choices = data.get("choices_text", [])
        if qtype == RegistrationQuestion.QuestionType.SINGLE_CHOICE and not choices:
            self.add_error("choices_text", "Single choice questions need at least one option.")
        return data

    def save(self, commit: bool = True) -> RegistrationQuestion:
        question = super().save(commit=False)
        question.choices_json = self.cleaned_data.get("choices_text", [])
        if commit:
            question.save()
        return question


class RegistrationForm(forms.ModelForm):
    """Public registration form — collects registrant + waiver signatures.

    One price engine (:meth:`_price_cents`) serves the quote on the page and the charge at
    checkout: sale price, then the code. ``member`` is accepted for callers that already
    resolved one and is not read by the form: the price does not depend on it, and the saved
    row links itself to the Member by email. On save, creates the Registration plus signed
    Waiver records.
    """

    discount_code = forms.CharField(
        max_length=40,
        required=False,
        label="Discount code (optional)",
        widget=forms.TextInput(
            attrs={
                "style": "text-transform:uppercase;",
                "oninput": "this.value = this.value.toUpperCase()",
            }
        ),
    )
    liability_signature = forms.CharField(
        max_length=255,
        label="Type your full name to sign the liability waiver",
    )
    model_release_signature = forms.CharField(
        max_length=255,
        required=False,
        label="Type your full name to sign the photo release",
    )
    accepts_liability = forms.BooleanField(
        label="I have read and agree to the liability waiver above.",
    )
    accepts_model_release = forms.BooleanField(
        required=False,
        label="I have read and agree to the photo release above.",
    )

    class Meta:
        model = Registration
        fields = [
            "first_name",
            "last_name",
            "pronouns",
            "email",
            "phone",
            "prior_experience",
            "looking_for",
            "wants_newsletter",
            "create_account",
        ]
        widgets = {
            "prior_experience": forms.Textarea(attrs={"rows": 3}),
            "looking_for": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "wants_newsletter": (
                "Keep me in the loop — email me about future classes, events, and what's happening at Past Lives."
            ),
            "create_account": (
                "Create a Past Lives account so you can manage your bookings — "
                "no password, we'll email you a sign-in code."
            ),
        }

    def __init__(
        self,
        *args,
        offering: ClassOffering,
        settings_obj: ClassSettings,
        member: "Member | None" = None,
        client_ip: str = "",
        is_waitlist: bool = False,
        holds_seat: bool = False,
        user: "AbstractBaseUser | AnonymousUser | None" = None,
        custom_answers_initial: dict[int, str] | None = None,
        refresh_params: Mapping[str, str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.offering = offering
        self.settings_obj = settings_obj
        self.member = member
        self.client_ip = client_ip
        self.is_waitlist = is_waitlist
        # The page's own query (``waitlist``, ``waitlist_token``), kept on the refresh URL so the
        # re-rendered summary is the one this page shows: a claim link keeps its seat and a
        # voluntary waitlist page keeps its no-charge form.
        self._refresh_params = dict(refresh_params or {})
        # This email already holds a seat in this class, so the class is not sold out
        # to THEM: the row making it full is their own. Set by the register view, which
        # resolves the existing signup before it builds the form.
        self.holds_seat = holds_seat
        self._validated_discount: DiscountCode | None = None
        self.auto_applied_discount: DiscountCode | None = None
        if not offering.requires_model_release:
            # Hide model release fields entirely when the class doesn't need them.
            self.fields.pop("model_release_signature")
            self.fields.pop("accepts_model_release")
        # Tracks whether we *asked*. A popped field can't bind, so without this the
        # saved row would read False and be indistinguishable from a deliberate
        # untick — see save().
        self._newsletter_opt_in_suppressed = False
        if self._user_already_opted_in(user):
            # Don't re-ask a user who already opted in during a prior session.
            self.fields.pop("wants_newsletter", None)
            self._newsletter_opt_in_suppressed = True
        if user is not None and user.is_authenticated:
            # Logged-in registrants already have an account — nothing to offer.
            self.fields.pop("create_account", None)
        else:
            # Anonymous registrants: offer the account, opt-in but default ON.
            self.fields["create_account"].required = False
            if not self.is_bound:
                self.fields["create_account"].initial = True
        if is_waitlist:
            # Waitlist signups don't transact money so the discount field is
            # noise on the form. Drop it so the registrant isn't confused.
            self.fields.pop("discount_code", None)
        # A non-stacking sale can't be combined with discount codes — drop the
        # code box entirely so the buyer is told up-front on the page, never
        # rejected after submit.
        self.sale_blocks_codes = offering.sale_is_active and not offering.sale_allow_discount_codes
        if self.sale_blocks_codes:
            self.fields.pop("discount_code", None)
        self._custom_questions = list(active_questions())
        inject_fields(self, self._custom_questions, custom_answers_initial)
        self._wire_price_refresh()
        # On the first GET render, pre-fill the discount field with the best
        # class-scoped auto-apply code (if one exists). The registrant can
        # still clear it before submitting. Skipped when a non-stacking sale
        # blocks codes — there is no field to prefill.
        if not self.is_bound and not self.sale_blocks_codes:
            applied = self._find_auto_apply_discount()
            if applied is not None:
                self.fields["discount_code"].initial = applied.code
                self.auto_applied_discount = applied

    def _wire_price_refresh(self) -> None:
        """Re-fetch the price summary when the email or the code box changes.

        The quote is for those two as they stand, so each carries the same ``hx-get`` back
        to this page; the summary is swapped in and the button label out of band. A returning
        guest's saved answers ride the same refresh when the class asks questions: they can't
        pre-fill server-side until the email is known.
        """
        from django.urls import reverse

        select_oob = "#reg-submit-label"
        if self._custom_questions:
            select_oob += ",#custom-questions-block"
        url = reverse("classes:register", kwargs={"slug": self.offering.slug})
        if self._refresh_params:
            url += "?" + urlencode(self._refresh_params)
        refresh_attrs = {
            "hx-get": url,
            "hx-trigger": "change",
            "hx-include": "[name=email],[name=discount_code]",
            "hx-target": "#reg-price-summary",
            "hx-select": "#reg-price-summary",
            "hx-swap": "outerHTML",
            "hx-select-oob": select_oob,
        }
        for name in ("email", "discount_code"):
            if name in self.fields:
                self.fields[name].widget.attrs.update(refresh_attrs)

    @staticmethod
    def _user_already_opted_in(user: "AbstractBaseUser | AnonymousUser | None") -> bool:
        """True when a logged-in user has already opted into Mailchimp."""
        if user is None or not user.is_authenticated:
            return False
        profile = getattr(user, "profile", None)
        return profile is not None and profile.subscribed_to_mailchimp_at is not None

    def _find_auto_apply_discount(self) -> DiscountCode | None:
        """Pick the class-scoped auto-apply code that yields the lowest final price.

        The base is the price before any code, so the cheapest code is chosen against the
        price actually paid. Defers the choice to the DiscountCode manager; ``None`` when no
        qualifying auto-apply code exists.
        """
        base = self._price_cents(code=None)
        return DiscountCode.objects.best_auto_apply_for(self.offering, base)

    def _code_for(self, raw: str) -> DiscountCode | None:
        """The code spelled ``raw`` that this class honours, valid or not, else ``None``.

        Codes are either global (class_offering is null) or scoped to this class. A code
        scoped to some other class is not recognized here.
        """
        return (
            DiscountCode.objects.filter(Q(class_offering__isnull=True) | Q(class_offering=self.offering))
            .filter(code=raw)
            .first()
        )

    def clean_discount_code(self) -> DiscountCode | None:
        raw = (self.cleaned_data.get("discount_code") or "").strip().upper()
        if not raw:
            return None
        code = self._code_for(raw)
        if code is None:
            raise forms.ValidationError("That discount code isn't recognized.")
        if not code.is_currently_valid():
            raise forms.ValidationError("That discount code isn't valid right now.")
        self._validated_discount = code
        return code

    def clean(self) -> dict:
        data = super().clean() or {}
        # None is a flexible class: no seat cap, so it is never sold out (#545).
        spots = self.offering.spots_remaining
        if not self.is_waitlist and not self.holds_seat and spots is not None and spots <= 0:
            raise forms.ValidationError("This class is sold out.")
        if self.offering.requires_model_release and not data.get("accepts_model_release"):
            self.add_error("accepts_model_release", "Photo release acceptance is required for this class.")
        if not self.is_waitlist:
            # Stripe rejects USD charges under $0.50. Either drop to 0 (free) or be at/above the minimum.
            final_price = self.compute_final_price_cents()
            if 0 < final_price < STRIPE_MIN_CHARGE_CENTS:
                raise forms.ValidationError(
                    "The total comes out to less than $0.50, which we can't charge online. "
                    "Please remove any discount code, or contact the studio if this looks wrong."
                )
        return data

    @property
    def validated_discount(self) -> DiscountCode | None:
        """The code this submission validated, if any.

        Public because the row is not always written by :meth:`save`: a signup resumed
        onto an existing registration re-stamps the code whose price it is charging.
        """
        return self._validated_discount

    def _price_cents(self, *, code: DiscountCode | None) -> int:
        """The one price engine: sale first, the code last, floor at zero.

        The quote on the page and the charge at checkout both come through here, so the
        number a registrant reads is the number Stripe is handed.
        """
        price = self.offering.sale_price_cents  # == price_cents when no sale
        if code is not None and not self.sale_blocks_codes:  # coupon last, unless the sale blocks it
            price = code.apply_to(price)
        return max(0, price)

    def compute_final_price_cents(self) -> int:
        """What this validated submission is charged."""
        return self._price_cents(code=self._validated_discount)

    def _code_as_shown(self) -> DiscountCode | None:
        """The code box as the page renders it, looked up leniently: blank or unusable quotes no code."""
        if "discount_code" not in self.fields:
            return None
        raw = str(self["discount_code"].value() or "").strip().upper()
        code = self._code_for(raw) if raw else None
        if code is None or not code.is_currently_valid():
            return None
        return code

    def quoted_price_cents(self) -> int:
        """The number on the summary and the button: what this page, as it stands, would charge.

        Reads the code box as it renders (the POST when bound, else the initial, which on
        first render is the auto-applied code). Never raises: a blank or unrecognised code
        quotes no code, which is what a submit with it would charge.
        """
        return self._price_cents(code=self._code_as_shown())

    def save(self, commit: bool = True) -> Registration:
        registration: Registration = super().save(commit=False)
        registration.class_offering = self.offering
        registration.discount_code = self.validated_discount
        if self._newsletter_opt_in_suppressed:
            # We hid the checkbox because this person already opted in, so the
            # unbound field left the flag False. Record the opt-in they actually
            # have: hiding the box means "don't ask again", never "unsubscribe".
            # Downstream (subscribe_registration, the admin detail page) can then
            # read one honest field instead of re-deriving what the form decided.
            registration.wants_newsletter = True
        registration.amount_paid_cents = 0  # set on payment success or, for a $0 total, on confirm
        if self.is_waitlist:
            # Create the row already on the waitlist so Registration.save logs
            # WAITLIST_JOINED at creation time rather than REGISTRATION_CREATED.
            registration.status = Registration.Status.WAITLISTED
        if commit:
            registration.save()
            self._create_waivers(registration)
            self._create_custom_answers(registration)
        return registration

    def custom_answers(self) -> dict[int, str]:
        """Non-empty answers to the custom questions, keyed by question id.

        Exposed so the view can remember them on the registrant's profile after
        a successful save without reaching into form internals.
        """
        return collect_answers(self, self._custom_questions)

    def _create_custom_answers(self, registration: Registration) -> None:
        rows = [
            RegistrationAnswer(registration=registration, question_id=qid, answer_text=text)
            for qid, text in self.custom_answers().items()
        ]
        if rows:
            RegistrationAnswer.objects.bulk_create(rows)

    @property
    def custom_question_fields(self):
        """Iterable of bound BoundField objects for the dynamic custom questions.

        Lets templates render the custom questions as their own fieldset
        without iterating the entire form.
        """
        return [self[f"custom_q_{q.pk}"] for q in self._custom_questions]

    def _create_waivers(self, registration: Registration) -> None:
        create_waivers(
            registration, settings_obj=self.settings_obj, cleaned_data=self.cleaned_data, client_ip=self.client_ip
        )


def create_waivers(
    registration: Registration, *, settings_obj: ClassSettings, cleaned_data: dict[str, Any], client_ip: str
) -> None:
    """Record the signed liability waiver, and the photo release when the class asks for one.

    The one place a Waiver row is written, so the site's registration form and the
    Eventbrite finish page (#652) record a signature identically: the text as shown, the
    typed name and the signer's IP.
    """
    Waiver.objects.create(
        registration=registration,
        kind=Waiver.Kind.LIABILITY,
        waiver_text=settings_obj.liability_waiver_text,
        signature_text=cleaned_data["liability_signature"],
        ip_address=client_ip or None,
    )
    if registration.class_offering.requires_model_release:
        Waiver.objects.create(
            registration=registration,
            kind=Waiver.Kind.MODEL_RELEASE,
            waiver_text=settings_obj.model_release_waiver_text,
            signature_text=cleaned_data["model_release_signature"],
            ip_address=client_ip or None,
        )


class FinishRegistrationForm(forms.Form):
    """The Eventbrite buyer's finish page: the waivers, the site's questions and an account (#652).

    Eventbrite has already taken the name, email and payment, so this asks only what its
    checkout cannot: the same signatures and questions :class:`RegistrationForm` asks, saved
    the same way. ``offers_account`` comes from ``eventbrite_offers_account``; without it the
    account box is not shown.
    """

    liability_signature = forms.CharField(max_length=255, label="Type your full name to sign the liability waiver")
    accepts_liability = forms.BooleanField(label="I have read and agree to the liability waiver above.")
    model_release_signature = forms.CharField(
        max_length=255, required=False, label="Type your full name to sign the photo release"
    )
    accepts_model_release = forms.BooleanField(
        required=False, label="I have read and agree to the photo release above."
    )
    create_account = forms.BooleanField(
        required=False,
        initial=True,
        label="Create a Past Lives account so you can manage your bookings. No password, we'll email you a sign-in code.",
    )

    def __init__(self, *args: Any, offering: ClassOffering, offers_account: bool, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.offering = offering
        if not offering.requires_model_release:
            self.fields.pop("model_release_signature")
            self.fields.pop("accepts_model_release")
        if not offers_account:
            self.fields.pop("create_account")
        self._custom_questions = list(active_questions())
        inject_fields(self, self._custom_questions)

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if self.offering.requires_model_release and not data.get("accepts_model_release"):
            self.add_error("accepts_model_release", "Photo release acceptance is required for this class.")
        return data

    @property
    def custom_question_fields(self) -> list[forms.BoundField]:
        """The question fields, for the shared questions block."""
        return [self[f"custom_q_{q.pk}"] for q in self._custom_questions]

    @property
    def wants_account(self) -> bool:
        """Whether the account box was offered and left ticked."""
        return bool(self.cleaned_data.get("create_account"))

    def save_to(self, registration: Registration, *, settings_obj: ClassSettings, client_ip: str) -> None:
        """Write the signatures and answers onto ``registration``, as :class:`RegistrationForm` does."""
        create_waivers(registration, settings_obj=settings_obj, cleaned_data=self.cleaned_data, client_ip=client_ip)
        rows = [
            RegistrationAnswer(registration=registration, question_id=qid, answer_text=text)
            for qid, text in collect_answers(self, self._custom_questions).items()
        ]
        RegistrationAnswer.objects.bulk_create(rows)


class ClassSettingsForm(forms.ModelForm):
    """The Waivers & Reminders page: the general class settings only.

    The Host a Class page's words moved to :class:`TeachingPageSettingsForm` and its
    own page, so this form is back to the fields the waivers page has always saved.
    """

    class Meta:
        model = ClassSettings
        fields = [
            "liability_waiver_text",
            "model_release_waiver_text",
            "reminder_hours_before",
            "instructor_approval_required",
            "confirmation_email_footer",
        ]
        widgets = {
            "liability_waiver_text": forms.Textarea(attrs={"rows": 10}),
            "model_release_waiver_text": forms.Textarea(attrs={"rows": 10}),
            "confirmation_email_footer": forms.Textarea(attrs={"rows": 3}),
        }


class TeachingPageSettingsForm(forms.ModelForm):
    """The Teaching Marketing Page: every word of the Host a Class page, plus the money split.

    The three prose sections use the rich editor (``PageContentEditorWidget``), so what an
    admin types is stored as sanitized HTML; an older Markdown value, or one typed into
    the raw textarea with no JS, passes through unchanged and still renders. The fields
    are declared in page order, which is the order the template walks them.
    """

    PERCENT_FIELDS = (
        "teach_page_split_instructor_pct",
        "teach_page_split_space_pct",
        "teach_page_split_guild_pct",
    )

    class Meta:
        model = ClassSettings
        fields = [
            "teach_page_title",
            "teach_page_lead",
            "teach_page_features",
            "teach_page_how_it_works",
            "teach_page_split_enabled",
            "teach_page_split_instructor_pct",
            "teach_page_split_space_pct",
            "teach_page_split_guild_pct",
            "teach_page_split_note",
            "teach_page_expectations",
            "teach_page_faq",
            "teach_page_cta_title",
            "teach_page_cta_line",
            "example_class",
        ]
        labels = {
            "teach_page_title": "Headline",
            "teach_page_lead": "Lead Paragraph",
            "teach_page_features": "The cards, one per line",
            "teach_page_how_it_works": "The steps",
            "teach_page_split_enabled": "Show the money section",
            "teach_page_split_instructor_pct": "You (the host)",
            "teach_page_split_space_pct": "Past Lives",
            "teach_page_split_guild_pct": "The guild",
            "teach_page_split_note": "Line under the split",
            "teach_page_expectations": "The list",
            "teach_page_faq": "The questions and their answers",
            "teach_page_cta_title": "Bottom Card Headline",
            "teach_page_cta_line": "Bottom Card Line",
            "example_class": "Example Class Page",
        }
        widgets = {
            "teach_page_lead": forms.Textarea(attrs={"rows": 4}),
            "teach_page_features": forms.Textarea(attrs={"rows": 8}),
            "teach_page_how_it_works": PageContentEditorWidget(markdown_profile="member"),
            "teach_page_split_instructor_pct": forms.NumberInput(attrs={"min": 0, "max": 100, "inputmode": "numeric"}),
            "teach_page_split_space_pct": forms.NumberInput(attrs={"min": 0, "max": 100, "inputmode": "numeric"}),
            "teach_page_split_guild_pct": forms.NumberInput(attrs={"min": 0, "max": 100, "inputmode": "numeric"}),
            "teach_page_split_note": forms.Textarea(attrs={"rows": 2}),
            "teach_page_expectations": PageContentEditorWidget(markdown_profile="member"),
            "teach_page_faq": PageContentEditorWidget(markdown_profile="member"),
            "teach_page_cta_line": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Only a published class can be the worked example: the page hides any other
        # pick, so offering drafts and archived classes here would be offering choices
        # that silently do nothing.
        example_field = self.fields["example_class"]
        assert isinstance(example_field, forms.ModelChoiceField)
        example_field.queryset = ClassOffering.objects.filter(status=ClassOffering.Status.PUBLISHED).order_by("title")
        # The model's MaxValueValidator only runs in _post_clean, after clean(); at field
        # level a share over 100 fails its own check first and stays out of cleaned_data,
        # so the sum rule never piles a second error on top of it.
        for name in self.PERCENT_FIELDS:
            self.fields[name].validators.append(MaxValueValidator(100))

    def _clean_prose(self, name: str) -> str:
        """Sanitize a rich editor save; pass Markdown (a no JS textarea) through unchanged."""
        from membership.markdown import sanitize_page_submission

        return sanitize_page_submission(self.cleaned_data[name] or "")

    def clean_teach_page_how_it_works(self) -> str:
        return self._clean_prose("teach_page_how_it_works")

    def clean_teach_page_expectations(self) -> str:
        return self._clean_prose("teach_page_expectations")

    def clean_teach_page_faq(self) -> str:
        return self._clean_prose("teach_page_faq")

    def clean(self) -> dict[str, Any]:
        """The three shares must add up to 100 while the money section is shown.

        A hidden section skips the check on purpose: an admin switching it off should
        not be blocked by stale numbers. A share that failed its own validation is
        absent from ``cleaned_data`` and already carries an error, so the sum is only
        checked when all three are present.
        """
        super().clean()
        cleaned = self.cleaned_data
        if cleaned["teach_page_split_enabled"] and all(name in cleaned for name in self.PERCENT_FIELDS):
            if sum(cleaned[name] for name in self.PERCENT_FIELDS) != 100:
                self.add_error("teach_page_split_instructor_pct", "The three shares have to add up to 100.")
        return cleaned


class _AnyValueMultipleChoiceField(forms.MultipleChoiceField):
    """A multi-select that accepts any submitted value, leaving the judgement to the form.

    Mirrors ``hub.forms._RecipientChoiceField``. A stale or hand-crafted row id must be dropped
    quietly rather than raised as "Select a valid choice": the honest answer to a tampered POST
    is the intersection with what the sender actually owns, which the form then rules on itself.
    """

    def valid_value(self, value: str) -> bool:  # noqa: D102 - see class docstring
        return True


class RosterSelectionForm(forms.Form):
    """The ticked rows on the teaching portal's Registrations tab, on their way to the composer.

    Three things have to hold before a selection can be handed over, and each is a rejection the
    instructor can act on rather than a row quietly disappearing:

    1. The rows are this teaching member's to email. Anything else is dropped, never emailed.
    2. They all sit in ONE class — the composer scopes to a single class, so a cross-class
       selection cannot be expressed there at all.
    3. At least one is somebody the composer can actually reach
       (:attr:`Registration.can_receive_class_announcement`). A cancelled or unpaid student has
       no checkbox on the composer's roster, so handing over only those tokens would pre-check
       nothing — and an empty pre-selection means "everyone", which would arm one click to
       email the whole class. Reachable rows that were NOT ticked must never become checked.

    A selection that mixes reachable and unreachable rows goes through with the reachable ones,
    and :attr:`dropped_notice` names the rest so the instructor is told who is not going.
    """

    NOTHING_SELECTED = "Tick the students you want to email first."
    MIXED_CLASSES = "Pick students from one class at a time."
    ONLY_EMAIL_CONFIRMED = "You can only email confirmed students and people on the waitlist."
    NONE_REACHABLE = f"Those students can't be emailed from here. {ONLY_EMAIL_CONFIRMED}"

    registration_ids = _AnyValueMultipleChoiceField(required=False, label="Students")

    def __init__(self, *args: Any, teaching_member: "Member", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.teaching_member = teaching_member
        self.registrations: list[Registration] = []
        self.dropped: list[Registration] = []

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        # ``registration_ids`` cannot fail on its own (optional, and every value is accepted),
        # so it is always in cleaned_data by here.
        ids = [value for value in cleaned["registration_ids"] if value.isdigit()]
        selected = list(
            Registration.objects.filter(pk__in=ids, class_offering__instructor=self.teaching_member)
            .select_related("class_offering", "member__user")
            .order_by("pk")
        )
        if not selected:
            raise ValidationError(self.NOTHING_SELECTED)
        if len({registration.class_offering_id for registration in selected}) > 1:
            raise ValidationError(self.MIXED_CLASSES)

        emailable: list[Registration] = []
        dropped: list[Registration] = []
        for registration in selected:
            if registration.can_receive_class_announcement:
                emailable.append(registration)
            else:
                dropped.append(registration)
        if not emailable:
            raise ValidationError(self.NONE_REACHABLE)

        self.registrations = emailable
        self.dropped = dropped
        return cleaned

    @property
    def offering(self) -> ClassOffering:
        """The one class the selection belongs to. Only meaningful once the form has validated."""
        return self.registrations[0].class_offering

    @property
    def recipient_tokens(self) -> list[str]:
        """The composer's pre-checked recipient values for the selected students.

        Never empty for a valid form: an all-unreachable selection is rejected in
        :meth:`clean`, precisely so this cannot hand the composer nothing.
        """
        return Registration.announcement_recipient_tokens(self.registrations)

    @property
    def dropped_notice(self) -> str:
        """A heads-up naming the ticked students who are not going, or ``""`` when all are.

        Quietly dropping half a selection is the same defect as dropping all of it, just
        smaller: the instructor has to be told which rows the composer will not carry.
        """
        if not self.dropped:
            return ""
        names = ", ".join(registration.roster_name for registration in self.dropped)
        return f"Left out: {names}. {self.ONLY_EMAIL_CONFIRMED}"

    @property
    def needs_waitlist(self) -> bool:
        """True when a waitlisted student was ticked.

        The composer builds a class roster from confirmed registrants alone unless the waitlist is
        folded in, so without this those picks would quietly vanish from the checklist. Reads the
        surviving rows, not the raw selection: a dropped row must not widen the roster.
        """
        return any(r.status == Registration.Status.WAITLISTED for r in self.registrations)

    @property
    def error_message(self) -> str:
        """The one message to flash back on the Registrations tab.

        Every rejection this form makes is a whole-selection rejection raised in :meth:`clean`,
        so there is always exactly one non-field error to show.
        """
        return str(self.non_field_errors()[0])


class TeachEmailForm(forms.Form):
    """Form for a teaching member to send a manual email to selected registrants of one of their classes.

    Recipient selection is bounded to ``Registration.objects.filter(class_offering__instructor=teaching_member)``
    so the form will not accept registration IDs outside the teaching member's own
    classes even if a hostile client submits them.
    """

    subject = forms.CharField(max_length=255, label="Subject")
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 8}), label="Message")
    registration_ids = forms.ModelMultipleChoiceField(
        queryset=Registration.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Recipients",
    )
    bcc_self = forms.BooleanField(required=False, initial=True, label="Send me a copy", help_text="BCC your own email.")

    def __init__(self, *args, teaching_member: "Member", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.teaching_member = teaching_member
        self.fields["registration_ids"].queryset = Registration.objects.filter(  # type: ignore[attr-defined]  # ModelMultipleChoiceField has queryset
            class_offering__instructor=teaching_member,
        ).select_related("class_offering")

    def send(self) -> InstructorMessage:
        """Send the email and record the InstructorMessage + recipient audit rows.

        Returns the created InstructorMessage. Caller is responsible for any
        success/error flash messages. Routes through the ``core.email.send``
        choke-point (Decision 8) so the send is audited in ``TransactionalEmailLog``
        instead of bypassing it; registrants stay BCC'd (private) and the test suite
        can still assert on ``mail.outbox``.
        """
        from django.db import transaction

        from core.email import send as send_email

        registrations = list(self.cleaned_data["registration_ids"])
        # All selected regs share the same class_offering only if the teaching member
        # is sending to a single class. We anchor the message to the first
        # registration's class — typical UX is one class at a time.
        offering = registrations[0].class_offering
        bcc_emails = [r.email for r in registrations]
        bcc_self = self.cleaned_data.get("bcc_self", True)
        teaching_member_email = (self.teaching_member.primary_email or "").strip()
        to_addresses = [teaching_member_email] if teaching_member_email else []
        if (
            bcc_self
            and teaching_member_email
            and teaching_member_email not in bcc_emails
            and teaching_member_email not in to_addresses
        ):
            bcc_emails.append(teaching_member_email)

        send_email(
            to=to_addresses,
            subject=self.cleaned_data["subject"],
            trigger_kind="classes.instructor_message",
            text_body=self.cleaned_data["body"],
            bcc=bcc_emails,
        )

        with transaction.atomic():
            message = InstructorMessage.objects.create(
                instructor=self.teaching_member,
                sent_by=self.teaching_member,
                class_offering=offering,
                subject=self.cleaned_data["subject"],
                body=self.cleaned_data["body"],
                recipient_count=len(registrations),
                bcc_self=bool(bcc_self),
            )
            InstructorMessageRecipient.objects.bulk_create(
                [InstructorMessageRecipient(message=message, registration=r, email=r.email) for r in registrations]
            )
        return message


class AdminClassEmailForm(forms.Form):
    """Form for an admin to email registrants of a specific class.

    Scoped to one ClassOffering (not instructor-scoped). Excludes cancelled/refunded
    registrations from the selectable queryset.
    """

    subject = forms.CharField(max_length=255, label="Subject")
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}), label="Message")
    registration_ids = forms.ModelMultipleChoiceField(
        queryset=Registration.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Recipients",
    )
    bcc_self = forms.BooleanField(required=False, initial=True, label="Send me a copy")

    def __init__(self, *args, offering: ClassOffering, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.offering = offering
        self.fields["registration_ids"].queryset = (  # type: ignore[attr-defined]  # ModelMultipleChoiceField has queryset
            Registration.objects.filter(
                class_offering=offering,
            )
            .exclude(
                status__in=[Registration.Status.CANCELLED, Registration.Status.REFUNDED],
            )
            .select_related("class_offering")
        )

    def send(self, *, sender_member: Member | None = None) -> InstructorMessage:
        """Send the admin class email + record the audit rows.

        Routes through the ``core.email.send`` choke-point (Decision 8) so the send
        is audited in ``TransactionalEmailLog``; registrants stay BCC'd.
        """
        from django.db import transaction

        from core.email import send as send_email

        registrations = list(self.cleaned_data["registration_ids"])
        bcc_emails = [r.email for r in registrations]
        bcc_self = self.cleaned_data.get("bcc_self", True)
        sender_email = (sender_member.primary_email if sender_member else "") or ""
        to_addresses = [sender_email] if sender_email else []
        if bcc_self and sender_email and sender_email not in bcc_emails:
            bcc_emails.append(sender_email)

        send_email(
            to=to_addresses,
            subject=self.cleaned_data["subject"],
            trigger_kind="classes.admin_message",
            text_body=self.cleaned_data["body"],
            bcc=bcc_emails,
        )

        with transaction.atomic():
            message = InstructorMessage.objects.create(
                instructor=None,
                sent_by=sender_member,
                class_offering=self.offering,
                subject=self.cleaned_data["subject"],
                body=self.cleaned_data["body"],
                recipient_count=len(registrations),
                bcc_self=bool(bcc_self),
            )
            InstructorMessageRecipient.objects.bulk_create(
                [InstructorMessageRecipient(message=message, registration=r, email=r.email) for r in registrations]
            )
        return message


class RegistrationMoveForm(forms.Form):
    """Pick a different class to reassign a registration to.

    The target queryset excludes the registration's current class, so a
    same-class move can't be selected (or POSTed) at all — no extra clean needed.
    The two audiences are deliberately asymmetric:

    - **Admins** (no ``instructor``) may pick any *published* ``upcoming()`` class,
      private ones included — parking a student in a private class is a real
      staff move, but parking one in a draft is not, because the student's class
      page would point at something that is not live. Admins may still overfill.
    - **Instructors** (``instructor=`` given) only see their own ``bookable()``
      classes — published, non-private, flexible or not yet started — so the
      moved student's class page link can never 404. A full class is rejected
      in ``clean_target`` for instructors only; admins keep the historical
      ability to overfill on purpose.

    The choices are materialized once at construction (a single query per form
    instance): Django's ``ModelChoiceIterator`` re-runs the queryset on every
    widget render, which would be an N+1 across the per-row roster modals.
    """

    target = forms.ModelChoiceField(
        queryset=ClassOffering.objects.none(),
        label="Move to class",
        empty_label="Choose a class…",
    )

    def __init__(
        self,
        *args: Any,
        current: "ClassOffering | None" = None,
        instructor: "Member | None" = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._instructor = instructor
        if instructor is not None:
            offerings = ClassOffering.objects.bookable().filter(instructor=instructor)
        else:
            # Published only: private classes stay available to admins, drafts do not.
            offerings = ClassOffering.objects.upcoming().filter(status=ClassOffering.Status.PUBLISHED)
        if current is not None:
            offerings = offerings.exclude(pk=current.pk)
        offerings = offerings.order_by("title")
        field = cast(forms.ModelChoiceField, self.fields["target"])
        field.queryset = offerings  # POSTed pks still validate against the scoped queryset
        self._targets: list[ClassOffering] = list(offerings)  # the one query
        field.choices = [("", str(field.empty_label)), *((o.pk, field.label_from_instance(o)) for o in self._targets)]
        field.widget.attrs["style"] = (
            "padding:0.45rem 0.75rem; border:1px solid var(--hub-border); border-radius:6px; "
            "background:var(--hub-card-bg,#0c2236); color:var(--hub-text,#f4efdd); font-size:0.875rem;"
        )

    def clean_target(self) -> ClassOffering:
        """Instructor moves can't overfill the destination; admin moves can (see the class docstring).

        A flexible destination answers ``None`` for its spots: no cap, so never full (#545).
        """
        target = cast(ClassOffering, self.cleaned_data["target"])
        spots = target.spots_remaining
        if self._instructor is not None and spots is not None and spots <= 0:
            raise ValidationError("That class is full.")
        return target

    @property
    def has_targets(self) -> bool:
        """Whether any class can be picked — drives the move modal's empty state. No query: choices are pre-materialized."""
        return bool(self._targets)

    @property
    def is_instructor_scoped(self) -> bool:
        """True when this form was built for an instructor (drives instructor-only modal copy)."""
        return self._instructor is not None

    def any_target_price_differs(self, amount_paid_cents: int) -> bool:
        """Whether any offered class's price differs from what the student paid — drives the modal's price note."""
        return any(offering.price_cents != amount_paid_cents for offering in self._targets)


class TeachWelcomeEmailForm(forms.ModelForm):
    """Edit a class's instructor-authored welcome email.

    Enabling the email requires a subject and a body, so an empty welcome email
    can never be switched on. Saving stamps ``welcome_email_updated_at``.
    """

    class Meta:
        model = ClassOffering
        fields = ["welcome_email_enabled", "welcome_email_subject", "welcome_email_body"]
        widgets = {
            "welcome_email_enabled": forms.CheckboxInput(attrs={"class": "hub-switch-input"}),
            "welcome_email_subject": forms.TextInput(
                attrs={
                    "placeholder": "Welcome to the class!",
                    "style": "width:100%; padding:0.45rem 0.75rem; border:1px solid var(--hub-border); "
                    "border-radius:6px; background:rgba(0,0,0,0.1); color:inherit; font-size:0.9rem;",
                }
            ),
            "welcome_email_body": RichTextEditorWidget(attrs={"rows": 12}),
        }
        labels = {
            "welcome_email_enabled": "Active",
            "welcome_email_subject": "Subject",
            "welcome_email_body": "Message",
        }

    def clean_welcome_email_body(self) -> str:
        return clean_rich_html(self.cleaned_data.get("welcome_email_body") or "")

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        if cleaned.get("welcome_email_enabled"):
            if not (cleaned.get("welcome_email_subject") or "").strip():
                self.add_error("welcome_email_subject", "Add a subject before turning the welcome email on.")
            if not (cleaned.get("welcome_email_body") or "").strip():
                self.add_error("welcome_email_body", "Add a message before turning the welcome email on.")
        return cleaned

    def save(self, commit: bool = True) -> ClassOffering:
        self.instance.welcome_email_updated_at = timezone.now()
        return super().save(commit=commit)


class PaymentRefundForm(RefundShareDecisionForm):
    """Validates the refund modal — amount bounds live here, not in the view.

    ``amount`` is pre-filled with the full refundable remainder (full refund is
    the default; editing it down makes it partial). ``reason`` is an optional
    internal note stored on the ``PaymentRefund`` row — the payer never sees it.
    """

    amount = forms.DecimalField(max_digits=8, decimal_places=2)
    reason = forms.CharField(required=False, widget=forms.TextInput)

    def __init__(self, *args: Any, registration: Registration, **kwargs: Any) -> None:
        self.registration = registration
        refundable = (Decimal(registration.refundable_cents) / 100).quantize(Decimal("0.01"))
        kwargs.setdefault("initial", {})
        kwargs["initial"].setdefault("amount", refundable)
        super().__init__(*args, **kwargs)
        self.fields["amount"].label = "Amount"
        self.fields["amount"].help_text = f"Up to ${refundable:.2f}. Edit for a partial refund."
        self.fields["reason"].label = "Reason"
        self.fields["reason"].help_text = "Internal note. The payer never sees this."
        # Eventbrite refunds a whole ticket, so its form posts no amount.
        self.fields["amount"].required = not registration.is_eventbrite
        self._add_share_decision(registration)  # #662: the required choice once the share was sent

    def clean_amount(self) -> Decimal:
        refundable = Decimal(self.registration.refundable_cents) / 100
        if self.registration.is_eventbrite:
            return refundable
        amount: Decimal = self.cleaned_data["amount"]
        if not Decimal("0.01") <= amount <= refundable:
            raise ValidationError(f"Enter an amount between $0.01 and ${refundable:.2f}.")
        return amount

    @property
    def amount_cents(self) -> int:
        """The validated refund amount in cents — what ``issue_refund`` takes."""
        return int(self.cleaned_data["amount"] * 100)
