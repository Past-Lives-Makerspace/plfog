"""Forms for the Classes app."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from django import forms
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory
from django.utils.text import slugify

from classes.models import (
    Category,
    ClassImage,
    ClassOffering,
    ClassSession,
    ClassSettings,
    DiscountCode,
    Instructor,
    InstructorMessage,
    InstructorMessageRecipient,
    Registration,
    RegistrationAnswer,
    RegistrationQuestion,
    Waiver,
)

if TYPE_CHECKING:
    from membership.models import Member


STRIPE_MIN_CHARGE_CENTS = 50  # Stripe's minimum USD charge is $0.50.
MIN_PAID_PRICE_CENTS = 100  # Floor for paid classes ($1.00) — anything cheaper should just be free.


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
                "price_cents", "Set a price (in cents) or check 'This is a free class / workshop'."
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
            "recurring_pattern",
            "image",
            "requires_model_release",
        ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.add_is_free_field()
        self.add_hero_crop_field()

    def clean(self) -> dict:
        data = super().clean()
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


class InstructorClassOfferingForm(_HeroCropMixin, _FreeClassMixin, forms.ModelForm):
    """Class form for instructors — no `instructor`, no `is_private`, slug auto-generated."""

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
            "recurring_pattern",
            "image",
            "requires_model_release",
        ]

    def __init__(self, *args, instructor: Instructor | None = None, **kwargs) -> None:
        self.instructor = instructor
        super().__init__(*args, **kwargs)
        self.add_is_free_field()
        self.add_hero_crop_field()

    def clean(self) -> dict:
        data = super().clean()
        self.clean_is_free_pricing()
        return data

    def save(self, commit: bool = True) -> ClassOffering:
        offering = super().save(commit=False)
        self.apply_is_free_to_instance(offering)
        self.apply_hero_crop_to_instance(offering)
        if self.instructor is not None and not offering.instructor_id:
            offering.instructor = self.instructor
            if not offering.created_by_id:
                offering.created_by = self.instructor
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


class InstructorProfileForm(forms.ModelForm):
    class Meta:
        model = Instructor
        fields = ["display_name", "bio", "photo", "website", "social_handle"]


class ClassSessionForm(forms.ModelForm):
    class Meta:
        model = ClassSession
        fields = ["starts_at", "ends_at"]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def clean(self) -> dict:
        data = super().clean()
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


class _UserByLegalNameField(forms.ModelChoiceField):
    """ModelChoiceField that labels each User with their Member's full legal name."""

    def label_from_instance(self, obj) -> str:  # noqa: ANN001
        member = getattr(obj, "member", None)
        legal_name = (getattr(member, "full_legal_name", "") or "").strip() if member else ""
        full_name = obj.get_full_name().strip()
        return legal_name or full_name or obj.email or obj.username


class PromoteUserToInstructorForm(forms.Form):
    """Add an existing User as an Instructor."""

    user = _UserByLegalNameField(
        queryset=get_user_model().objects.none(),
        help_text="Pick the member to add as an instructor.",
    )
    display_name = forms.CharField(max_length=255, required=False, help_text="Defaults to the user's current name.")
    bio = forms.CharField(widget=forms.Textarea, required=False)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        from classes.models import Instructor

        User = get_user_model()
        already_instructors = Instructor.objects.values_list("user_id", flat=True)
        self.fields["user"].queryset = (
            User.objects.filter(is_active=True)
            .exclude(pk__in=already_instructors)
            .select_related("member")
            .order_by("member__full_legal_name", "email")
        )

    def save(self) -> Instructor:
        from classes.models import Instructor

        user = self.cleaned_data["user"]
        display_name = (self.cleaned_data.get("display_name") or user.get_full_name() or user.email).strip()

        base_slug = slugify(display_name) or f"instructor-{user.pk}"
        slug = base_slug
        n = 1
        while Instructor.objects.filter(slug=slug).exists():
            n += 1
            slug = f"{base_slug}-{n}"

        return Instructor.objects.create(
            user=user,
            display_name=display_name,
            slug=slug,
            bio=self.cleaned_data.get("bio", ""),
        )


class DiscountCodeForm(forms.ModelForm):
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

    def clean(self) -> dict:
        data = super().clean()
        if not data.get("discount_pct") and not data.get("discount_fixed_cents"):
            raise forms.ValidationError("Set either a percent OR a fixed-cents discount.")
        return data


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
        data = super().clean()
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
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.offering = offering
        self.settings_obj = settings_obj
        self.member = member
        self.client_ip = client_ip
        self._validated_discount: DiscountCode | None = None
        if not offering.requires_model_release:
            # Hide model release fields entirely when the class doesn't need them.
            self.fields.pop("model_release_signature")
            self.fields.pop("accepts_model_release")
        self._custom_questions = list(RegistrationQuestion.objects.filter(is_active=True))
        self._inject_custom_question_fields()

    def _inject_custom_question_fields(self) -> None:
        """Add one dynamic form field per active RegistrationQuestion.

        Field name pattern: ``custom_q_<pk>``. Type is mapped from the
        question's ``question_type``. Required flag follows the question's
        ``is_required``. choices_json drives the options for SINGLE_CHOICE.
        """
        for q in self._custom_questions:
            field_name = f"custom_q_{q.pk}"
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
        raw = (self.cleaned_data.get("discount_code") or "").strip().upper()
        if not raw:
            return None
        try:
            code = DiscountCode.objects.get(code=raw)
        except DiscountCode.DoesNotExist:
            raise forms.ValidationError("That discount code isn't recognized.") from None
        if not code.is_currently_valid():
            raise forms.ValidationError("That discount code isn't valid right now.")
        self._validated_discount = code
        return code

    def clean(self) -> dict:
        data = super().clean()
        if self.offering.spots_remaining <= 0:
            raise forms.ValidationError("This class is sold out.")
        if self.offering.requires_model_release and not data.get("accepts_model_release"):
            self.add_error("accepts_model_release", "Model release acceptance is required for this class.")
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


class InstructorEmailForm(forms.Form):
    """Form for an instructor to send a manual email to selected registrants of one of their classes.

    Recipient selection is bounded to ``Registration.objects.filter(class_offering__instructor=instructor)``
    so the form will not accept registration IDs outside the instructor's own
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

    def __init__(self, *args, instructor: Instructor, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.instructor = instructor
        self.fields["registration_ids"].queryset = Registration.objects.filter(
            class_offering__instructor=instructor,
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
        # All selected regs share the same class_offering only if the instructor
        # is sending to a single class. We anchor the message to the first
        # registration's class — typical UX is one class at a time.
        offering = registrations[0].class_offering
        bcc_emails = [r.email for r in registrations]
        bcc_self = self.cleaned_data.get("bcc_self", True)
        instructor_email = (self.instructor.user.email or "").strip()
        to_addresses = [instructor_email] if instructor_email else []
        if (
            bcc_self
            and instructor_email
            and instructor_email not in bcc_emails
            and instructor_email not in to_addresses
        ):
            bcc_emails.append(instructor_email)

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
                instructor=self.instructor,
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
