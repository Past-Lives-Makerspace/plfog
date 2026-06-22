"""Core app models for PWA push notification infrastructure and site configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

if TYPE_CHECKING:
    from classes.models import Registration


class HeroCropMixin(models.Model):
    """Adds hero_crop_* fields and a hero_object_position property to a model.

    Subclasses must implement get_hero_image_field_name() to return the string
    name of the ImageField (e.g. "image", "banner_image", "hero_image").
    """

    hero_crop_x = models.PositiveIntegerField(
        null=True, blank=True, help_text="Crop box left edge in source-image pixels — set by the hero cropper."
    )
    hero_crop_y = models.PositiveIntegerField(
        null=True, blank=True, help_text="Crop box top edge in source-image pixels — set by the hero cropper."
    )
    hero_crop_w = models.PositiveIntegerField(
        null=True, blank=True, help_text="Crop box width in source-image pixels — set by the hero cropper."
    )
    hero_crop_h = models.PositiveIntegerField(
        null=True, blank=True, help_text="Crop box height in source-image pixels — set by the hero cropper."
    )

    class Meta:
        abstract = True

    def get_hero_image_field_name(self) -> str:
        raise NotImplementedError("Subclasses must implement get_hero_image_field_name()")

    @property
    def hero_object_position(self) -> str:
        """CSS ``object-position`` value to keep the cropped focal point centered.

        If width/height are missing but x/y are present, they are treated as
        direct 0-100 percentages (Focal Point mode).

        Otherwise, returns ``"X% Y%"`` where the coords are the crop-box center
        expressed as a percentage of the source image. pair this with
        ``object-fit: cover`` on the hero <img>/banner.
        Returns ``"50% 50%"`` (CSS default) when unknown.
        """
        if self.hero_crop_x is not None and self.hero_crop_y is not None:
            if not self.hero_crop_w or not self.hero_crop_h:
                # Focal Point mode
                return f"{self.hero_crop_x}% {self.hero_crop_y}%"

        if not (self.hero_crop_w and self.hero_crop_h):
            return "50% 50%"

        field_name = self.get_hero_image_field_name()
        image_field = getattr(self, field_name, None)
        if not image_field or not getattr(image_field, "name", None):
            return "50% 50%"

        try:
            src_w = image_field.width
            src_h = image_field.height
        except (FileNotFoundError, ValueError, AttributeError, OSError):
            return "50% 50%"

        if not (src_w and src_h):
            return "50% 50%"

        cx = (self.hero_crop_x or 0) + self.hero_crop_w / 2
        cy = (self.hero_crop_y or 0) + self.hero_crop_h / 2
        return f"{(cx / src_w) * 100:.1f}% {(cy / src_h) * 100:.1f}%"


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
        verbose_name="Show class catalog on the Community Calendar",
        help_text="When enabled, upcoming classes from our catalog appear on the Community Calendar, each linking to its class page.",
    )
    classes_calendar_color = models.CharField(
        max_length=7,
        blank=True,
        default="#7C5CBF",
        verbose_name="Classes Calendar Color",
        help_text="Hex color for class events on the Community Calendar (e.g. #7C5CBF).",
    )
    classes_last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When class events were last refreshed onto the Community Calendar. Set by the calendar service.",
    )
    legacy_cms_sync_enabled = models.BooleanField(
        default=False,
        verbose_name="Sync offerings from legacy CMS",
        help_text="When enabled, classes.pastlives.space offerings sync into plfog automatically each morning.",
    )
    legacy_cms_last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        verbose_name="Legacy CMS last synced at",
        help_text="Timestamp of the last successful legacy CMS sync.",
    )
    legacy_cms_last_sync_duration = models.FloatField(
        null=True,
        blank=True,
        editable=False,
        verbose_name="Legacy CMS last sync duration (seconds)",
        help_text="Wall-clock seconds the last successful legacy CMS sync took. Used to estimate progress.",
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


class CalendarFeed(models.Model):
    """A named iCal feed displayed on the Community Calendar.

    Multiple feeds (e.g. "General Calendar", "Workshops", "Open Studio") can be
    configured from the Site Settings → Calendar tab. Each is fetched on demand
    by ``hub.calendar_service`` and rendered as its own legend entry.
    """

    name = models.CharField(
        max_length=100,
        help_text="Display name shown on the Community Calendar legend (e.g. 'General Calendar', 'Workshops').",
    )
    ical_url = models.URLField(
        help_text="Public iCal URL. Paste the 'Secret address in iCal format' from Google Calendar settings.",
    )
    color = models.CharField(
        max_length=7,
        default="#EEB44B",
        help_text="Hex color for this feed's events on the Community Calendar (e.g. #EEB44B).",
    )
    last_fetched_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this feed was last synced. Set by the calendar service.",
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text="Lower values appear first on the legend.",
    )

    class Meta:
        ordering = ["sort_order", "pk"]
        verbose_name = "Calendar Feed"
        verbose_name_plural = "Calendar Feeds"

    def __str__(self) -> str:
        return self.name


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
        member = self.member
        SiteActivity.log(
            SiteActivity.Kind.INVITE_ACCEPTED,
            actor=member.user if member is not None else None,
            target=member,
        )
        if self.invited_by is not None:
            from core import notifications

            notifications.dispatch(
                "invite_accepted",
                [self.invited_by],
                title="Your invite was accepted",
                body="Someone you invited has joined Past Lives.",
                url="/members/",
            )

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
        SiteActivity.log(SiteActivity.Kind.MEMBER_INVITED, actor=invited_by, payload={"email": email})
        return invite

    def send_invite_email(self) -> None:
        """Send a plaintext invite email with a signup link."""
        from urllib.parse import urlencode

        from django.contrib.sites.models import Site

        current_site = Site.objects.get_current()
        protocol = "https" if not settings.DEBUG else "http"
        query = urlencode({"email": self.email})
        signup_url = f"{protocol}://{current_site.domain}/accounts/signup/?{query}"

        from core import email as core_email

        core_email.send(
            to=self.email,
            subject="You're invited to Past Lives Makerspace",
            trigger_kind="core.invite",
            text_body=(
                f"You've been invited to join Past Lives Makerspace!\n\n"
                f"Click the link below to create your account:\n\n"
                f"{signup_url}\n\n"
                f"If you didn't expect this invite, you can ignore this email."
            ),
        )


class UserProfile(models.Model):
    """Per-user profile fields not covered by Django's User model.

    Holds the data the book.pastlives.space /account/profile/ page edits
    plus the onboarding wizard answers. Members are read-only on /profile/;
    their canonical profile lives on Member and is edited at members.pastlives.space.
    """

    class FirstAttendance(models.TextChoices):
        FIRST_TIME = "first_time", "First time"
        RETURNING = "returning", "Returning"
        EVENT_ONLY = "event_only", "Event only, no class"
        UNKNOWN = "unknown", "Can't remember"

    class Referral(models.TextChoices):
        FRIEND = "friend", "Friend or family"
        INSTAGRAM = "instagram", "Instagram"
        GOOGLE = "google", "Google"
        EVENT = "event", "Open studio / event"
        MAIN_SITE = "main_site", "Past Lives main site"
        OTHER = "other", "Somewhere else"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        help_text="The user this profile belongs to.",
    )
    preferred_name = models.CharField(max_length=100, blank=True, help_text="Preferred name on rosters.")
    pronouns = models.CharField(
        max_length=50,
        blank=True,
        help_text="Pronouns shown on roster sheets and confirmation emails.",
    )
    phone = models.CharField(
        max_length=20,
        blank=True,
        help_text="Day-of contact phone — only used if an instructor needs to reach you.",
    )
    first_attendance_status = models.CharField(
        max_length=20,
        choices=FirstAttendance.choices,
        blank=True,
        help_text="Self-reported on first signup. Used for welcome flows.",
    )
    referral_source = models.CharField(
        max_length=20,
        choices=Referral.choices,
        blank=True,
        help_text="Self-reported referral source. Aggregated for marketing analysis.",
    )
    interest_category_slugs = models.JSONField(
        default=list,
        blank=True,
        help_text="List of Category slugs the user opted into for new-class email notifications.",
    )
    custom_question_answers = models.JSONField(
        default=dict,
        blank=True,
        help_text="Remembered answers to the CMS registration questions, keyed by question id (as a string).",
    )
    accessibility_note = models.TextField(
        blank=True,
        help_text="Free-text accessibility note from onboarding step 3.",
    )
    onboarding_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Stamp set when the user finishes (or skips through) the 3-step onboarding.",
    )
    subscribed_to_mailchimp_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Stamp set when the account-signup push to Mailchimp succeeded.",
    )
    completed_tour_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Cached from Simplybook; refreshed by the tour-status sync.",
    )
    tour_status_checked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time Simplybook was polled for this user's tour status.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Profile for {self.user.email}"

    @property
    def is_onboarded(self) -> bool:
        return self.onboarding_completed_at is not None

    def cache_from_registration(self, registration: Registration) -> None:
        """Seed empty profile fields from a class registration's overlapping answers.

        Only fills fields that are currently blank — a registration never
        overwrites a value the user has already set. Maps exactly the fields
        with a clean 1:1 semantic match (pronouns, phone); see the onboarding
        pre-fill plan for the full mapping rationale.
        """
        mapping = {"pronouns": registration.pronouns, "phone": registration.phone}
        changed: list[str] = []
        for field_name, incoming in mapping.items():
            if not getattr(self, field_name) and incoming:
                setattr(self, field_name, incoming)
                changed.append(field_name)
        if changed:
            self.save(update_fields=changed)

    def set_custom_answers(self, answers: dict[int, str]) -> None:
        """Remember the user's answers to CMS registration questions for pre-fill next time.

        Merges the incoming answers over whatever is already stored — a later
        registration updates a changed answer but never wipes an unrelated one.
        Empty/blank answers are ignored so a skipped optional question doesn't
        clobber a previously remembered value. Keys are stored as strings because
        JSON object keys are always strings.

        Args:
            answers: Map of RegistrationQuestion id to the answer text.
        """
        merged = dict(self.custom_question_answers)
        changed = False
        for question_id, text in answers.items():
            if text in (None, ""):
                continue
            key = str(question_id)
            if merged.get(key) != text:
                merged[key] = text
                changed = True
        if changed:
            self.custom_question_answers = merged
            self.save(update_fields=["custom_question_answers", "updated_at"])


class TransactionalEmailLog(models.Model):
    """One row per transactional email attempted — sent or failed.

    Written by ``core.email.send()`` on every attempt so the admin Email Log
    tab can audit whether confirmation/receipt emails are actually going out.
    """

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    to_email = models.CharField(max_length=254, help_text="Recipient(s); comma-joined when multiple.")
    subject = models.CharField(max_length=500, help_text="Email subject line.")
    trigger_kind = models.CharField(max_length=100, help_text="Which workflow sent it, e.g. 'billing.receipt'.")
    status = models.CharField(max_length=10, choices=Status.choices, help_text="Send outcome.")
    error_message = models.TextField(blank=True, default="", help_text="Exception text when status=failed.")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_status_display()} → {self.to_email} ({self.trigger_kind})"


class SiteActivity(models.Model):
    """Append-only event log for every meaningful site-wide happening.

    Written via ``SiteActivity.log()`` from each workflow point (auth,
    profile, voting, billing, classes, membership) so the admin
    ``/manage/activity/`` feed shows one chronological stream. ``payload``
    carries free-form per-kind detail; ``email_log`` links to the email that
    this event triggered, if any.
    """

    class Kind(models.TextChoices):
        LOGIN = "login", "Logged in"
        LOGOUT = "logout", "Logged out"
        PROFILE_UPDATED = "profile_updated", "Updated profile"
        VOTE_SUBMITTED = "vote_submitted", "Submitted vote"
        VOTE_CHANGED = "vote_changed", "Changed vote"
        TAB_CHARGED = "tab_charged", "Tab charged"
        TAB_CHARGE_FAILED = "tab_charge_failed", "Tab charge failed"
        TAB_ENTRY_ADDED = "tab_entry_added", "Tab entry added"
        CLASS_REGISTERED = "class_registered", "Registered for class"
        CLASS_REGISTRATION_CANCELLED = "class_registration_cancelled", "Cancelled registration"
        CLASS_WAITLIST_JOINED = "class_waitlist_joined", "Joined waitlist"
        CLASS_PUBLISHED = "class_published", "Class published"
        CLASS_SUBMITTED = "class_submitted", "Class submitted"
        CLASS_APPROVED = "class_approved", "Class approved"
        CLASS_CANCELLED = "class_cancelled", "Class cancelled"
        REFUND_ISSUED = "refund_issued", "Refund issued"
        FUNDING_SNAPSHOT_TAKEN = "funding_snapshot_taken", "Funding snapshot taken"
        MEMBER_INVITED = "member_invited", "Member invited"
        INVITE_ACCEPTED = "invite_accepted", "Invite accepted"
        MEMBER_SIGNUP = "member_signup", "Member signed up"
        GUILD_ANNOUNCEMENT = "guild_announcement", "Guild announcement"
        ORIENTATION_REQUESTED = "orientation_requested", "Orientation requested"
        ORIENTATION_CONFIRMED = "orientation_confirmed", "Orientation confirmed"
        ORIENTATION_DECLINED = "orientation_declined", "Orientation declined"
        ORIENTATION_CANCELLED = "orientation_cancelled", "Orientation cancelled"
        ORIENTATION_COMPLETED = "orientation_completed", "Orientation completed"
        GUILD_JOINED = "guild_joined", "Joined a guild"
        LEASE_ACTIVATED = "lease_activated", "Lease activated"
        SITE_ANNOUNCEMENT = "site_announcement", "Site announcement"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="User who triggered this. Null for system events.",
    )
    kind = models.CharField(max_length=50, choices=Kind.choices, help_text="What happened.")
    target_ct = models.ForeignKey(
        ContentType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Content type of the related object, when applicable.",
    )
    target_id = models.PositiveIntegerField(null=True, blank=True, help_text="PK of the related object.")
    target = GenericForeignKey("target_ct", "target_id")
    payload = models.JSONField(default=dict, blank=True, help_text="Free-form per-kind detail.")
    email_log = models.ForeignKey(
        "core.TransactionalEmailLog",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activity",
        help_text="The email this event sent, if any. Source of the ✉ badge.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["kind", "-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
        ]
        verbose_name_plural = "Site activity"

    def __str__(self) -> str:
        return f"{self.get_kind_display()} @ {self.created_at:%Y-%m-%d %H:%M}"

    @classmethod
    def log(
        cls,
        kind: str,
        *,
        actor: Any | None = None,
        target: models.Model | None = None,
        email_log: Any | None = None,
        payload: dict[str, Any] | None = None,
    ) -> "SiteActivity":
        """Append one activity row. Safe to call from views, signals, or model methods."""
        activity = cls(
            kind=kind,
            actor=actor if (actor is not None and getattr(actor, "pk", None)) else None,
            email_log=email_log,
            payload=payload or {},
        )
        if target is not None:
            activity.target = target
        activity.save()
        return activity


class Notification(models.Model):
    """One in-app bell entry for one user. Always created on dispatch (non-optional)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    trigger = models.CharField(max_length=40, help_text="Trigger key from core.triggers.")
    title = models.CharField(max_length=200, help_text="Bold headline shown in the bell.")
    body = models.CharField(max_length=500, help_text="One-line detail.")
    url = models.CharField(max_length=500, blank=True, default="", help_text="Where clicking navigates.")
    read_at = models.DateTimeField(null=True, blank=True, help_text="Set when the user reads it.")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "read_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} → {self.user.email}"

    @property
    def is_unread(self) -> bool:
        return self.read_at is None

    def mark_read(self) -> None:
        if self.read_at is None:
            self.read_at = timezone.now()
            self.save(update_fields=["read_at"])


class NotificationPreference(models.Model):
    """Per-user, per-trigger push/email opt-in. Absent row → trigger defaults apply."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_prefs")
    trigger = models.CharField(max_length=40, help_text="Trigger key from core.triggers.")
    push_enabled = models.BooleanField(default=False, help_text="Send browser push for this trigger.")
    email_enabled = models.BooleanField(default=False, help_text="Send email for this trigger.")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "trigger"], name="uq_notificationpreference_user_trigger"),
        ]

    def __str__(self) -> str:
        return f"{self.user.email}:{self.trigger} (push={self.push_enabled}, email={self.email_enabled})"


class ScheduledNotificationMarker(models.Model):
    """Idempotency guard for time-based notification jobs.

    A unique ``key`` records that a given scheduled notification already fired,
    e.g. "voting_closing:2026-06" or "lease_expiring:42". Jobs get_or_create
    the key and skip when it already exists.
    """

    key = models.CharField(max_length=120, unique=True, help_text="Stable per-notification idempotency key.")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.key


class KnownLoginSignature(models.Model):
    """Records (user, signature) pairs already seen, to detect new-device logins."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="login_signatures")
    signature = models.CharField(max_length=64, help_text="Hash of (user-agent, IP).")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "signature"], name="uq_loginsignature_user_signature"),
        ]
