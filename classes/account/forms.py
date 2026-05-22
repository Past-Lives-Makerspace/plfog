"""Forms for the /account/ dashboard on book.pastlives.space."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from django import forms
from django.contrib.auth import get_user_model

if TYPE_CHECKING:
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

    def save(self, commit: bool = True) -> User:
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
