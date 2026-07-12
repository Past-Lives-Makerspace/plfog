"""Core app models for PWA push notification infrastructure and site configuration."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models.functions import Coalesce
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

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

    class MemberEventPolicy(models.TextChoices):
        APPROVAL = "approval", "Members can propose (needs review)"  # default
        OPEN = "open", "Members can post directly"
        DISABLED = "disabled", "Only leads and admins can post"

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
    discord_general_webhook_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="#general-chat Discord webhook",
        help_text="Discord webhook for #general-chat. Guild leads can post announcements here. "
        "Blank = the option is hidden from the picker.",
    )
    discord_leadership_webhook_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="#leadership Discord webhook",
        help_text="Discord webhook for #leadership. Blank = the option is hidden from the picker.",
    )
    discord_officers_webhook_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="#guild-officers Discord webhook",
        help_text="Discord webhook for #guild-officers. Blank = the option is hidden from the picker.",
    )
    discord_server_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name="Discord server (guild) id",
        help_text="The Discord server id used for role assignment (outbound guild sync). "
        "Blank disables the whole two-way guild sync.",
    )
    discord_role_message_channel_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name="Reaction-role channel id",
        help_text="The channel id of the reaction-role message members react on. "
        "Blank disables the inbound reaction sync.",
    )
    discord_role_message_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name="Reaction-role message id",
        help_text="The id of the reaction-role message — update if it's reposted. "
        "Blank disables the inbound reaction sync.",
    )
    google_analytics_measurement_id = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Google Analytics measurement ID",
        help_text="GA4 measurement ID (e.g. G-XXXXXXX) — injected site-wide (excludes the Django admin). Leave blank to disable.",
    )
    tab_payments_enabled = models.BooleanField(
        default=True,
        verbose_name="Enable My Tab & Payments",
        help_text="When off, hides My Tab, the balance pill, the Buyables tab on guild pages, "
        "and the admin Payments/Reports nav. Members visiting the Tab pages are redirected.",
    )
    class_registration_enabled = models.BooleanField(
        default=True,
        verbose_name="Allow class registration",
        help_text="When off, the public Register button is disabled (with the note below) and "
        "the registration form refuses sign-ups.",
    )
    class_registration_disabled_note = models.TextField(
        blank=True,
        default="Online registration is paused right now. Email info@pastlives.space and we'll help you sign up.",
        verbose_name="Registration-off message",
        help_text="Shown under the disabled Register button when class registration is off.",
    )
    member_event_policy = models.CharField(
        max_length=20,
        choices=MemberEventPolicy.choices,
        default=MemberEventPolicy.APPROVAL,
        help_text=(
            "Who can create Community Calendar events, and whether a member's event needs review "
            "before it's published. Leads, staff, and admins always post directly."
        ),
    )
    general_google_calendar_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="General Google Calendar ID",
        help_text=(
            "Google Calendar ID for site-wide community events (Calendar Settings → Integrate calendar → "
            "Calendar ID — NOT the iCal URL). Blank keeps site-wide events in FOG only."
        ),
    )
    google_calendar_sync_enabled = models.BooleanField(
        default=False,
        verbose_name="Push events to Google Calendar",
        help_text=(
            "When on (and the Google service account is configured), publishing/editing/deleting an "
            "event updates the linked Google Calendar."
        ),
    )
    signage_default_slide_seconds = models.PositiveIntegerField(
        default=12,
        verbose_name="Default slide duration (seconds)",
        help_text="Default seconds each slide shows, unless a slide overrides it.",
    )
    signage_show_events = models.BooleanField(
        default=True,
        verbose_name="Show upcoming events on screens",
        help_text="Automatically add slides for upcoming site-wide events.",
    )
    signage_event_days_ahead = models.PositiveIntegerField(
        default=30,
        verbose_name="Event look-ahead (days)",
        help_text="How many days ahead to pull upcoming events for the slideshow.",
    )
    signage_event_qr = models.BooleanField(
        default=False,
        verbose_name="Add a QR to event slides",
        help_text="Add a QR code to the community calendar on auto event slides.",
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


class InviteManager(models.Manager["Invite"]):
    """Querysets for the Manage Members invites panel — all date/window math lives here."""

    def _expiry_cutoff(self) -> datetime:
        """The moment before which an un-accepted invite reads as expired."""
        return timezone.now() - timedelta(days=settings.INVITE_EXPIRY_DAYS)

    def outstanding(self) -> models.QuerySet[Invite]:
        """Un-accepted invites (pending + expired), newest first, joins prefetched."""
        return self.filter(accepted_at__isnull=True).select_related("invited_by", "member")

    def pending(self) -> models.QuerySet[Invite]:
        """Un-accepted invites last sent inside the expiry window."""
        sent = Coalesce("last_sent_at", "created_at")
        return self.outstanding().annotate(_sent=sent).filter(_sent__gte=self._expiry_cutoff())

    def expired(self) -> models.QuerySet[Invite]:
        """Un-accepted invites whose most-recent send is older than the expiry window."""
        sent = Coalesce("last_sent_at", "created_at")
        return self.outstanding().annotate(_sent=sent).filter(_sent__lt=self._expiry_cutoff())

    def for_management_panel(self) -> models.QuerySet[Invite]:
        """What the Invites card shows: all un-accepted + accepted within the last 30 days."""
        recent_accept = timezone.now() - timedelta(days=30)
        return self.filter(
            models.Q(accepted_at__isnull=True) | models.Q(accepted_at__gte=recent_accept)
        ).select_related("invited_by", "member")


class Invite(models.Model):
    """Tracks email invitations sent by admins for invite-only registration."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        EXPIRED = "expired", "Expired"

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
    last_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the invite email was most recently sent. Resending updates this; expiry is measured from it.",
    )

    objects = InviteManager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        status = "pending" if self.is_pending else "accepted"
        return f"Invite for {self.email} ({status})"

    @property
    def is_pending(self) -> bool:
        """Return True if the invite has not been accepted yet."""
        return self.accepted_at is None

    @property
    def sent_at(self) -> datetime:
        """The timestamp the UI means by 'sent' — most-recent send, else creation."""
        return self.last_sent_at or self.created_at

    @property
    def is_expired(self) -> bool:
        """Un-accepted and last sent longer ago than the expiry window (advisory only)."""
        if not self.is_pending:
            return False
        cutoff = timezone.now() - timedelta(days=settings.INVITE_EXPIRY_DAYS)
        return self.sent_at < cutoff

    @property
    def status(self) -> str:
        """Derived lifecycle state: accepted / expired / pending."""
        if not self.is_pending:
            return self.Status.ACCEPTED
        return self.Status.EXPIRED if self.is_expired else self.Status.PENDING

    @property
    def status_label(self) -> str:
        """Human label for the derived status (templates can't call Status(...) with an arg)."""
        return self.Status(self.status).label

    def revoke(self) -> None:
        """Cancel an un-accepted invite: remove it and its bare placeholder member.

        Raises:
            ValueError: if the invite was already accepted (the person is now a real member).
        """
        from membership.models import Member

        if not self.is_pending:
            raise ValueError("Cannot revoke an invite that has already been accepted.")
        member = self.member
        email = self.email
        self.delete()
        # Clean up ONLY a placeholder this flow created itself: a bare INVITED stub with no
        # linked user AND no Airtable origin. create_and_send REUSES a pre-existing INVITED
        # placeholder pulled from Airtable; Members are read-only from Airtable by contract,
        # so the `not member.airtable_record_id` guard keeps revoke from ever deleting an
        # imported stub — it just detaches the (now-deleted) invite.
        if (
            member is not None
            and member.user_id is None
            and member.status == Member.Status.INVITED
            and not member.airtable_record_id
        ):
            member.delete()
        SiteActivity.log(SiteActivity.Kind.MEMBER_INVITE_REVOKED, payload={"email": email})

    def mark_accepted(self) -> None:
        """Mark this invite as accepted with the current timestamp."""
        self.accepted_at = timezone.now()
        self.save(update_fields=["accepted_at"])
        member = self.member

        from core.events.emit import emit

        # emit logs the INVITE_ACCEPTED SiteActivity (registry activity_kind=
        # "invite_accepted") with actor=member.user and target=member, and resolves
        # the recipient via INVITER → self.invited_by (yielding no recipient, and so
        # no notification, when invited_by is None).
        emit(
            "invite_accepted",
            actor=member.user if member is not None else None,
            target=member,
            context={"invite": self},
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

        # Reuse an existing invited placeholder (e.g. pulled from Airtable) instead of
        # creating a duplicate Member for the same email. Only reuse one with no invite
        # already attached, so the Invite.member one-to-one never collides.
        member = (
            Member.objects.filter(
                _pre_signup_email__iexact=email,
                status=Member.Status.INVITED,
                invite__isnull=True,
            )
            .order_by("pk")
            .first()
        )
        if member is None:
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
        """Emit the ``member.invited`` event — a forced, DB-editable invite email.

        The invitee has no account yet, so this is an email-only forced send routed to
        the raw ``self.email`` (the ``member.invited`` event resolves to no per-user
        recipient and carries the address as ``email_to``). The subject/body come from
        the DB-editable copy catalogue; only the signup URL is computed here.

        An invite is an explicit, intentional admin action — re-inviting (or re-sending)
        MUST always send a fresh email, exactly as the old inline sender did. So each
        send uses a unique idempotency ``period`` (a microsecond timestamp): the
        EventDelivery ledger never dedupes one invite send against another. (Dedupe on
        the address would silently swallow a deliberate re-invite.)
        """
        from urllib.parse import urlencode

        from django.contrib.sites.models import Site

        from core.events.emit import emit

        current_site = Site.objects.get_current()
        protocol = "https" if not settings.DEBUG else "http"
        query = urlencode({"email": self.email})
        signup_url = f"{protocol}://{current_site.domain}/accounts/signup/?{query}"

        # Stamp the send so the panel's "sent N ago" and is_expired derive from the
        # most-recent send — this is what makes Resend reset the badge/age.
        self.last_sent_at = timezone.now()
        self.save(update_fields=["last_sent_at"])

        emit(
            "member.invited",
            target=self.member,
            context={"invitee_email": self.email, "signup_url": signup_url},
            email_to=self.email,
            period=f"invite:{timezone.now():%Y%m%d%H%M%S%f}",
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
        MEMBER_INVITE_REVOKED = "member_invite_revoked", "Member invite revoked"
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


class NotificationQuerySet(models.QuerySet["Notification"]):
    """Querysets for the member Notifications page — filters live here, not in the view."""

    def for_user(self, user: AbstractBaseUser) -> NotificationQuerySet:
        """This user's notifications, newest-first (relies on Meta.ordering)."""
        return self.filter(user=user)

    def unread(self) -> NotificationQuerySet:
        """Only notifications the user hasn't read yet."""
        return self.filter(read_at__isnull=True)


class Notification(models.Model):
    """One in-app bell entry for one user. Always created on dispatch (non-optional)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    trigger = models.CharField(max_length=40, help_text="Trigger key from core.triggers.")
    title = models.CharField(max_length=200, help_text="Bold headline shown in the bell.")
    body = models.CharField(max_length=500, help_text="One-line detail.")
    url = models.CharField(max_length=500, blank=True, default="", help_text="Where clicking navigates.")
    read_at = models.DateTimeField(null=True, blank=True, help_text="Set when the user reads it.")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = NotificationQuerySet.as_manager()

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
    """Per-user, per-(event, channel) opt-in/out. Absent row → the event's channel default applies.

    The design's unified per-channel preference shape (§2.7): one row per
    ``(user, event_key, channel)``. This generalizes the old two-boolean
    ``push_enabled`` / ``email_enabled`` columns so new channels (scheduled email,
    digest, Discord) are first-class — a preference is now just *"does this user want
    this event on this channel?"*. The data migration that introduced this shape
    fanned each old row into one EMAIL + one PUSH row, preserving every opt-in/out.

    ``enabled`` records the user's explicit choice. A FORCED channel ignores it (the
    event is always delivered); for a non-forced channel an absent row means the
    event's channel default decides (``ON`` → opt-in, ``OFF`` → opted out).
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_prefs")
    event_key = models.CharField(max_length=60, help_text="Event key from core.events.registry.")
    channel = models.CharField(max_length=20, help_text="Channel key from core.events.registry.Channel.")
    enabled = models.BooleanField(default=False, help_text="Whether this user receives this event on this channel.")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "event_key", "channel"],
                name="uq_notificationpreference_user_event_channel",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "event_key"], name="idx_notifpref_user_event"),
        ]

    def __str__(self) -> str:
        state = "on" if self.enabled else "off"
        return f"{self.user.email}:{self.event_key}/{self.channel}={state}"


class EventDelivery(models.Model):
    """Idempotency ledger for the event spine (design §2.5).

    One row records that a given event was delivered to a given target on a given
    channel within a given period. The unique key
    ``(event_key, target_ref, channel, period)`` makes
    :func:`core.events.emit.emit` safe to re-run from schedulers without
    double-delivering: the emitter ``get_or_create``s the row and skips the
    channel send when the row already existed.

    Folds in the three legacy dedupe patterns (``ScheduledNotificationMarker``,
    ``RegistrationReminder``, and the orientation ``is_completed``-as-dedupe) — they
    migrate onto this one model in a later phase. ``period`` is a free-form bucket:
    ``""`` for one-shot events (dedupe forever), or a window label like
    ``"2026-06"`` / ``"2026-06-24"`` for recurring scheduled events.

    The ``status`` field carries the digest buffer (design §2.4, #9). A row created
    by the normal fan-out is ``SENT`` (it both deduped and delivered). The
    :class:`core.events.channels.DigestAdapter` instead writes a ``PENDING`` row
    carrying the rendered message (``title`` / ``body`` / ``url``); the digest flush
    (Phase 5 scheduler) reads the pending rows, batches them into one email, and
    flips them to ``SENT``. The dedupe semantics are unchanged for the existing
    channels — they never write a ``PENDING`` row.
    """

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        PENDING = "pending", "Pending (buffered for digest)"

    event_key = models.CharField(max_length=60, help_text="The EventType key that was delivered.")
    target_ref = models.CharField(
        max_length=120,
        help_text="Stable string identifying who/what this delivery was for (e.g. 'user:42', 'booking:7').",
    )
    channel = models.CharField(max_length=20, help_text="The channel key the event was delivered on.")
    period = models.CharField(
        max_length=40,
        blank=True,
        default="",
        help_text="Dedupe window bucket — empty for one-shot, else e.g. '2026-06' for monthly.",
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.SENT,
        help_text="SENT once delivered; PENDING while buffered for a later digest flush.",
    )
    title = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Buffered message title (digest rows only).",
    )
    body = models.TextField(blank=True, default="", help_text="Buffered message body (digest rows only).")
    url = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Buffered click-through URL (digest rows only).",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event_key", "target_ref", "channel", "period"],
                name="uq_eventdelivery_event_target_channel_period",
            ),
        ]
        indexes = [
            models.Index(fields=["event_key", "period"], name="idx_eventdelivery_event_period"),
            models.Index(
                fields=["status", "target_ref"],
                name="idx_eventdelivery_pending",
                condition=models.Q(status="pending"),
            ),
        ]

    def __str__(self) -> str:
        suffix = f"@{self.period}" if self.period else ""
        return f"{self.event_key}→{self.target_ref}[{self.channel}]{suffix}"


class NotificationTemplate(models.Model):
    """Admin-editable authored copy for one ``(event, channel)`` pair (design §2.3).

    Phase 3 (Decision 6): all subject/body copy lives in the DB so the copy team
    edits everything in an admin catalogue — code only **seeds** initial values
    (see :mod:`core.events.copy` + the ``seed_notification_templates`` command).
    One row per ``(event_key, channel)`` that needs authored copy.

    Rendering is a constrained merge-field substitution over a documented per-event
    context (:mod:`core.events.rendering`), never raw Django template execution from
    the DB. ``is_overridden`` is set when the copy team edits a row; the seed command
    preserves overridden rows so re-seeding never clobbers authored copy.
    """

    event_key = models.CharField(max_length=60, help_text="The EventType key this copy belongs to.")
    channel = models.CharField(max_length=20, help_text="The channel key this copy is rendered for (email, in_app, …).")
    subject = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="The subject / title line, with {{ merge_field }} placeholders.",
    )
    body_text = models.TextField(
        blank=True,
        default="",
        help_text="The plain-text body, with {{ merge_field }} placeholders.",
    )
    body_html = models.TextField(
        blank=True,
        default="",
        help_text="The HTML body, with {{ merge_field }} placeholders (autoescaped on render).",
    )
    is_overridden = models.BooleanField(
        default=False,
        help_text="True once the copy team has edited this row; the seed command then preserves it.",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="edited_notification_templates",
        help_text="The user who last edited this copy (null for seeded rows).",
    )
    updated_at = models.DateTimeField(auto_now=True, help_text="When this copy was last written.")

    class Meta:
        ordering = ["event_key", "channel"]
        constraints = [
            models.UniqueConstraint(
                fields=["event_key", "channel"],
                name="uq_notificationtemplate_event_channel",
            ),
        ]

    def __str__(self) -> str:
        flag = " (overridden)" if self.is_overridden else ""
        return f"{self.event_key}[{self.channel}]{flag}"

    def snapshot_version(self) -> "NotificationTemplateVersion":
        """Append a :class:`NotificationTemplateVersion` capturing the CURRENT copy.

        Call this *before* mutating the row so the prior copy is preserved for the
        history + revert UI. Returns the created version row.
        """
        return NotificationTemplateVersion.objects.create(
            template=self,
            subject=self.subject,
            body_text=self.body_text,
            body_html=self.body_html,
            edited_by=self.updated_by,
        )

    def apply_edit(self, *, subject: str, body_text: str, body_html: str, editor: Any | None) -> None:
        """Snapshot the prior copy, then write the new copy as an admin override.

        Versioning is automatic: every edit snapshots the row's current copy into a
        :class:`NotificationTemplateVersion` first, so the history + revert surface
        always has the prior state. Marks the row ``is_overridden`` so the seed
        command will not clobber it.
        """
        self.snapshot_version()
        self.subject = subject
        self.body_text = body_text
        self.body_html = body_html
        self.is_overridden = True
        self.updated_by = editor if (editor is not None and getattr(editor, "pk", None)) else None
        self.save(update_fields=["subject", "body_text", "body_html", "is_overridden", "updated_by", "updated_at"])

    def revert_to(self, version: "NotificationTemplateVersion", *, editor: Any | None) -> None:
        """Restore the copy captured in ``version`` (snapshotting current first).

        Reverting is itself an edit: the current copy is snapshotted before the
        older copy is restored, so a revert is undoable in turn. The row stays
        ``is_overridden`` (a revert is a deliberate copy-team action).

        Raises:
            ValueError: If ``version`` belongs to a different template.
        """
        if version.template_id != self.pk:
            raise ValueError("Cannot revert to a version that belongs to a different template.")
        self.apply_edit(
            subject=version.subject,
            body_text=version.body_text,
            body_html=version.body_html,
            editor=editor,
        )


class NotificationTemplateVersion(models.Model):
    """One immutable snapshot of a :class:`NotificationTemplate`'s copy (design §2.3).

    Every edit snapshots the prior copy here so the admin catalogue can show history
    and revert. ``django-simple-history`` is not in the project (checked), so this is
    a hand-rolled, append-only version table — simpler and dependency-free.
    """

    template = models.ForeignKey(
        NotificationTemplate,
        on_delete=models.CASCADE,
        related_name="versions",
        help_text="The template this snapshot belongs to.",
    )
    subject = models.CharField(max_length=255, blank=True, default="", help_text="Subject as it was at snapshot time.")
    body_text = models.TextField(blank=True, default="", help_text="Text body as it was at snapshot time.")
    body_html = models.TextField(blank=True, default="", help_text="HTML body as it was at snapshot time.")
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notification_template_versions",
        help_text="The user whose edit this snapshot captures (null for seeded copy).",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["template", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.template.event_key}[{self.template.channel}] @ {self.created_at:%Y-%m-%d %H:%M}"


class DiscordWebhookRoute(models.Model):
    """DB-backed Discord event→webhook routing override (design §2.4, Decision 9).

    Phase 2 left ``core.events.discord.EVENT_WEBHOOK_OVERRIDES`` as an in-code seam;
    Phase 3 makes it DB-backed + admin-editable. One row maps an ``event_key`` to a
    specific webhook URL; :func:`core.events.discord.webhook_for_event` reads these
    rows first and falls back to the global webhook when none matches (or when the
    row is disabled). A blank ``webhook_url`` disables Discord for that event even
    when a global webhook is configured.
    """

    event_key = models.CharField(
        max_length=60,
        unique=True,
        help_text="The EventType key this route applies to (one route per event).",
    )
    webhook_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="The Discord webhook this event posts to. Blank = Discord disabled for this event.",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="When off, this route is ignored and the event falls back to the global webhook.",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="edited_discord_routes",
        help_text="The user who last edited this route.",
    )
    updated_at = models.DateTimeField(auto_now=True, help_text="When this route was last written.")

    class Meta:
        ordering = ["event_key"]

    def __str__(self) -> str:
        state = "on" if self.is_enabled else "off"
        return f"{self.event_key} → discord ({state})"

    @property
    def effective_webhook(self) -> str:
        """The webhook this route contributes, or ``""`` when it should not apply.

        An enabled route with a non-blank URL overrides the global webhook. A
        disabled route contributes nothing (the caller falls back to global). An
        enabled-but-blank route is a deliberate *disable* and is handled by the
        caller via :meth:`overrides_global`.
        """
        if not self.is_enabled:
            return ""
        return (self.webhook_url or "").strip()

    @property
    def overrides_global(self) -> bool:
        """Whether this row should override the global webhook (incl. a blank disable).

        An enabled route always overrides the global default — a non-blank URL
        redirects the post; a blank URL deliberately silences Discord for the event.
        A disabled route does not override (fall back to global).
        """
        return self.is_enabled

    @classmethod
    def save_routing(
        cls, *, event_key: str, submitted_url: str, is_enabled: bool, editor: Any | None
    ) -> DiscordWebhookRoute:
        """Create or update the routing row for ``event_key`` (blank URL keeps the stored one).

        The webhook URL field is write-only and blank-on-load on the edit form, so a
        blank ``submitted_url`` means "keep the existing URL" — toggling ``is_enabled``
        must never wipe a configured webhook — while a non-blank value replaces it.

        Args:
            event_key: The EventType key this route applies to.
            submitted_url: The URL from the form; blank keeps the stored one.
            is_enabled: Whether the route is active.
            editor: The user who made the change, recorded as ``updated_by`` (dropped
                when it has no pk, e.g. an anonymous or system caller).

        Returns:
            The created or updated route.
        """
        existing = cls.objects.filter(event_key=event_key).first()
        new_url = submitted_url if submitted_url else (existing.webhook_url if existing is not None else "")
        route, _ = cls.objects.update_or_create(
            event_key=event_key,
            defaults={
                "webhook_url": new_url,
                "is_enabled": is_enabled,
                "updated_by": editor if (editor is not None and getattr(editor, "pk", None)) else None,
            },
        )
        return route
