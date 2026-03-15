"""Forms for the member hub."""

from __future__ import annotations

from django import forms

from membership.models import Member


class ProfileSettingsForm(forms.ModelForm):
    """Form for editing member profile fields."""

    class Meta:
        model = Member
        fields = ["preferred_name", "phone"]
        widgets = {
            "preferred_name": forms.TextInput(attrs={"placeholder": "How should we call you?"}),
            "phone": forms.TextInput(attrs={"placeholder": "(optional)"}),
        }


class EmailPreferencesForm(forms.Form):
    """Form for email notification preferences."""

    voting_results = forms.BooleanField(required=False, label="Voting Result Emails")
