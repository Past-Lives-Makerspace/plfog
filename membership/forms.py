"""Forms for the membership app."""

from __future__ import annotations

from typing import Any

from django import forms
from django.core.exceptions import ValidationError

from core.models import Invite

from .models import Member, MembershipPlan


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


class AddMemberForm(forms.Form):
    """Create a member directly, without the invite-plus-email flow.

    Staff use this to add someone straight to the roster. No invite is created and
    no email is sent; the person signs in later with a passwordless login code (an
    ACTIVE member is given a login-ready account automatically on save, and any
    other status has its User auto-created from this email on first sign-in — see
    ``plfog.adapters.AutoCreateUserLoginCodeForm``).
    """

    full_legal_name = forms.CharField(
        max_length=255,
        label="Full legal name",
        help_text="The name on their membership record.",
        error_messages={"required": "Enter the member's full legal name."},
    )
    email = forms.EmailField(
        label="Email",
        help_text="Where their one-time login code will be sent. No invite email goes out.",
    )
    membership_plan = forms.ModelChoiceField(
        queryset=MembershipPlan.objects.all(),
        label="Membership plan",
        help_text="Which plan this member is on.",
    )
    preferred_name = forms.CharField(
        max_length=255,
        required=False,
        label="Preferred name",
        help_text="What they like to be called, if different (optional).",
    )
    status = forms.ChoiceField(
        choices=Member.Status.choices,
        initial=Member.Status.ACTIVE,
        label="Status",
        help_text="Membership status. Active members get a login-ready account right away.",
    )

    def clean_email(self) -> str:
        email = self.cleaned_data["email"]
        if Member.objects.filter(_pre_signup_email__iexact=email).exclude(status=Member.Status.INVITED).exists():
            raise ValidationError("A member with this email already exists.")
        return email

    def create_member(self) -> Member:
        """Create and return the Member from validated data.

        Must be called only after ``is_valid()``. The email is stored on
        ``_pre_signup_email``; an ACTIVE member is auto-provisioned a linked,
        passwordless User by the ``auto_provision_member_user`` signal (silently,
        no email), so no invite flow is involved.
        """
        return Member.objects.create(
            full_legal_name=self.cleaned_data["full_legal_name"],
            _pre_signup_email=self.cleaned_data["email"],
            preferred_name=self.cleaned_data["preferred_name"],
            membership_plan=self.cleaned_data["membership_plan"],
            status=self.cleaned_data["status"],
        )


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
