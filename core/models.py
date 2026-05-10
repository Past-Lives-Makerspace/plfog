"""Core app models for PWA push notification infrastructure and site configuration."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.mail import send_mail
from django.db import models
from django.utils import timezone


class PushSubscription(models.Model):
    """Stores Web Push subscription data for a user."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    endpoint = models.URLField(max_length=500, unique=True)
    p256dh = models.CharField(max_length=100)
    auth = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user.email} - {self.endpoint[:50]}..."


class SiteConfiguration(models.Model):
    """Singleton model for site-wide settings like registration mode."""

    class RegistrationMode(models.TextChoices):
        OPEN = "open", "Open"
        INVITE_ONLY = "invite_only", "Invite Only"

    registration_mode = models.CharField(
        "New User Registration Mode",
        max_length=20,
        choices=RegistrationMode.choices,
        default=RegistrationMode.INVITE_ONLY,
        help_text="Open — anyone can sign up. Invite Only — only people with an invite can register.",
    )
    general_calendar_url = models.URLField(
        blank=True,
        default="",
        verbose_name="General Calendar iCal URL",
        help_text="Public iCal URL for the general makerspace calendar. Paste the 'Secret address in iCal format' from Google Calendar settings.",
    )
    general_calendar_color = models.CharField(
        max_length=7,
        blank=True,
        default="#EEB44B",
        verbose_name="General Calendar Color",
        help_text="Hex color for general makerspace events on the Community Calendar (e.g. #EEB44B).",
    )
    general_calendar_last_fetched_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the general calendar was last synced. Set by the calendar service.",
    )
    sync_classes_enabled = models.BooleanField(
        default=False,
        verbose_name="Sync classes from classes.pastlives.space",
        help_text="When enabled, upcoming classes are imported into the Community Calendar with links to register.",
    )
    classes_calendar_color = models.CharField(
        max_length=7,
        blank=True,
        default="#7C5CBF",
        verbose_name="Classes Calendar Color",
        help_text="Hex color for classes from classes.pastlives.space on the Community Calendar (e.g. #7C5CBF).",
    )
    classes_last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When classes were last synced from classes.pastlives.space. Set by the calendar service.",
    )
    mailchimp_api_key = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="MailChimp API key",
        help_text="MailChimp API key used for auto-subscribe on class registration and other integrations. Leave blank to disable.",
    )
    mailchimp_list_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="MailChimp list / audience ID",
        help_text="MailChimp list (audience) ID new subscribers are added to.",
    )
    google_analytics_measurement_id = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Google Analytics measurement ID",
        help_text="GA4 measurement ID (e.g. G-XXXXXXX) — injected site-wide (excludes the Django admin). Leave blank to disable.",
    )

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def __str__(self) -> str:
        return "Site Settings"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Force singleton by always using pk=1."""
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> SiteConfiguration:
        """Load the singleton instance, creating it with defaults if needed."""
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj


class Invite(models.Model):
    """Tracks email invitations sent by admins for invite-only registration."""

    email = models.EmailField(unique=True, help_text="Email address of the person being invited.")
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The admin user who sent this invite.",
    )
    member = models.OneToOneField(
        "membership.Member",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invite",
        help_text="The pre-created Member record for this invite.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the invite was created.")
    accepted_at = models.DateTimeField(null=True, blank=True, help_text="When the invite was accepted by signing up.")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        status = "pending" if self.is_pending else "accepted"
        return f"Invite for {self.email} ({status})"

    @property
    def is_pending(self) -> bool:
        """Return True if the invite has not been accepted yet."""
        return self.accepted_at is None

    def mark_accepted(self) -> None:
        """Mark this invite as accepted with the current timestamp."""
        self.accepted_at = timezone.now()
        self.save(update_fields=["accepted_at"])

    @classmethod
    def create_and_send(cls, email: str, invited_by: Any) -> Invite:
        """Create an invite with a pre-created Member placeholder and send the email.

        Args:
            email: The email address to invite.
            invited_by: The admin User sending the invite.

        Returns:
            The created Invite instance.

        Raises:
            ValueError: If email already has an active member or pending invite, or no MembershipPlan exists.
        """
        from membership.models import Member, MembershipPlan

        if Member.objects.filter(_pre_signup_email__iexact=email).exclude(status=Member.Status.INVITED).exists():
            raise ValueError(f"A member with email {email} already exists.")

        if cls.objects.filter(email__iexact=email, accepted_at__isnull=True).exists():
            raise ValueError(f"A pending invite for {email} already exists.")

        plan = MembershipPlan.objects.order_by("pk").first()
        if plan is None:
            raise ValueError("Cannot invite: no membership plan exists yet.")

        member = Member.objects.create(
            _pre_signup_email=email,
            full_legal_name=email,
            membership_plan=plan,
            status=Member.Status.INVITED,
        )

        invite = cls.objects.create(email=email, invited_by=invited_by, member=member)
        invite.send_invite_email()
        return invite

    def send_invite_email(self) -> None:
        """Send a plaintext invite email with a signup link."""
        from urllib.parse import urlencode

        from django.contrib.sites.models import Site

        current_site = Site.objects.get_current()
        protocol = "https" if not settings.DEBUG else "http"
        query = urlencode({"email": self.email})
        signup_url = f"{protocol}://{current_site.domain}/accounts/signup/?{query}"

        send_mail(
            subject="You're invited to Past Lives Makerspace",
            message=(
                f"You've been invited to join Past Lives Makerspace!\n\n"
                f"Click the link below to create your account:\n\n"
                f"{signup_url}\n\n"
                f"If you didn't expect this invite, you can ignore this email."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[self.email],
        )
