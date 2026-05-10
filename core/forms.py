"""Core app forms."""

from __future__ import annotations

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.mail import send_mail

from django import forms
from django.db import models

from membership.models import Member


class NewsletterSignupForm(forms.Form):
    """Standalone newsletter signup — anyone can subscribe to the audience."""

    email = forms.EmailField(label="Email address")
    first_name = forms.CharField(max_length=100, required=False, label="First name (optional)")
    last_name = forms.CharField(max_length=100, required=False, label="Last name (optional)")

    def subscribe(self) -> bool:
        """Push the form data to Mailchimp. Returns True on success.

        Returns False when Mailchimp is disabled (no api_key/list_id) or when
        the subscribe call fails. The view turns this into a user-facing error.
        """
        from core.integrations.mailchimp import MailchimpClient

        client = MailchimpClient.from_site_config()
        if not client.enabled:
            return False
        return client.subscribe(
            email=self.cleaned_data["email"],
            first_name=self.cleaned_data.get("first_name", ""),
            last_name=self.cleaned_data.get("last_name", ""),
            tags=["newsletter"],
        )


class FindAccountForm(forms.Form):
    """Look up a member by name and send a login link to the email on file."""

    name = forms.CharField(
        max_length=255,
        help_text="Enter your full legal name or preferred name.",
    )

    def send_login_email(self) -> None:
        """If a matching member exists, send a login link to their email on file."""
        name = self.cleaned_data["name"].strip()
        member = (
            Member.objects.filter(status=Member.Status.ACTIVE)
            .filter(
                models.Q(full_legal_name__iexact=name) | models.Q(preferred_name__iexact=name),
            )
            .first()
        )
        if member is None or not member.primary_email:
            return

        current_site = Site.objects.get_current()
        protocol = "https" if not settings.DEBUG else "http"
        login_url = f"{protocol}://{current_site.domain}/accounts/login/"

        send_mail(
            subject="Your Past Lives Account",
            message=(
                f"Hi {member.preferred_name or member.full_legal_name},\n\n"
                f"Your account email is: {member.primary_email}\n\n"
                f"You can log in here:\n{login_url}\n\n"
                f"If you didn't request this, you can safely ignore this email."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[member.primary_email],
        )
