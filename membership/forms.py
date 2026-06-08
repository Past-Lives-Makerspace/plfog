"""Forms for the membership app."""

from __future__ import annotations

from typing import Any

from django import forms
from django.core.exceptions import ValidationError

from core.models import Invite

from .models import Member


class MemberAdminForm(forms.ModelForm):
    """Admin form for Member with optional User creation."""

    create_user = forms.BooleanField(
        required=False,
        label="Create login immediately",
        help_text="Creates a User account so this person can log in right away.",
    )

    class Meta:
        model = Member
        fields = "__all__"


class InviteMemberForm(forms.Form):
    """Form for inviting a new member by email."""

    email = forms.EmailField(help_text="The person will receive a signup link at this address.")

    def clean_email(self) -> str:
        email = self.cleaned_data["email"]
        if Member.objects.filter(_pre_signup_email__iexact=email).exclude(status=Member.Status.INVITED).exists():
            raise ValidationError("A member with this email already exists.")
        if Invite.objects.filter(email__iexact=email, accepted_at__isnull=True).exists():
            raise ValidationError("A pending invite for this email already exists.")
        return email


class AddEmailAliasForm(forms.Form):
    """Admin form for adding an email alias to a linked member's User.

    Lives here rather than in plfog/ because email/user identity is a
    membership-domain concern. Validation rules:

    1. Email must not already exist on this user (case-insensitive).
    2. Email must not already exist on any other user (allauth unique-email
       handling is the ultimate guard, but we check first for a nicer message).

    THREE-EMAIL-STORE NOTE: This form only operates on allauth.EmailAddress.
    It never touches Member._pre_signup_email or MemberEmail staging rows.
    See docs/superpowers/specs/2026-04-07-user-email-aliases-design.md.
    """

    email = forms.EmailField(
        label="Email address",
        help_text="The new alias. It will be created verified and non-primary.",
    )

    def __init__(self, *args: Any, user: Any, **kwargs: Any) -> None:
        self._user = user
        super().__init__(*args, **kwargs)

    def clean_email(self) -> str:
        from allauth.account.models import EmailAddress

        email = self.cleaned_data["email"].lower()
        if EmailAddress.objects.filter(user=self._user, email__iexact=email).exists():
            raise ValidationError("This address is already on this member.")
        if EmailAddress.objects.filter(email__iexact=email).exclude(user=self._user).exists():
            raise ValidationError("This address is already tied to a different account.")
        return email
