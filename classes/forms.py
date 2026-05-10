"""Forms for the Classes app."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django import forms
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory
from django.utils.text import slugify

from classes.models import (
    Category,
    ClassOffering,
    ClassSession,
    ClassSettings,
    DiscountCode,
    Instructor,
    Registration,
    Waiver,
)

if TYPE_CHECKING:
    from membership.models import Member


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
        if cleaned.get("price_cents") in (None, ""):
            self.add_error(  # type: ignore[attr-defined]
                "price_cents", "Set a price (in cents) or check 'This is a free class / workshop'."
            )

    def apply_is_free_to_instance(self, offering: ClassOffering) -> None:
        if self.cleaned_data.get("is_free"):  # type: ignore[attr-defined]
            offering.price_cents = 0
            offering.member_discount_pct = 0


class ClassOfferingForm(_FreeClassMixin, forms.ModelForm):
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

    def clean(self) -> dict:
        data = super().clean()
        self.clean_is_free_pricing()
        return data

    def save(self, commit: bool = True) -> ClassOffering:
        offering = super().save(commit=False)
        self.apply_is_free_to_instance(offering)
        if commit:
            offering.save()
            self.save_m2m()
        return offering


class InstructorClassOfferingForm(_FreeClassMixin, forms.ModelForm):
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

    def clean(self) -> dict:
        data = super().clean()
        self.clean_is_free_pricing()
        return data

    def save(self, commit: bool = True) -> ClassOffering:
        offering = super().save(commit=False)
        self.apply_is_free_to_instance(offering)
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
        return registration

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
