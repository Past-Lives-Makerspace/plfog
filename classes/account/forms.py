"""Forms for the /account/ dashboard on book.pastlives.space."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from django import forms
from django.contrib.auth import get_user_model

if TYPE_CHECKING:
    from django.contrib.auth.models import User as UserType

    from classes.models import Registration

User = get_user_model()


class AccountProfileForm(forms.ModelForm):
    """Edits the User's name and the linked UserProfile's pronouns + phone.

    UserProfile is created on first save if it doesn't exist. The view is
    responsible for blocking POSTs from member-persona users — this form
    doesn't enforce read-only-ness on its own.
    """

    pronouns = forms.CharField(max_length=50, required=False)
    phone = forms.CharField(max_length=20, required=False)

    class Meta:
        model = User
        fields = ["first_name", "last_name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Initial values for the profile fields, if a profile exists.
        if self.instance and self.instance.pk:
            profile = getattr(self.instance, "profile", None)
            if profile is not None:
                self.fields["pronouns"].initial = profile.pronouns
                self.fields["phone"].initial = profile.phone

    def save(self, commit: bool = True) -> UserType:
        user = super().save(commit=commit)
        from core.models import UserProfile

        UserProfile.objects.update_or_create(
            user=user,
            defaults={
                "pronouns": self.cleaned_data.get("pronouns", ""),
                "phone": self.cleaned_data.get("phone", ""),
            },
        )
        return user


class LookupForm(forms.Form):
    """Guest lookup by last name + confirmation order number.

    Last name is matched case-insensitively. Order number is normalized to
    uppercase and validated against the `PL-XXXX-YY` shape. Tests cover
    cross-match prevention (a Sandoval querying with a Smith order number
    must read as "not found", never accidentally show Smith's booking).
    """

    last_name = forms.CharField(max_length=100, label="Last name")
    order_number = forms.CharField(max_length=12, label="Order number")

    _PATTERN = re.compile(r"^PL-[A-HJ-NP-Z2-9]{4}-\d{2}$")

    def clean_order_number(self) -> str:
        value = self.cleaned_data["order_number"].strip().upper()
        if not self._PATTERN.match(value):
            raise forms.ValidationError("Order number should look like PL-XXXX-YY.")
        return value

    def clean_last_name(self) -> str:
        return self.cleaned_data["last_name"].strip()

    def find(self) -> "Registration | None":
        from classes.models import Registration

        return Registration.objects.filter(
            last_name__iexact=self.cleaned_data["last_name"],
            order_number=self.cleaned_data["order_number"],
        ).first()


class OnboardingStep1Form(forms.Form):
    """First-time-here-or-have-we-met? — single radio choice."""

    first_attendance_status = forms.ChoiceField(choices=[], widget=forms.RadioSelect)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import UserProfile

        self.fields["first_attendance_status"].choices = UserProfile.FirstAttendance.choices


class OnboardingStep2Form(forms.Form):
    """About you — preferred name, pronouns, phone, referral source."""

    preferred_name = forms.CharField(max_length=100, required=False)
    pronouns = forms.CharField(max_length=50, required=False)
    phone = forms.CharField(max_length=20, required=False)
    referral_source = forms.ChoiceField(
        choices=[],
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import UserProfile

        self.fields["referral_source"].choices = [("", "Pick one (optional)")] + list(UserProfile.Referral.choices)


class _OpenMultipleChoiceField(forms.MultipleChoiceField):
    """MultipleChoiceField that accepts any submitted value.

    Used for interest category slugs — the template only renders valid choices,
    and we don't want form validation to reject values simply because the DB
    queryset returned a different set (e.g. in tests).
    """

    def valid_value(self, value: str) -> bool:  # type: ignore[override]
        return True


class OnboardingStep3Form(forms.Form):
    """Interest chips + free-text accessibility note."""

    interest_category_slugs = _OpenMultipleChoiceField(
        choices=[],
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    accessibility_note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from classes.models import Category

        self.fields["interest_category_slugs"].choices = [
            (c.slug, c.name) for c in Category.objects.order_by("sort_order", "name")
        ]
