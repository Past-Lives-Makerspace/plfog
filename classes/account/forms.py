"""Forms for the /account/ dashboard on book.pastlives.space."""

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model

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
