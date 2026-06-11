"""Forms for the Classes app."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from django import forms
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
from django.utils.text import slugify

from classes.models import (
    Category,
    ClassImage,
    ClassOffering,
    ClassSession,
    ClassSettings,
    DiscountCode,
    InstructorMessage,
    InstructorMessageRecipient,
    Registration,
    RegistrationAnswer,
    RegistrationQuestion,
    Waiver,
)
from classes.templatetags.classes_tags import youtube_embed_id as _youtube_embed_id


def _validate_youtube_url(url: str) -> str:
    """Return a stripped YouTube URL, raising ValidationError when given a
    non-YouTube link. Empty/blank values pass through (the field is optional)."""
    cleaned = (url or "").strip()
    if not cleaned:
        return ""
    if not _youtube_embed_id(cleaned):
        raise ValidationError("Enter a YouTube URL — e.g. https://www.youtube.com/watch?v=… or https://youtu.be/…")
    return cleaned


if TYPE_CHECKING:
    from membership.models import Member


STRIPE_MIN_CHARGE_CENTS = 50  # Stripe's minimum USD charge is $0.50.
MIN_PAID_PRICE_CENTS = 100  # Floor for paid classes ($1.00) — anything cheaper should just be free.


class CentsAsDollarsField(forms.DecimalField):
    """Accepts dollar input, stores as cents. Model stays PositiveIntegerField."""

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("max_digits", 8)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", Decimal("0"))
        super().__init__(**kwargs)

    def prepare_value(self, value: int | str | None) -> Decimal | int | str | None:
        if value is None or value == "":
            return value
        try:
            return Decimal(int(value)) / 100
        except (ValueError, TypeError, InvalidOperation):
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
        if instance and instance.pk and instance.hero_crop_w and instance.hero_crop_h:
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


class _FreeClassMixin:
    """Adds an `is_free` checkbox that, when checked, forces price/discount to 0.

    Source of truth remains `price_cents` on the model (0 = free). The checkbox
    is a UX affordance so the instructor/admin doesn't have to know that "type 0
    in cents" makes a class free — they just tick a box.
    """

    def add_is_free_field(self) -> None:
        instance = getattr(self, "instance", None)
        initial = bool(instance and instance.pk and instance.price_cents == 0)
        self.fields["is_free"] = forms.BooleanField(  # type: ignore[attr-defined]
            required=False,
            initial=initial,
            label="This is a free class / workshop",
            help_text="Check this if there's no fee. Members will be able to register without entering payment info.",
        )
        # Price and discount aren't required when the class is free — the form's
        # clean() enforces that price_cents is filled in for non-free classes.
        self.fields["price_cents"].required = False  # type: ignore[attr-defined]
        self.fields["member_discount_pct"].required = False  # type: ignore[attr-defined]
        # Render the checkbox just above price so the visual flow is "Is this free?
        # → if not, here's the price." Django keeps this order when iterating `form`.
        ordered: list[str] = []
        for name in self.fields:  # type: ignore[attr-defined]
            if name == "price_cents":
                ordered.append("is_free")
            if name == "is_free":
                continue
            ordered.append(name)
        self.order_fields(ordered)  # type: ignore[attr-defined]

    def clean_is_free_pricing(self) -> None:
        """Require price_cents when the class isn't free. Call from `clean()`."""
        cleaned = self.cleaned_data  # type: ignore[attr-defined]
        if cleaned.get("is_free"):
            return
        price = cleaned.get("price_cents")
        if price in (None, ""):
            self.add_error(  # type: ignore[attr-defined]
                "price_cents", "Set a price or check 'This is a free class / workshop'."
            )
            return
        if price < MIN_PAID_PRICE_CENTS:
            self.add_error(  # type: ignore[attr-defined]
                "price_cents",
                "Paid classes must cost at least $1.00. Check 'This is a free class / workshop' for free classes.",
            )

    def apply_is_free_to_instance(self, offering: ClassOffering) -> None:
        if self.cleaned_data.get("is_free"):  # type: ignore[attr-defined]
            offering.price_cents = 0
            offering.member_discount_pct = 0


class ClassOfferingForm(_HeroCropMixin, _FreeClassMixin, forms.ModelForm):
    price_cents = CentsAsDollarsField(label="Price", help_text="e.g. 80.00 for $80.")

    class Meta:
        model = ClassOffering
        fields = [
            "title",
            "slug",
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
            "member_discount_pct",
            "capacity",
            "scheduling_model",
            "flexible_note",
            "is_private",
            "private_for_name",
            "image",
            "video_url",
        ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["member_discount_pct"].label = "Member discount (%)"
        self.add_is_free_field()
        self.add_hero_crop_field()

    def clean_video_url(self) -> str:
        return _validate_youtube_url(self.cleaned_data.get("video_url", ""))

    def clean(self) -> dict:
        data = super().clean() or {}
        self.clean_is_free_pricing()
        return data

    def save(self, commit: bool = True) -> ClassOffering:
        offering = super().save(commit=False)
        self.apply_is_free_to_instance(offering)
        self.apply_hero_crop_to_instance(offering)
        if commit:
            offering.save()
            self.save_m2m()
        return offering


class TeachClassOfferingForm(_HeroCropMixin, _FreeClassMixin, forms.ModelForm):
    """Class form for teaching members — no `instructor`, no `is_private`, slug auto-generated."""

    price_cents = CentsAsDollarsField(label="Price", help_text="e.g. 80.00 for $80.")

    class Meta:
        model = ClassOffering
        fields = [
            "title",
            "category",
            "description",
            "prerequisites",
            "materials_included",
            "materials_to_bring",
            "safety_requirements",
            "age_minimum",
            "age_guardian_note",
            "price_cents",
            "member_discount_pct",
            "capacity",
            "scheduling_model",
            "flexible_note",
            "image",
            "video_url",
        ]

    def __init__(self, *args, teaching_member: "Member | None" = None, **kwargs) -> None:
        self.teaching_member = teaching_member
        super().__init__(*args, **kwargs)
        self.fields["member_discount_pct"].label = "Member discount (%)"
        self.add_is_free_field()
        self.add_hero_crop_field()

    def clean_video_url(self) -> str:
        return _validate_youtube_url(self.cleaned_data.get("video_url", ""))

    def clean(self) -> dict:
        data = super().clean() or {}
        self.clean_is_free_pricing()
        return data

    def save(self, commit: bool = True) -> ClassOffering:
        offering = super().save(commit=False)
        self.apply_is_free_to_instance(offering)
        self.apply_hero_crop_to_instance(offering)
        if self.teaching_member is not None and not offering.instructor_id:
            offering.instructor = self.teaching_member
            if not offering.created_by_id:
                offering.created_by = self.teaching_member
        if not offering.slug:
            base = slugify(offering.title) or "class"
            slug = base
            n = 1
            while ClassOffering.objects.filter(slug=slug).exclude(pk=offering.pk).exists():
                n += 1
                slug = f"{base}-{n}"
            offering.slug = slug
        if commit:
            offering.save()
        return offering


class TeachProfileForm(forms.ModelForm):
    class Meta:
        from membership.models import Member

        model = Member
        fields = ["preferred_name", "about_me", "profile_photo", "instructor_website", "instructor_social_handle"]


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


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "slug", "sort_order", "hero_image"]


class ClassReviewDecisionForm(forms.Form):
    """Reviewer's decision form on the tokenized review page.

    Notes are required when the decision is changes_requested or denied so
    the instructor gets actionable feedback. They're optional on approve.
    """

    decision = forms.ChoiceField(
        choices=[
            ("approved", "Approve"),
            ("changes_requested", "Request changes"),
            ("denied", "Decline"),
        ],
        widget=forms.RadioSelect,
        label="Decision",
    )
    notes = forms.CharField(
        widget=forms.Textarea(
            attrs={"rows": 4, "placeholder": "Optional on approve; required on request-changes and decline."}
        ),
        required=False,
        label="Notes for the instructor",
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
            "auto_apply",
        ]
        labels = {
            "auto_apply": "Auto-apply for eligible registrants (no need for them to type the code)",
        }

    def __init__(self, *args, scoped_to: ClassOffering | None = None, created_by=None, **kwargs) -> None:
        """Optionally bind this code to a single class and an audit user.

        Passing ``scoped_to`` makes a class-scoped code: registrations for any
        other class won't honor it. Passing ``created_by`` records who made it
        (and, for instructor-created class-scoped codes, auto-approves it
        since the instructor already controls that class's pricing).
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
            # Class-scoped codes created by the instructor of that class auto-approve;
            # the instructor already controls the class price, so admin gating adds
            # friction without protecting anything.
            if (
                self._created_by is not None
                and self._scoped_to.instructor_id
                and self._scoped_to.instructor.user_id == self._created_by.pk  # type: ignore[union-attr]  # instructor_id guard ensures non-None
            ):
                code.is_approved = True
        if self._created_by is not None and not code.created_by_id:
            code.created_by = self._created_by
        if commit:
            code.save()
            self.save_m2m()
        return code


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
        ]

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

    Computes the final price (member discount + optional discount code) and,
    on save, creates the Registration plus signed Waiver records.
    """

    discount_code = forms.CharField(
        max_length=40,
        required=False,
        label="Discount code (optional)",
    )
    liability_signature = forms.CharField(
        max_length=255,
        label="Type your full name to sign the liability waiver",
    )
    model_release_signature = forms.CharField(
        max_length=255,
        required=False,
        label="Type your full name to sign the model release",
    )
    accepts_liability = forms.BooleanField(
        label="I have read and agree to the liability waiver above.",
    )
    accepts_model_release = forms.BooleanField(
        required=False,
        label="I have read and agree to the model release above.",
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
        ]
        widgets = {
            "prior_experience": forms.Textarea(attrs={"rows": 3}),
            "looking_for": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "wants_newsletter": (
                "Keep me in the loop — email me about future classes, events, and what's happening at Past Lives."
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
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.offering = offering
        self.settings_obj = settings_obj
        self.member = member
        self.client_ip = client_ip
        self.is_waitlist = is_waitlist
        self._validated_discount: DiscountCode | None = None
        self.auto_applied_discount: DiscountCode | None = None
        if not offering.requires_model_release:
            # Hide model release fields entirely when the class doesn't need them.
            self.fields.pop("model_release_signature")
            self.fields.pop("accepts_model_release")
        if is_waitlist:
            # Waitlist signups don't transact money so the discount field is
            # noise on the form. Drop it so the registrant isn't confused.
            self.fields.pop("discount_code", None)
        self._custom_questions = list(RegistrationQuestion.objects.filter(is_active=True))
        self._inject_custom_question_fields()
        # On the first GET render, pre-fill the discount field with the best
        # class-scoped auto-apply code (if one exists). The registrant can
        # still clear it before submitting.
        if not self.is_bound:
            applied = self._find_auto_apply_discount()
            if applied is not None:
                self.fields["discount_code"].initial = applied.code
                self.auto_applied_discount = applied

    def _find_auto_apply_discount(self) -> DiscountCode | None:
        """Pick the class-scoped auto-apply code that yields the lowest final price.

        Computes the post-member-discount base this registrant would pay, then
        defers the cheapest-code selection to the DiscountCode manager. Returns
        ``None`` when no qualifying auto-apply code exists.
        """
        base = self.offering.price_cents
        if self.member is not None and self.offering.member_price_cents is not None:
            base = self.offering.member_price_cents
        return DiscountCode.objects.best_auto_apply_for(self.offering, base)

    def _inject_custom_question_fields(self) -> None:
        """Add one dynamic form field per active RegistrationQuestion.

        Field name pattern: ``custom_q_<pk>``. Type is mapped from the
        question's ``question_type``. Required flag follows the question's
        ``is_required``. choices_json drives the options for SINGLE_CHOICE.
        """
        for q in self._custom_questions:
            field_name = f"custom_q_{q.pk}"
            field: forms.Field
            if q.question_type == RegistrationQuestion.QuestionType.LONG_TEXT:
                field = forms.CharField(
                    required=q.is_required,
                    label=q.prompt,
                    widget=forms.Textarea(attrs={"rows": 3}),
                )
            elif q.question_type == RegistrationQuestion.QuestionType.YES_NO:
                field = forms.TypedChoiceField(
                    required=q.is_required,
                    label=q.prompt,
                    choices=[("", "Choose…"), ("yes", "Yes"), ("no", "No")],
                )
            elif q.question_type == RegistrationQuestion.QuestionType.SINGLE_CHOICE:
                options = [(c, c) for c in (q.choices_json or [])]
                field = forms.ChoiceField(
                    required=q.is_required,
                    label=q.prompt,
                    choices=[("", "Choose…")] + options,
                )
            else:  # SHORT_TEXT and any future fallthrough
                field = forms.CharField(
                    required=q.is_required,
                    label=q.prompt,
                    max_length=500,
                )
            self.fields[field_name] = field

    def clean_discount_code(self) -> DiscountCode | None:
        from django.db.models import Q

        raw = (self.cleaned_data.get("discount_code") or "").strip().upper()
        if not raw:
            return None
        # Codes are either global (class_offering is null) or scoped to this
        # class. A code scoped to some other class is not recognized here.
        try:
            code = DiscountCode.objects.filter(Q(class_offering__isnull=True) | Q(class_offering=self.offering)).get(
                code=raw
            )
        except DiscountCode.DoesNotExist:
            raise forms.ValidationError("That discount code isn't recognized.") from None
        if not code.is_currently_valid():
            raise forms.ValidationError("That discount code isn't valid right now.")
        self._validated_discount = code
        return code

    def clean(self) -> dict:
        data = super().clean() or {}
        if not self.is_waitlist and self.offering.spots_remaining <= 0:
            raise forms.ValidationError("This class is sold out.")
        if self.offering.requires_model_release and not data.get("accepts_model_release"):
            self.add_error("accepts_model_release", "Model release acceptance is required for this class.")
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
    def member_discount_pct(self) -> int:
        """Member discount applies only when the registrant matches a verified member."""
        if self.member is None:
            return 0
        return self.offering.member_discount_pct or 0

    def compute_final_price_cents(self) -> int:
        price = self.offering.price_cents
        if self.member_discount_pct:
            price = int(price * (100 - self.member_discount_pct) / 100)
        code = self._validated_discount
        if code is not None:
            price = code.apply_to(price)
        return max(0, price)

    def save(self, commit: bool = True) -> Registration:
        registration: Registration = super().save(commit=False)
        registration.class_offering = self.offering
        registration.discount_code = self._validated_discount
        registration.amount_paid_cents = 0  # set on payment success or, for free classes, on confirm
        if self.is_waitlist:
            # Create the row already on the waitlist so Registration.save logs
            # WAITLIST_JOINED at creation time rather than REGISTRATION_CREATED.
            registration.status = Registration.Status.WAITLISTED
        if commit:
            registration.save()
            self._create_waivers(registration)
            self._create_custom_answers(registration)
        return registration

    def _create_custom_answers(self, registration: Registration) -> None:
        answers = []
        for q in self._custom_questions:
            raw = self.cleaned_data.get(f"custom_q_{q.pk}")
            if raw in (None, ""):
                continue
            answers.append(RegistrationAnswer(registration=registration, question=q, answer_text=str(raw)))
        if answers:
            RegistrationAnswer.objects.bulk_create(answers)

    @property
    def custom_question_fields(self):
        """Iterable of bound BoundField objects for the dynamic custom questions.

        Lets templates render the custom questions as their own fieldset
        without iterating the entire form.
        """
        return [self[f"custom_q_{q.pk}"] for q in self._custom_questions]

    def _create_waivers(self, registration: Registration) -> None:
        Waiver.objects.create(
            registration=registration,
            kind=Waiver.Kind.LIABILITY,
            waiver_text=self.settings_obj.liability_waiver_text,
            signature_text=self.cleaned_data["liability_signature"],
            ip_address=self.client_ip or None,
        )
        if self.offering.requires_model_release:
            Waiver.objects.create(
                registration=registration,
                kind=Waiver.Kind.MODEL_RELEASE,
                waiver_text=self.settings_obj.model_release_waiver_text,
                signature_text=self.cleaned_data["model_release_signature"],
                ip_address=self.client_ip or None,
            )


class ClassSettingsForm(forms.ModelForm):
    class Meta:
        model = ClassSettings
        fields = [
            "liability_waiver_text",
            "model_release_waiver_text",
            "default_member_discount_pct",
            "reminder_hours_before",
            "instructor_approval_required",
            "confirmation_email_footer",
        ]
        widgets = {
            "liability_waiver_text": forms.Textarea(attrs={"rows": 10}),
            "model_release_waiver_text": forms.Textarea(attrs={"rows": 10}),
            "confirmation_email_footer": forms.Textarea(attrs={"rows": 3}),
        }


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
        success/error flash messages. Uses Django's mail backend, so the test
        suite can assert on ``mail.outbox``.
        """
        from django.conf import settings as django_settings
        from django.core.mail import EmailMessage
        from django.db import transaction

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

        email_message = EmailMessage(
            subject=self.cleaned_data["subject"],
            body=self.cleaned_data["body"],
            from_email=django_settings.DEFAULT_FROM_EMAIL,
            to=to_addresses,
            bcc=bcc_emails,
        )
        email_message.send(fail_silently=False)

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
        from django.conf import settings as django_settings
        from django.core.mail import EmailMessage
        from django.db import transaction

        registrations = list(self.cleaned_data["registration_ids"])
        bcc_emails = [r.email for r in registrations]
        bcc_self = self.cleaned_data.get("bcc_self", True)
        sender_email = (sender_member.primary_email if sender_member else "") or ""
        to_addresses = [sender_email] if sender_email else []
        if bcc_self and sender_email and sender_email not in bcc_emails:
            bcc_emails.append(sender_email)

        email_message = EmailMessage(
            subject=self.cleaned_data["subject"],
            body=self.cleaned_data["body"],
            from_email=django_settings.DEFAULT_FROM_EMAIL,
            to=to_addresses,
            bcc=bcc_emails,
        )
        email_message.send(fail_silently=False)

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
