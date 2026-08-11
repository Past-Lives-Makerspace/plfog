"""Core app forms."""

from __future__ import annotations

from django.conf import settings
from django.contrib.sites.models import Site
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

        from core import email as core_email

        core_email.send(
            to=member.primary_email,
            subject="Your Past Lives Account",
            trigger_kind="core.find_account",
            text_body=(
                f"Hi {member.preferred_name or member.full_legal_name},\n\n"
                f"Welcome back! Your account email is: {member.primary_email}\n\n"
                f"You can log in here:\n{login_url}\n\n"
                f"If you didn't request this, you can safely ignore this email."
            ),
        )


# TEMPORARY — remove on/after 2026-08-10. Copy-review gallery anonymous comments.
# See docs/superpowers/plans/2026-07-27-copy-review-comments.md.
class CopyReviewCommentForm(forms.Form):
    """Validation for a copy-review gallery comment (create + edit share it).

    Every field is required and non-blank once surrounding whitespace is stripped.
    Length caps (``body`` 2000, ``author_name`` 80, ``section`` 200) are the abuse
    guard. Validation lives here, never in the view. TEMPORARY — remove on/after
    2026-08-10.
    """

    section = forms.CharField(max_length=200, required=False)
    author_name = forms.CharField(max_length=80, required=False)
    body = forms.CharField(max_length=2000, required=False)

    def clean_section(self) -> str:
        return self._require("section")

    def clean_author_name(self) -> str:
        return self._require("author_name")

    def clean_body(self) -> str:
        return self._require("body")

    def _require(self, field: str) -> str:
        """Return the stripped value, rejecting anything blank after strip.

        Fields are declared ``required=False`` so the field layer accepts an empty
        string and defers the non-blank rule to here — otherwise the field's own
        "required" error would fire first and this branch would be unreachable.
        """
        value = self.cleaned_data[field].strip()
        if not value:
            raise forms.ValidationError("This field is required.")
        return value
