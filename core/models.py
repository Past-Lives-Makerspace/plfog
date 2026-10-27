"""Core app models for PWA push notification infrastructure and site configuration."""

from __future__ import annotations

import json
import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MaxValueValidator, MinValueValidator, StepValueValidator
from django.db import models, transaction
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.features import DEFAULT_SOON_MESSAGE, FEATURES, FeatureState, FeatureView
from core.files import delete_orphan_on_replace
from core.scheduled_jobs import Trigger
from core.validators import validate_hex_color, validate_image_size

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.contrib.auth.models import AbstractBaseUser, User
    from django.core.files.uploadedfile import UploadedFile

    from classes.models import Registration

logger = logging.getLogger(__name__)

# The four columns the composer's crop box (or the Adjust tool's focal point) lives in. A
# model's ``save()`` reads them from the stored row to tell whether the box moved.
HERO_CROP_FIELDS = ("hero_crop_x", "hero_crop_y", "hero_crop_w", "hero_crop_h")


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
    def hero_crop_box(self) -> tuple[int | None, int | None, int | None, int | None]:
        """The four crop columns as one value, so a row can be compared with its stored state."""
        return (self.hero_crop_x, self.hero_crop_y, self.hero_crop_w, self.hero_crop_h)

    def scale_hero_crop(self, factor: float) -> None:
        """Shrink or grow a pixel crop box to follow a resize of its source image.

        The hero cropper measures the photo the browser showed. When ``save()`` then
        downsizes a fresh upload to the long-edge cap, the box has to follow or the
        stored centre points at the wrong part of the stored photo. Focal-point crops
        (x and y as percentages, no width or height) and unset crops are left alone,
        and a box never rounds below one pixel.
        """
        if not (self.hero_crop_w and self.hero_crop_h):
            return
        self.hero_crop_x = round((self.hero_crop_x or 0) * factor)
        self.hero_crop_y = round((self.hero_crop_y or 0) * factor)
        self.hero_crop_w = max(1, round(self.hero_crop_w * factor))
        self.hero_crop_h = max(1, round(self.hero_crop_h * factor))

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

    def focal_point_on_source(self, x_pct: int, y_pct: int) -> tuple[int, int]:
        """A focal point picked on the photo the page shows, in the stored file's coordinates.

        The Adjust tool reads its point as percentages of the photo on screen. Here that
        photo is the stored file itself, so the point comes back unchanged; a model whose
        page shows a derived picture (a class and its cropped copy) overrides this.
        """
        return x_pct, y_pct


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


class FcmDevice(models.Model):
    """Firebase Cloud Messaging registration token for a user's native (Capacitor) device.

    The native-app counterpart to :class:`PushSubscription`: web push cannot reach the
    Android/iOS WebView, so the app registers an FCM token here and :class:`core.events.channels.PushAdapter`
    fans out to both. Delivery lives in :mod:`core.fcm`.
    """

    class Platform(models.TextChoices):
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, help_text="The member this device belongs to."
    )
    token = models.CharField(max_length=512, unique=True, help_text="FCM registration token for this device.")
    platform = models.CharField(
        max_length=10,
        choices=Platform.choices,
        default=Platform.ANDROID,
        help_text="Native platform the token came from.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the device first registered.")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the token was last refreshed.")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user.email} - {self.get_platform_display()} - {self.token[:16]}..."


class InvalidBiometricCredential(Exception):
    """Raised when a biometric secret cannot be exchanged for a session.

    Every failure — unknown, expired, revoked, replayed — raises this one type, and the
    view turns all of them into the same 401 with the same message. Telling the caller
    *which* it was would let an attacker probe which secrets exist.
    """


# 48 bytes of urlsafe entropy. The secret is a bearer token, not a password, so the only
# defense that matters is that it cannot be guessed.
BIOMETRIC_SECRET_BYTES = 48
# A phone that stops being used stops working rather than staying valid forever. Pushed
# forward on every redeem, so an in-use device never hits it.
BIOMETRIC_TTL_DAYS = 90
# How long after a rotation the superseded secret is still accepted. See `redeem`.
BIOMETRIC_ROTATION_GRACE_SECONDS = 60

# NAMING IS LOAD-BEARING BELOW. Every variable, parameter, and attribute holding a raw
# biometric secret is called exactly `secret`, never `raw_secret` or `new_secret` or
# anything else descriptive.
#
# Sentry runs with send_default_pii=True, so an unhandled 500 raised anywhere in this call
# stack ships the frame locals. Its scrubber matches denylist entries against the WHOLE key
# (EventScrubber.scrub_dict does `k.lower() in self.denylist`), not as a substring, and the
# default denylist contains "secret" — so a local named `secret` is redacted and a local
# named `raw_secret` is transmitted in full. A rename here for readability would silently
# start leaking live bearer tokens to a third party, with nothing failing to warn you.
# tests/core/biometric_credential_spec.py pins this.


def hash_biometric_secret(secret: str) -> str:
    """SHA-256 hex digest of a raw biometric secret.

    A deliberately slow password hash (bcrypt/argon2) would buy nothing here: the input is
    :data:`BIOMETRIC_SECRET_BYTES` of ``secrets.token_urlsafe`` entropy, not a human-chosen
    password, so there is no dictionary to run against it. What the hash IS for is that a
    database leak yields no usable secrets.

    Args:
        secret: The raw secret as handed to the device.

    Returns:
        The 64-character lowercase hex digest stored on the row.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class BiometricCredentialManager(models.Manager["BiometricCredential"]):
    """All of the biometric credential logic: issue, redeem, and revoke."""

    def issue(self, user: User, *, device_label: str, platform: str) -> tuple[BiometricCredential, str]:
        """Mint a new credential for one device and hand back its raw secret.

        The raw secret is returned to exactly one caller, once. It is never stored and
        never logged — only :func:`hash_biometric_secret` of it lands in the database.

        Args:
            user: The already-authenticated member enrolling this device.
            device_label: Member-visible name for the phone. Client supplied, untrusted.
            platform: One of :class:`BiometricCredential.Platform` values.

        Returns:
            The stored credential and the raw secret the device must keep.
        """
        secret = secrets.token_urlsafe(BIOMETRIC_SECRET_BYTES)
        now = timezone.now()
        credential = self.create(
            user=user,
            secret_hash=hash_biometric_secret(secret),
            device_label=device_label,
            platform=platform,
            expires_at=now + timedelta(days=BIOMETRIC_TTL_DAYS),
        )
        return credential, secret

    def redeem(self, secret: str) -> tuple[User, str]:
        """Exchange a raw secret for its member and a freshly rotated secret.

        The state machine, in the order it is checked:

        1. **Hit on ``secret_hash``** — the normal path. Rotate and return the new secret.
        2. **Hit on ``previous_secret_hash``, within the grace window** — the app redeemed,
           the server rotated, and the reply never arrived, so the app retried with the only
           secret it has. That is a dropped response, not an attack: rotate again and keep
           the credential alive. Without this branch a single lost reply on a phone network
           permanently breaks that member's biometric login.
        3. **Hit on ``previous_secret_hash``, after the grace window** — a spent secret is
           being replayed, which is the signature of a copied credential. Revoke it, warn,
           and raise. The member re-enrols with a login code; the copy is worthless.
        4. **No hit** — raise.

        Args:
            secret: The secret the device read out of its Keychain/Keystore.

        Returns:
            The member to log in, and the new raw secret the device must store.

        Raises:
            InvalidBiometricCredential: On every failure. Never returns ``None``.
        """
        if not secret:
            raise InvalidBiometricCredential("No biometric secret supplied.")

        digest = hash_biometric_secret(secret)
        now = timezone.now()

        # select_for_update pins the row for the read-modify-write below so two redeems
        # landing together cannot both rotate off the same secret. It is a no-op on SQLite
        # (local dev and tests) and a real row lock on the Postgres that runs production.
        with transaction.atomic():
            current = self.select_for_update().filter(secret_hash=digest).first()
            if current is not None:
                if current.revoked_at is not None:
                    raise InvalidBiometricCredential("This biometric credential was revoked.")
                if current.expires_at <= now:
                    raise InvalidBiometricCredential("This biometric credential expired.")
                return current.user, current.rotate(now)

            superseded = self.select_for_update().filter(previous_secret_hash=digest).first()
            if superseded is None:
                raise InvalidBiometricCredential("Unknown biometric credential.")
            if superseded.revoked_at is not None:
                raise InvalidBiometricCredential("This biometric credential was revoked.")
            if superseded.expires_at <= now:
                raise InvalidBiometricCredential("This biometric credential expired.")

            rotated_at = superseded.rotated_at
            within_grace = (
                rotated_at is not None and (now - rotated_at).total_seconds() <= BIOMETRIC_ROTATION_GRACE_SECONDS
            )
            if within_grace:
                # keep_previous: the caller presented the SPENT secret, so that hash has to stay
                # recognizable. Sliding the window here would let a thief erase replay detection
                # by redeeming twice inside it. See BiometricCredential.rotate.
                return superseded.user, superseded.rotate(now, keep_previous=True)

            replayed = superseded

        # Past the grace window this is a replay of a secret that was already spent.
        #
        # The revoke happens OUT HERE, after the atomic block has committed, on purpose. It
        # is followed by a raise, and a raise inside the block would roll the revoke back
        # with it — leaving the stolen credential live, which is the exact opposite of what
        # detecting a replay is for. Every other branch above either returns or raises
        # without having written anything, so a rollback there costs nothing.
        #
        # The device label identifies which phone; the secret itself is never logged.
        self.revoke(replayed)
        logger.warning(
            "Biometric credential replay: spent secret reused for user pk=%s, device %r. Credential revoked.",
            replayed.user_id,
            replayed.device_label,
        )
        raise InvalidBiometricCredential("This biometric credential was already used.")

    def active_for(self, user: User) -> models.QuerySet[BiometricCredential]:
        """This member's usable credentials — not revoked, not expired — newest first."""
        return self.filter(user=user, revoked_at__isnull=True, expires_at__gt=timezone.now()).order_by("-created_at")

    def revoke(self, credential: BiometricCredential) -> None:
        """Kill one credential. Idempotent — re-revoking keeps the original timestamp."""
        if credential.revoked_at is not None:
            return
        credential.revoked_at = timezone.now()
        credential.save(update_fields=["revoked_at"])

    def revoke_all(self, user: User) -> None:
        """Kill every live credential this member has, on every device."""
        self.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())


class BiometricCredential(models.Model):
    """A device-bound bearer secret that exchanges a biometric verify for a session.

    The phone holds the secret in the Keychain/Keystore behind Face ID or a fingerprint;
    the server holds only its SHA-256. Redeeming it logs the member in and rotates the
    secret, so any given secret is single use.

    The biometric never authenticates to the server. It gates local access to the secret,
    and the server trusts the secret and nothing else.

    KNOWN LIMIT ON REPLAY DETECTION, stated plainly because the obvious reading of the
    rotation is more reassuring than the truth. ``previous_secret_hash`` is a SINGLE slot,
    so the server remembers exactly one generation back. A copied secret is therefore only
    recognized as a replay while it is at most one rotation stale. Someone holding a stolen
    copy who simply redeems it twice pushes the original out of that slot, and the real
    device's next attempt then looks merely *unknown* rather than replayed: nothing is
    revoked, and the stolen credential stays live and renews its expiry on every use.

    What that costs is DETECTION, not containment. The victim's device is still kicked back
    to an emailed code, the stolen credential still shows up as a row under Signed In
    Devices where it can be revoked by hand, and getting the secret out of the Keychain or
    Keystore in the first place needs a compromised device or an extracted backup.

    Making detection exact needs a stable unguessable selector alongside the rotating
    verifier, so a credential can be identified independently of which generation of secret
    is presented, and any mismatch on a known credential is a replay. That is a design
    change rather than a patch, so it is deliberately not bolted on here.
    """

    class Platform(models.TextChoices):
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="biometric_credentials",
        help_text="The member this credential signs in.",
    )
    secret_hash = models.CharField(
        max_length=64, unique=True, help_text="SHA-256 hex of the live secret. The raw secret is never stored."
    )
    previous_secret_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="SHA-256 hex of the secret this one replaced. Used to tell a dropped reply from a replay.",
    )
    rotated_at = models.DateTimeField(
        null=True, blank=True, help_text="When the previous secret was superseded. Starts the 60 second grace window."
    )
    device_label = models.CharField(
        max_length=120, help_text="Member-visible name for the device, e.g. 'iPhone'. Client supplied, so untrusted."
    )
    platform = models.CharField(
        max_length=10,
        choices=Platform.choices,
        default=Platform.ANDROID,
        help_text="Native platform the credential was enrolled from.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the device enrolled.")
    last_used_at = models.DateTimeField(
        null=True, blank=True, help_text="When this credential last signed the member in."
    )
    expires_at = models.DateTimeField(help_text="When the credential stops working. Pushed forward on every use.")
    revoked_at = models.DateTimeField(
        null=True, blank=True, help_text="Set when the member, an admin, or a detected replay killed this credential."
    )

    objects = BiometricCredentialManager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        state = "active" if self.is_active else "inactive"
        return f"{self.device_label} - {self.get_platform_display()} - {state}"

    @property
    def is_active(self) -> bool:
        """Whether this credential can still be redeemed."""
        return self.revoked_at is None and self.expires_at > timezone.now()

    def rotate(self, now: datetime, *, keep_previous: bool = False) -> str:
        """Replace the live secret with a fresh one and return the raw replacement.

        The outgoing hash normally moves to ``previous_secret_hash`` so a retry of the request
        whose reply was lost is still recognized for
        :data:`BIOMETRIC_ROTATION_GRACE_SECONDS`. The expiry is pushed out from here, so a
        phone in regular use never ages out.

        ``keep_previous`` is for the grace-window redeem, and it is a security control, not a
        tidiness option. That branch is reached by presenting the ALREADY SPENT secret, so
        shifting the window would overwrite the spent hash with the live one and erase the only
        record that the spent secret ever existed. Anyone holding a stolen copy could then wipe
        replay detection by redeeming twice inside the window: the real device's next attempt
        would look merely unknown instead of replayed, so nothing would be revoked and the
        thief's credential would stay live and keep renewing its expiry.

        Pinning the window to the original rotation also fixes the honest case it mirrors, two
        dropped replies in a row, which would otherwise fail for exactly the same reason.

        Args:
            now: The redeem timestamp, so one redeem stamps every field identically.
            keep_previous: Leave ``previous_secret_hash`` and ``rotated_at`` untouched, so the
                spent secret stays recognizable and the grace window does not slide forward.

        Returns:
            The new raw secret. It is returned once and stored nowhere.
        """
        secret = secrets.token_urlsafe(BIOMETRIC_SECRET_BYTES)
        updated = ["secret_hash", "last_used_at", "expires_at"]
        if not keep_previous:
            self.previous_secret_hash = self.secret_hash
            self.rotated_at = now
            updated += ["previous_secret_hash", "rotated_at"]
        self.secret_hash = hash_biometric_secret(secret)
        self.last_used_at = now
        self.expires_at = now + timedelta(days=BIOMETRIC_TTL_DAYS)
        self.save(update_fields=updated)
        return secret


# The starting content of the #important-info "Important Links" embed — the pinned post's
# current live sections. Admins edit the copy on Site Settings → Discord; this constant only
# seeds the field (and backstops a blanked one) so the embed is never empty.
DISCORD_INFO_LINKS_DEFAULT = (
    "**🎨 Classes**\n"
    "Browse and sign up for upcoming classes:\n"
    "https://classes.pastlives.space/\n"
    "\n"
    "**🗓️ Calendar**\n"
    "Everything happening at the makerspace — events, guild meetings, studio hours:\n"
    "https://calendar.pastlives.space\n"
    "\n"
    "**🤝 Code of Conduct**\n"
    "The community agreements every member signs on to:\n"
    "https://docs.google.com/document/d/1avWCAnbwDbO79k-n-_QpUc0P2Dz-s6f4/edit\n"
    "\n"
    "**📱 Past Lives App (FOG)**\n"
    "Your member hub — profile, guilds, calendar, and more:\n"
    "https://pastlives.app"
)


# The Spotlight crawl speed's range (#942), in pixels a second: the column's validators and the slider's bounds.
SPOTLIGHT_CRAWL_SPEED_MIN = 10
SPOTLIGHT_CRAWL_SPEED_MAX = 80
SPOTLIGHT_CRAWL_SPEED_STEP = 5
SPOTLIGHT_CRAWL_SPEED_DEFAULT = 25


def default_signage_tour_url() -> str:
    """The Book a Tour slide's link for a new Site Settings row: the tours site (#950)."""
    return str(settings.TOURS_BASE_URL)


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
    member_agreement_required = models.BooleanField(
        default=False,
        verbose_name="Require members to accept the Member Agreement",
        help_text="When checked, active members must accept the agreement to access the hub.",
    )
    member_agreement_url = models.URLField(
        blank=True,
        default="",
        verbose_name="Member Agreement URL",
        help_text="URL to the Member Agreement (e.g., in the Knowledge Base).",
    )
    member_agreement_version = models.CharField(
        max_length=40,
        blank=True,
        default="",
        verbose_name="Member Agreement version",
        help_text=(
            "The released version members must have accepted, e.g. '2.0.0'. Leave blank to keep "
            "the one-time behaviour: anyone who has ever accepted is never asked again. Setting a "
            "version here re-prompts every member who accepted a different one, so change it only "
            "for a release that people genuinely need to re-read."
        ),
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
        help_text="Hex color for general makerspace events on the Calendar (e.g. #EEB44B).",
    )
    general_calendar_last_fetched_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the general calendar was last synced. Set by the calendar service.",
    )
    sync_classes_enabled = models.BooleanField(
        default=False,
        verbose_name="Show class catalog on the Calendar",
        help_text="When enabled, upcoming classes from our catalog appear on the Calendar, each linking to its class page.",
    )
    classes_calendar_color = models.CharField(
        max_length=7,
        blank=True,
        default="#7C5CBF",
        verbose_name="Classes Calendar Color",
        help_text="Hex color for class events on the Calendar (e.g. #7C5CBF).",
    )
    classes_last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When class events were last refreshed onto the Calendar. Set by the calendar service.",
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
    discord_reservations_webhook_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="#reservations Discord webhook",
        help_text="Discord webhook for #reservations. New equipment reservations post here "
        "automatically. Blank = reservations are not posted to Discord.",
    )
    discord_server_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name="Discord server (guild) id",
        help_text="The Discord server id used for role assignment (outbound guild sync). "
        "Blank disables the whole two-way guild sync.",
    )
    discord_member_role_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name="Discord Member role ID",
        help_text="The Discord role every member has; channels it cannot see are never read. "
        "Blank: Discord pings are not queued for Spotlight.",
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
        help_text="GA4 measurement ID (e.g. G-XXXXXXX) — injected on every page, this admin included. Leave blank to disable.",
    )
    late_cancel_fees_enabled = models.BooleanField(
        default=False,
        verbose_name="Charge late cancellation fees",
        help_text=(
            "When on, guilds and equipment can set a fee for cancelling an orientation or reservation "
            "inside the notice window. Off means nothing charges anywhere."
        ),
    )
    late_cancel_notice_hours = models.PositiveSmallIntegerField(
        default=24,
        verbose_name="Cancellation notice (hours)",
        help_text="How far ahead members are told to cancel. Shown wherever a fee applies.",
    )
    late_cancel_grace_hours = models.PositiveSmallIntegerField(
        default=2,
        verbose_name="Grace period (hours)",
        help_text="Not shown to members. A cancel this close to the notice line is still free.",
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
    # #409: who the members site turns away, and what they read. Former members are always
    # locked out; suspended ones only while the switch is on. See core/member_lockout.py.
    former_member_signin_message = models.TextField(
        blank=True,
        default="Your membership is no longer active, so this account can no longer sign in to the member site.",
        verbose_name="Former member sign in message",
        help_text="Shown when a former member tries to sign in to the member site. The support email is "
        "shown under it. Blank uses the built in sentence.",
    )
    # #654: an account made from a class booking is a guest, not a member, and reads its own
    # sentence when it reaches the members site.
    guest_member_signin_message = models.TextField(
        blank=True,
        default="This account is for booking classes. Membership is separate: email us to become a member.",
        verbose_name="Guest sign in message",
        help_text="Shown when a guest account (made when booking a class) tries to sign in to the member site. "
        "The support email is shown under it. Blank uses the built in sentence.",
    )
    suspended_members_locked_out = models.BooleanField(
        default=True,
        verbose_name="Lock out suspended members",
        help_text="When on, a suspended member cannot sign in to the member site and sees the message below. "
        "Former members are always locked out.",
    )
    kiln_tickets_open = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name="Open kiln tickets to everyone",
        help_text=(
            "When on, every member and every guest account can file kiln tickets, and guests can sign in "
            "to the member site for that alone. When off, only the Ceramics Guild lead and staff can open "
            "Kiln Tickets, and guest accounts stay locked out of the member site."
        ),
    )
    suspended_member_signin_message = models.TextField(
        blank=True,
        default="Your membership is paused right now, so this account cannot sign in to the member site.",
        verbose_name="Suspended member sign in message",
        help_text="Shown when a suspended member tries to sign in while suspended members are locked out. "
        "The support email is shown under it. Blank uses the built in sentence.",
    )
    help_page_enabled = models.BooleanField(
        default=True,
        verbose_name="Show Help in the sidebar",
        help_text="When off, the Help link is hidden from the sidebar and the /help/ page redirects to the home page.",
    )
    guided_tours_enabled = models.BooleanField(
        default=True,
        verbose_name="Offer guided tours",
        help_text="When off, the guided tour offer never pops up for anyone. Each member's own tour setting is "
        "kept, and a tour can still be started from its Show Me Around link.",
    )
    # wiki_enabled, equipment_page_enabled and host_a_workshop_enabled used to live here. They are
    # FeatureSwitch rows now (see core/features.py) so that every member feature answers to one
    # mechanism with three states instead of two, and so that the next one needs no migration.
    guild_welcome_email_enabled = models.BooleanField(
        default=True,
        verbose_name="Send guild welcome emails",
        help_text="When off, no guild welcome email is sent when a member joins a guild (the Join "
        "button or the Discord /join-guild command), the join popup's email opt-in is hidden, and "
        "the Welcome Email tab is hidden from the guild editor. Per-guild settings are kept and "
        "take effect again when this is turned back on.",
    )
    instructor_discount_codes_enabled = models.BooleanField(
        default=False,
        verbose_name="Allow instructors to manage their own discount codes",
        help_text=(
            "When off, instructors can no longer create, edit, or approve their own discount "
            "codes from the Teaching portal — the Discount Codes tile is hidden and the pages "
            "redirect. Admins can always create and approve discount codes from Classes admin, "
            "either way. Default off: only admins create discount codes."
        ),
    )
    instructor_discount_codes_need_approval = models.BooleanField(
        default=True,
        verbose_name="Instructors request discount codes and an admin approves them",
        help_text=(
            "When on, instructors ask for a discount code and an admin approves or declines it; the "
            "code exists only once approved. When off, instructors create, edit and delete their own "
            "codes directly, and each new code waits for approval as before. This only matters while "
            "the setting above is on."
        ),
    )
    display_demo_classes = models.BooleanField(
        default=False,
        verbose_name="Display demo classes",
        help_text=(
            "When on, classes seeded by the demo_data command (a demo- slug) appear in the public "
            "catalog, calendar, and class pages. Off hides them from members while admins and "
            "instructors still see and manage them. Turn on only for a live demo."
        ),
    )
    display_demo_guild = models.BooleanField(
        default=False,
        verbose_name="Display demo guild",
        help_text=(
            "When on, the example Cartographers guild appears in the guild directory and sidebar. "
            "Off keeps it reachable only by its direct link, and it never enters voting or funding "
            "either way. Turn on only for a live demo."
        ),
    )
    member_directory_public = models.BooleanField(
        default=False,
        verbose_name="Public member directory",
        help_text=(
            "When on, the member directory at /members/ is viewable without signing in (the original "
            "public-directory behavior). When off, visitors must sign in before the directory shows anything. "
            "This is the real access switch for the directory. Member Directory in the feature list above is "
            "cosmetic: it only decides whether the entry shows in the sidebar, and the page stays reachable by "
            "its own link either way. Two switches, two jobs — that one tidies the sidebar, this one decides "
            "whether signed-out visitors can read the directory."
        ),
    )
    member_event_policy = models.CharField(
        max_length=20,
        choices=MemberEventPolicy.choices,
        default=MemberEventPolicy.APPROVAL,
        help_text=(
            "Who can create Calendar events, and whether a member's event needs review "
            "before it's published. Leads, staff, and admins always post directly."
        ),
    )
    member_google_calendar_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Member Google Calendar ID",
        help_text=(
            "Google Calendar ID for the members-only makerspace calendar — where events set to the "
            "'Member' calendar sync to (Calendar Settings → Integrate calendar → Calendar ID, NOT the "
            "iCal URL). Blank keeps 'Member' events in FOG only."
        ),
    )
    public_google_calendar_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Public Google Calendar ID",
        help_text=(
            "Google Calendar ID for the outward-facing public calendar — where events set to the "
            "'Public' calendar sync to (Calendar Settings → Integrate calendar → Calendar ID, NOT the "
            "iCal URL). Blank keeps 'Public' events in FOG only."
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
    discord_events_sync_enabled = models.BooleanField(
        default=False,
        verbose_name="Publish events to Discord",
        help_text=(
            "When on (and the Discord bot is configured with Manage Events), publishing/editing/deleting a "
            "an event creates/updates/removes it in the Discord server's Events. Studio hours and "
            "classes are never pushed."
        ),
    )
    eventbrite_sync_enabled = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name="Sell classes on Eventbrite",
        help_text=(
            "When on (and the Eventbrite credentials are set), a published class whose instructor ticked "
            "Also sell on Eventbrite is listed there, kept up to date, and taken down when it ends."
        ),
    )
    discord_calendar_channel_id = models.CharField(
        max_length=30,
        blank=True,
        default="",
        verbose_name="Discord calendar channel id",
        help_text=(
            "The channel id of #calendar — where the weekly digest and new-event posts go. Blank disables both posts."
        ),
    )
    discord_calendar_posts_enabled = models.BooleanField(
        default=False,
        verbose_name="Post calendar updates to Discord",
        help_text=(
            "When on (and the calendar channel id is set), FOG posts a weekly what's-coming-up digest "
            "to #calendar every Monday morning, plus a short post whenever a new event or class "
            "lands on the Calendar."
        ),
    )
    discord_classes_channel_id = models.CharField(
        max_length=30,
        blank=True,
        default="",
        verbose_name="Discord classes channel id",
        help_text=(
            "The channel id of #classes — where the weekly classes digest and new-class posts go. "
            "Blank disables both posts."
        ),
    )
    discord_classes_posts_enabled = models.BooleanField(
        default=False,
        verbose_name="Post class updates to Discord",
        help_text=(
            "When on (and the classes channel id is set), FOG posts a weekly what's-teaching digest "
            "to #classes every Monday morning, plus a short post whenever a new class is published."
        ),
    )
    discord_info_channel_id = models.CharField(
        max_length=30,
        blank=True,
        default="",
        verbose_name="Discord info channel id",
        help_text=(
            "The channel id of #important-info — where the bot's pinned links post lives. "
            "Blank disables syncing the post."
        ),
    )
    discord_info_message_id = models.CharField(
        max_length=30,
        blank=True,
        default="",
        verbose_name="Discord info message id",
        help_text=(
            "The id of the bot's pinned message in #important-info. FOG edits this message in place "
            "(it keeps its pin) — update only if the post is ever reposted. Blank disables syncing."
        ),
    )
    discord_info_links_content = models.TextField(
        blank=True,
        default=DISCORD_INFO_LINKS_DEFAULT,
        verbose_name="Important links content",
        help_text=(
            "Discord-flavored markdown for the '📌 Past Lives — Important Links' embed in #important-info. "
            "Saving updates the pinned Discord post immediately."
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
        help_text="Add a QR code to the Calendar on auto event slides.",
    )
    # The self-building slide blocks. All default ON: a screen pointed at its URL should
    # arrive populated, and an admin switches off what they don't want on the wall.
    signage_show_classes = models.BooleanField(
        default=True,
        verbose_name="Show this week's classes",
        help_text="Add a slide for each class happening in the next seven days.",
    )
    signage_show_guilds = models.BooleanField(
        default=True,
        verbose_name="Show the guilds",
        help_text=(
            "Add a slide for each guild, naming its next meeting or class. A guild with nothing "
            "coming up shows its About text instead. Guild meetings on a screen are readable by "
            "anyone in the building, not only that guild's members."
        ),
    )
    signage_show_calendar = models.BooleanField(
        default=True,
        verbose_name="Show what's on this month",
        help_text="Add a slide naming what is still to come this month, soonest first.",
    )
    signage_show_voting = models.BooleanField(
        default=True,
        verbose_name="Show the funding vote",
        help_text="Add a slide about the monthly guild funding vote and when it closes.",
    )
    signage_show_directory = models.BooleanField(
        default=True,
        verbose_name="Show the member directory",
        help_text="Add a slide with a QR that opens the member directory.",
    )
    signage_show_teach = models.BooleanField(
        default=True,
        verbose_name="Show Host a Class",
        help_text="Add a slide inviting members to run their own class.",
    )
    signage_show_tour = models.BooleanField(
        default=True,
        verbose_name="Show Book a Tour",
        help_text="Add a slide inviting visitors to book a walkthrough of the space, with a QR to the booking page.",
    )
    signage_tour_url = models.URLField(
        blank=True,
        default=default_signage_tour_url,
        verbose_name="Tour booking link",
        help_text="Where the Book a Tour QR sends people. Blank leaves the tour slide off the screens.",
    )

    # Brand block (PLAT-1). One deployment is one organization; these are the strings and
    # assets that identify it. Defaults are the Past Lives values so the migration is a
    # no-op on the live instance.
    org_name = models.CharField(
        max_length=200,
        default="Past Lives Makerspace",
        verbose_name="Organization name",
        help_text="Your organization's full name. Shown in page titles, the privacy policy, and public page descriptions.",
    )
    org_short_name = models.CharField(
        max_length=60,
        blank=True,
        default="Past Lives",
        verbose_name="Short name",
        help_text="The compact wordmark used in the sidebar, the public topbar, and browser tab titles. Blank uses the full name.",
    )
    org_legal_name = models.CharField(
        max_length=200,
        blank=True,
        default="Past Lives Makerspace LLC",
        verbose_name="Legal name",
        help_text="Your registered legal entity, for receipts and legal notices. Blank uses the organization name.",
    )
    org_logo = models.ImageField(
        upload_to="brand/logo/",
        blank=True,
        validators=[validate_image_size],
        verbose_name="Logo",
        help_text="Square logo shown in the sidebar, the public topbar, and the browser tab. Blank uses the built in mark.",
    )
    org_primary_color = models.CharField(
        max_length=7,
        blank=True,
        default="#092E4C",
        validators=[validate_hex_color],
        verbose_name="Primary brand color",
        help_text="Your main brand color as a hex code, e.g. #092E4C. Sets the browser and mobile app chrome color.",
    )
    org_support_email = models.EmailField(
        blank=True,
        default="info@pastlives.space",
        verbose_name="Support email",
        help_text="The address members are told to write to for help. Shown in the privacy policy.",
    )
    org_website_url = models.URLField(
        blank=True,
        default="https://pastlives.space",
        verbose_name="Public website",
        help_text="Your main marketing website, with no trailing slash. The public topbar's Home, Guilds, Membership, and Contact links and the sidebar globe icon are built from it.",
    )
    # #467: the two store listings behind every "Get the app" badge (email footers, the sign in
    # page, the member home, the sidebar and the landing page). Both default to the live Past
    # Lives listings; blank means not launched, which a fresh deployment or a pulled listing uses.
    google_play_url = models.URLField(
        blank=True,
        default="https://play.google.com/store/apps/details?id=app.pastlives.hub",
        verbose_name="Google Play URL",
        help_text="The app's Google Play listing. Blank means not launched: the badge is left out everywhere.",
    )
    app_store_url = models.URLField(
        blank=True,
        default="https://apps.apple.com/us/app/past-lives-makerspace/id6796557084",
        verbose_name="App Store URL",
        help_text="The app's App Store listing. Blank means not launched: the badge is left out everywhere and the copy says iOS is coming soon.",
    )
    # #813: the building door code on the member portal home page, changed from its Door Code
    # card by an admin. The repo is public: the real code lives only in the production row.
    door_code = models.CharField(
        max_length=20,
        blank=True,
        default="",
        db_default="",
        help_text="The building door code active members see on their home page. Blank shows no card.",
    )
    door_code_changed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When an admin last saved the door code; members see the date on the card.",
    )
    door_code_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The admin who last saved the door code; admins see the name on the card.",
    )
    # #942: how fast the Spotlight messages crawl across the top of Home, set from Admin Tools >
    # Spotlight's Spotlight Messages card (SpotlightCrawlSpeedForm), its slider's steps the validators'.
    spotlight_crawl_speed = models.PositiveSmallIntegerField(
        default=SPOTLIGHT_CRAWL_SPEED_DEFAULT,
        db_default=SPOTLIGHT_CRAWL_SPEED_DEFAULT,
        validators=[
            MinValueValidator(SPOTLIGHT_CRAWL_SPEED_MIN),
            MaxValueValidator(SPOTLIGHT_CRAWL_SPEED_MAX),
            StepValueValidator(SPOTLIGHT_CRAWL_SPEED_STEP),
        ],
        verbose_name="Spotlight crawl speed",
        help_text=(
            f"How fast the Spotlight messages crawl across the top of Home, in pixels a second "
            f"({SPOTLIGHT_CRAWL_SPEED_MIN} to {SPOTLIGHT_CRAWL_SPEED_MAX}, in steps of {SPOTLIGHT_CRAWL_SPEED_STEP})."
        ),
    )

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def __str__(self) -> str:
        return "Site Settings"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Force singleton by always using pk=1."""
        self.pk = 1
        delete_orphan_on_replace(self, "org_logo")
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if self.member_agreement_required and not self.member_agreement_url:
            from django.core.exceptions import ValidationError

            raise ValidationError({"member_agreement_url": "Required when Member Agreement is enforced."})

    @classmethod
    def load(cls) -> SiteConfiguration:
        """Load the singleton instance, creating it with defaults if needed."""
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def set_door_code(self, code: str, *, by: User, now: datetime) -> bool:
        """Save the door code, stamping who saved it and when only if it changed (#813).

        Saving the code already stored writes nothing, so the card keeps the date and name of
        the real change. Writes only the three door code columns, so a concurrent edit to any
        other setting is never reverted by a whole row save.

        Returns:
            True when the code changed.
        """
        if code == self.door_code:
            return False
        self.door_code = code
        self.door_code_changed_at = now
        self.door_code_changed_by = by
        self.save(update_fields=["door_code", "door_code_changed_at", "door_code_changed_by"])
        return True

    @property
    def instructor_discount_codes_approval_mode(self) -> bool:
        """Instructors request and an admin decides: both discount code settings on."""
        return self.instructor_discount_codes_enabled and self.instructor_discount_codes_need_approval


class AnnouncementOrderChangedError(ValueError):
    """A reorder that does not name every announcement exactly once: the admin's page is out of date."""


class AnnouncementQuerySet(models.QuerySet["Announcement"]):
    """Query helpers for the ticker on Home and Admin Tools > Spotlight (#889, #935)."""

    def in_order(self) -> AnnouncementQuerySet:
        """The admin's order: ``position``, then newest first, so a new one leads until it is moved."""
        return self.order_by("position", "-created_at", "-pk")

    def live(self, now: datetime) -> AnnouncementQuerySet:
        """The ones members see at ``now``: on, begun (or no start) and not yet ended (or no end), in order."""
        return (
            self.filter(is_active=True)
            .filter(models.Q(show_from__isnull=True) | models.Q(show_from__lte=now))
            .filter(models.Q(show_until__isnull=True) | models.Q(show_until__gt=now))
            .in_order()
        )


class Announcement(models.Model):
    """A short notice an admin posts for every member, crawling across the top of Home (#889, a ticker since #935).

    Written in Admin Tools > Spotlight. The body is rich text (bold, italic and links), stored as
    sanitized HTML of at most :data:`core.html_sanitize.ANNOUNCEMENT_MAX_CHARS` visible
    characters. It shows while it is on and between its optional ``show_from`` and
    ``show_until``; the live ones run one after another in the admins' order. Nothing is ever sent: an
    announcement is shown, not emailed, pushed or posted.
    """

    class Status(models.TextChoices):
        LIVE = "live", "Live"
        SCHEDULED = "scheduled", "Scheduled"
        ENDED = "ended", "Ended"
        OFF = "off", "Off"

    body = models.TextField(
        help_text="Sanitized rich text HTML (bold, italic, links and line breaks), at most 160 visible characters.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Off hides it from Home whatever its dates say.",
    )
    show_from = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When it starts showing on Home. Empty shows it at once.",
    )
    show_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When it stops showing on Home. Empty keeps it until an admin turns it off.",
    )
    position = models.PositiveIntegerField(
        default=0,
        help_text="Its place in the ticker on Home, set by dragging in Admin Tools > Spotlight. Lower shows first.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was added.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="site_announcements",
        help_text="The admin who added it.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Its last save. Part of the ticker's signature, so an edit brings a closed ticker back.",
    )

    objects = AnnouncementQuerySet.as_manager()

    def __str__(self) -> str:
        return self.text[:60]

    @property
    def text(self) -> str:
        """The visible words, tags gone and entities decoded: what the 160 limit counts."""
        from core.html_sanitize import rich_html_to_text

        return rich_html_to_text(self.body)

    @property
    def body_html(self) -> str:
        """The body for a page, sanitized again on the way out."""
        from django.utils.safestring import mark_safe

        from core.html_sanitize import sanitize_announcement_html

        return mark_safe(sanitize_announcement_html(self.body))  # noqa: S308  # sanitized just above

    @property
    def inline_html(self) -> str:
        """The body on one line, for the ticker on Home (#935): bold, italic and links kept, paragraphs and breaks as spaces."""
        from django.utils.safestring import mark_safe

        from core.html_sanitize import sanitize_announcement_html

        html = re.sub(r"\s*(?:<br\s*/?>|</p>\s*<p>)\s*", " ", sanitize_announcement_html(self.body))
        return mark_safe(re.sub(r"</?p>", "", html).strip())  # noqa: S308  # sanitized, then only p and br removed

    def status(self, now: datetime) -> Announcement.Status:
        """Live, Scheduled, Ended or Off at ``now``, by the same rule as :meth:`AnnouncementQuerySet.live`."""
        if not self.is_active:
            return self.Status.OFF
        if self.show_until is not None and self.show_until <= now:
            return self.Status.ENDED
        if self.show_from is not None and self.show_from > now:
            return self.Status.SCHEDULED
        return self.Status.LIVE

    @classmethod
    def reorder(cls, pks: list[int]) -> None:
        """Put every announcement at its index in ``pks``.

        Saved with ``bulk_update``, so ``updated_at`` stays put: a new order is not a new set, and
        a member who closed the ticker keeps it closed.

        Raises:
            AnnouncementOrderChangedError: ``pks`` does not name every announcement exactly once.
        """
        rows = {row.pk: row for row in cls.objects.all()}
        if len(pks) != len(set(pks)) or set(pks) != set(rows):
            raise AnnouncementOrderChangedError("The list changed since this page loaded. Reload it and try again.")
        for index, pk in enumerate(pks):
            rows[pk].position = index
        cls.objects.bulk_update(rows.values(), ["position"])

    @staticmethod
    def signature(rows: list[Announcement]) -> str:
        """The ticker's identity: each row's id and last save, by id, so a new order alone keeps it.

        A member's Close keeps this; any new, edited, ended or begun announcement changes it, so
        the ticker comes back.
        """
        return ",".join(f"{row.pk}:{row.updated_at.timestamp():.6f}" for row in sorted(rows, key=lambda row: row.pk))


#: How long an approved Discord ping shows on Home (#943).
QUEUED_MESSAGE_LIVE_FOR = timedelta(days=7)
#: The longest a guild page headline made from a Discord ping runs.
GUILD_TITLE_MAX_CHARS = 80
QUEUED_MESSAGE_HANDLED = "That Discord message was already approved or discarded."


class QueuedMessageHandledError(ValueError):
    """An approve, discard or edit of a Discord message that is no longer waiting."""


class QueuedDiscordMessageQuerySet(models.QuerySet["QueuedDiscordMessage"]):
    """Query helpers for the Waiting for Review queue in Admin Tools > Spotlight (#943)."""

    def waiting(self) -> QueuedDiscordMessageQuerySet:
        """The ones still waiting for an admin, oldest first, with what their rows show."""
        return (
            self.filter(state=QueuedDiscordMessage.State.WAITING)
            .select_related("sender")
            .prefetch_related("guilds")
            .order_by("sent_at", "pk")
        )


class QueuedDiscordMessage(models.Model):
    """A Discord message that pinged @everyone or a guild role, waiting to become a Spotlight message (#943).

    ``queue_discord_pings`` writes one per message, already drafted as a Spotlight line in
    ``line``. Nothing here is ever live: an admin approves it into an :class:`Announcement`
    (and, for a guild ping, a ``GuildAnnouncement`` on each pinged guild's page), or discards
    it. A discarded row stays, so the unique message id keeps it from being queued again.
    """

    class State(models.TextChoices):
        WAITING = "waiting", "Waiting for review"
        APPROVED = "approved", "Approved"
        DISCARDED = "discarded", "Discarded"

    discord_message_id = models.CharField(
        max_length=32, unique=True, help_text="The Discord message's id. Unique, so no message is queued twice."
    )
    discord_channel_id = models.CharField(max_length=32, help_text="The id of the Discord channel it was posted in.")
    discord_url = models.URLField(help_text="The link that opens the message in Discord.")
    original_text = models.TextField(
        help_text="The whole message as readable text: mentions as names, the triggering ping left out."
    )
    line = models.TextField(
        help_text="The drafted Spotlight line, sanitized rich text HTML of at most 160 visible characters."
    )
    guilds = models.ManyToManyField(
        "membership.Guild",
        blank=True,
        related_name="queued_discord_messages",
        help_text="The guilds whose Discord role it pinged. None for a plain @everyone.",
    )
    sender_discord_id = models.CharField(max_length=32, help_text="The Discord user id of whoever sent it.")
    sender_name = models.CharField(
        max_length=100, help_text="The sender's Discord display name, else their username, when it was queued."
    )
    sender = models.ForeignKey(
        "membership.Member",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queued_discord_messages",
        help_text="The member whose verified Discord account sent it. Empty when the account is not linked.",
    )
    sent_at = models.DateTimeField(help_text="When it was posted in Discord.")
    state = models.CharField(
        max_length=10,
        choices=State.choices,
        default=State.WAITING,
        help_text="Waiting for an admin, approved into Spotlight, or discarded for good.",
    )
    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queued_discord_messages",
        help_text="The Spotlight message it became once approved.",
    )
    guild_announcements = models.ManyToManyField(
        "membership.GuildAnnouncement",
        blank=True,
        related_name="queued_discord_messages",
        help_text="The guild page announcements it became once approved, one per pinged guild.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was queued.")

    objects = QueuedDiscordMessageQuerySet.as_manager()

    def __str__(self) -> str:
        return f"Discord message {self.discord_message_id} ({self.get_state_display()})"

    @property
    def credit(self) -> str:
        """Who the line credits: the linked member's name, else the Discord name."""
        return self.sender.display_name if self.sender else self.sender_name

    @property
    def line_html(self) -> str:
        """The drafted line for a page, sanitized again on the way out."""
        from django.utils.safestring import mark_safe

        from core.html_sanitize import sanitize_announcement_html

        return mark_safe(sanitize_announcement_html(self.line))  # noqa: S308  # sanitized just above

    @property
    def guild_title(self) -> str:
        """The guild page headline: the first line of the text, cut at a word to 80 characters with "..."."""
        first = self.original_text.splitlines()[0]
        if len(first) <= GUILD_TITLE_MAX_CHARS:
            return first
        cut = first[: GUILD_TITLE_MAX_CHARS - 2]
        return f"{cut.rsplit(' ', 1)[0] if ' ' in cut else cut[:-1]}..."

    @property
    def guild_body(self) -> str:
        """The guild page text: the message, then who said it and the link to it in Discord.

        When the headline is the whole first line and more follows, the first line is left out, so it
        does not show twice. The page links the bare address (the body is plain text), so it stays a URL.
        """
        first, *rest = self.original_text.splitlines()
        text = "\n".join(rest) if rest and self.guild_title == first else self.original_text
        return f"{text}\n\nPosted in Discord by {self.credit}: {self.discord_url}"

    @classmethod
    def _locked_waiting(cls, pk: int) -> QueuedDiscordMessage:
        """The row, locked for this transaction; raises when it is gone or no longer waiting."""
        # Only this row is locked: PostgreSQL refuses FOR UPDATE on the nullable side of the sender join.
        row = cls.objects.select_for_update(of=("self",)).select_related("sender__user").get(pk=pk)
        if row.state != cls.State.WAITING:
            raise QueuedMessageHandledError(QUEUED_MESSAGE_HANDLED)
        return row

    @classmethod
    def save_line(cls, pk: int, line: str) -> None:
        """Keep an admin's edit of the drafted line; it stays waiting. ``line`` is already cleaned."""
        with transaction.atomic():
            row = cls._locked_waiting(pk)
            row.line = line
            row.save(update_fields=["line"])

    @classmethod
    def approve(cls, pk: int, by: User) -> QueuedDiscordMessage:
        """Put the line live on Home for 7 days and, for a guild ping, on each guild's page. Sends nothing.

        One transaction with the row locked, so a double click or two admins approve it once.

        Raises:
            QueuedMessageHandledError: it was already approved or discarded.
        """
        from membership.models import GuildAnnouncement

        with transaction.atomic():
            row = cls._locked_waiting(pk)
            now = timezone.now()
            row.announcement = Announcement.objects.create(
                body=row.line, show_from=now, show_until=now + QUEUED_MESSAGE_LIVE_FOR, created_by=by
            )
            author = row.sender.user if row.sender else None
            # Built here, never through notify_members: approving emails, pushes, bells and posts nothing.
            posts = [
                GuildAnnouncement.objects.create(
                    guild=guild,
                    author=author,
                    title=row.guild_title,
                    body=row.guild_body,
                    send_email=False,
                    discord_channel=GuildAnnouncement.DiscordChannel.NONE,
                    moderation_state=GuildAnnouncement.ModerationState.PUBLISHED,
                    reviewed_by=by,
                    reviewed_at=now,
                )
                for guild in row.guilds.order_by("name")
            ]
            row.state = cls.State.APPROVED
            row.save(update_fields=["state", "announcement"])
            row.guild_announcements.set(posts)
        return row

    @classmethod
    def discard(cls, pk: int) -> None:
        """Drop it for good. The row stays, so its message id is never queued again.

        Raises:
            QueuedMessageHandledError: it was already approved or discarded.
        """
        with transaction.atomic():
            row = cls._locked_waiting(pk)
            row.state = cls.State.DISCARDED
            row.save(update_fields=["state"])

    @staticmethod
    def draft_line(text: str, guild_names: list[str], credit: str, url: str) -> str:
        """The Spotlight line for ``text``: ``Guild: text [...] (Credit)``, at most 160 visible characters.

        The guild names lead only for a guild ping. Text that does not fit is cut at a word and
        ends in ``[...]``, a link to ``url``; text that fits shows whole with no ``[...]``.
        """
        from django.utils.html import escape

        from core.html_sanitize import ANNOUNCEMENT_MAX_CHARS, sanitize_announcement_html

        prefix = f"{', '.join(guild_names)}: " if guild_names else ""
        tail = f" ({credit})"
        words = " ".join(text.split())
        budget = ANNOUNCEMENT_MAX_CHARS - len(prefix) - len(tail)
        if len(words) <= budget:
            return sanitize_announcement_html(f"<p>{escape(prefix + words + tail)}</p>")
        more = " [...]"
        cut = words[: max(budget - len(more), 0) + 1]
        cut = cut.rsplit(" ", 1)[0] if " " in cut else cut[:-1]
        body = f'{escape(prefix + cut.rstrip())} <a href="{escape(url)}">[...]</a>{escape(tail)}'
        return sanitize_announcement_html(f"<p>{body}</p>")


class DiscordChannelCursor(models.Model):
    """The newest Discord message ``queue_discord_pings`` has read in one channel (#943).

    A channel's first sighting records its newest message and queues nothing, so the first run
    never imports history; each later tick reads only what came after.
    """

    channel_id = models.CharField(max_length=32, unique=True, help_text="The Discord channel's id.")
    last_message_id = models.CharField(
        max_length=32, help_text="The newest message id already read in the channel, or 0 for an empty channel."
    )
    updated_at = models.DateTimeField(auto_now=True, help_text="When the job last moved it.")

    def __str__(self) -> str:
        return f"Channel {self.channel_id} read to {self.last_message_id}"


class CalendarFeed(models.Model):
    """A named iCal feed displayed on the Calendar.

    Multiple feeds (e.g. "General Calendar", "Classes", "Open Studio") can be
    configured from the Site Settings → Calendar tab. Each is fetched on demand
    by ``hub.calendar_service`` and rendered as its own legend entry.
    """

    name = models.CharField(
        max_length=100,
        help_text="Display name shown on the Calendar legend (e.g. 'General Calendar', 'Classes').",
    )
    ical_url = models.URLField(
        help_text="Public iCal URL. Paste the 'Secret address in iCal format' from Google Calendar settings.",
    )
    color = models.CharField(
        max_length=7,
        default="#EEB44B",
        help_text="Hex color for this feed's events on the Calendar (e.g. #EEB44B).",
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

    def clear_expired(self) -> int:
        """Revoke every currently-expired invite in one pass; return how many were cleared.

        Each invite goes through :meth:`Invite.revoke`, so its placeholder Member is
        cleaned up and a ``MEMBER_INVITE_REVOKED`` activity is logged, exactly as a
        one-off revoke would. Expired invites are un-accepted by definition, so revoke
        never raises here.
        """
        expired = list(self.expired())
        for invite in expired:
            invite.revoke()
        return len(expired)


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
        help_text="When this person took a facility tour: stamped when a guide marks their tour attended (#949).",
    )
    tour_status_checked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Unused since the old tour booking service was retired (#950); kept until a later release drops it.",
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
        # Staging only: the delivery policy refused the recipient, so nothing was sent.
        SUPPRESSED = "suppressed", "Suppressed"

    to_email = models.CharField(max_length=254, help_text="Recipient(s); comma-joined when multiple.")
    subject = models.CharField(max_length=500, help_text="Email subject line.")
    trigger_kind = models.CharField(max_length=100, help_text="Which workflow sent it, e.g. 'billing.receipt'.")
    status = models.CharField(max_length=10, choices=Status.choices, help_text="Send outcome.")
    error_message = models.TextField(
        blank=True,
        default="",
        help_text="Exception text when status=failed; the policy reason when status=suppressed.",
    )
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
        ACCEPTED_MEMBER_AGREEMENT = "accepted_member_agreement", "Accepted the Member Agreement"
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
        ORIENTATION_RECORDED = "orientation_recorded", "Orientation recorded"
        ORIENTATION_RECORD_REMOVED = "orientation_record_removed", "Orientation record removed"
        LATE_FEE_CHARGED = "late_fee_charged", "Late cancellation fee charged"
        LATE_FEE_PAID = "late_fee_paid", "Late cancellation fee paid"
        LATE_FEE_WAIVED = "late_fee_waived", "Late cancellation fee waived"
        LATE_FEE_REFUNDED = "late_fee_refunded", "Late cancellation fee refunded"
        INSTRUCTOR_ORIENTED = "instructor_oriented", "Completed instructor orientation"
        TEACHING_APPLIED = "teaching_applied", "Applied to teach"
        TEACHING_APPLICATION_DECLINED = "teaching_application_declined", "Teaching application declined"
        TEACHING_GRANTED = "teaching_granted", "Teaching access granted"
        TEACHING_REVOKED = "teaching_revoked", "Teaching access revoked"
        TEACHING_INQUIRY_ADDED = "teaching_inquiry_added", "Teaching inquiry added"
        GUILD_JOINED = "guild_joined", "Joined a guild"
        LEASE_ACTIVATED = "lease_activated", "Space agreement activated"
        SPACE_REQUEST = "space_request", "Space request"
        SITE_ANNOUNCEMENT = "site_announcement", "Site announcement"
        MEETING_APPROVED = "meeting_approved", "Meeting minutes approved"
        MEETING_UNLOCKED = "meeting_unlocked", "Meeting minutes unlocked"
        MEETING_UNPUBLISHED = "meeting_unpublished", "Meeting agenda unpublished"
        ACCOUNT_DELETED = "account_deleted", "Deleted account"
        RECONCILIATION_SNAPSHOT_TAKEN = "reconciliation_snapshot_taken", "Reconciliation snapshot taken"
        RECONCILIATION_SNAPSHOT_DELETED = "reconciliation_snapshot_deleted", "Reconciliation snapshot deleted"
        # Member Wiki (spec A ships all six together — brief §9.1 — so specs B and D,
        # which build in parallel off this migration, never add an enum member in their
        # own migration. A writes WIKI_PAGE_CREATED / WIKI_PAGE_EDITED; B writes
        # WIKI_PAGE_VERIFIED; D writes the rest. An unwritten value costs one row here.
        WIKI_PAGE_CREATED = "wiki_page_created", "Wiki page created"
        WIKI_PAGE_EDITED = "wiki_page_edited", "Wiki page edited"
        WIKI_PAGE_VERIFIED = "wiki_page_verified", "Wiki page verified"
        WIKI_PAGE_REPORTED = "wiki_page_reported", "Wiki page reported"
        WIKI_PAGE_ARCHIVED = "wiki_page_archived", "Wiki page archived"
        WIKI_PAGE_REVERTED = "wiki_page_reverted", "Wiki page reverted"

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

    @property
    def who(self) -> str:
        """The person the feed names for this row, or "" for a system event.

        The actor's member name first, then their account name, then their email. A
        guest checkout reaches the log from the Stripe webhook with no actor, so a row
        targeting a registration falls back to the registrant's own name.
        """
        if self.actor is not None:
            member = getattr(self.actor, "member", None)
            if member is not None and member.display_name:
                return str(member.display_name)
            return str(self.actor.get_full_name() or self.actor.email)
        roster_name = getattr(self.target, "roster_name", "")
        return str(roster_name)

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

    def mark_read_for(self, *, url: str, triggers: Iterable[str]) -> int:
        """Mark read every unread bell row of ``triggers`` linking to ``url`` (#894); returns the count.

        A decided staff request clears the bell rows that asked about it, for everyone it
        reached; the link is unique to the request (:class:`StaffRequestMixin`).
        """
        return self.unread().filter(url=url, trigger__in=list(triggers)).update(read_at=timezone.now())


class StaffRequestMixin:
    """A request staff are asked to decide, whose bell rows clear once it is decided (#894 part 3).

    The model names the events that ask about it (:attr:`REQUEST_EVENT_KEYS`) and the bell
    link, which is unique to the request (:attr:`request_bell_path`); its decision methods
    call :meth:`clear_request_bells`, and ``core.signals.clear_request_bells_on_delete``
    covers a delete, cascades included. The link is what ``Notification.url`` stores: the
    path (and any query or fragment) of what the emit passed as ``url``.
    """

    REQUEST_EVENT_KEYS: ClassVar[tuple[str, ...]] = ()

    @property
    def request_bell_path(self) -> str:
        raise NotImplementedError(f"{type(self).__name__} must name its request's bell link")

    def clear_request_bells(self) -> int:
        """Mark read every unread bell row asking about this request; returns how many."""
        return Notification.objects.mark_read_for(url=self.request_bell_path, triggers=self.REQUEST_EVENT_KEYS)


class Notification(models.Model):
    """One in-app bell entry for one user. Always created on dispatch (non-optional)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    # 100 to match TransactionalEmailLog.trigger_kind, which stores the same keys. At 40
    # the longest shipped key (equipment.reservation_cancelled_by_manager, 42 characters)
    # overflowed the column: Postgres rejects the INSERT and aborts the surrounding
    # transaction, while SQLite silently stores it, so the whole class of bug is invisible
    # to a SQLite test run.
    trigger = models.CharField(max_length=100, help_text="Trigger key from core.triggers.")
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

    ``changed_at`` and ``changed_by`` say who last flipped the switch and when: the member
    themselves, or the admin who saved it for them on the member's page. Both are empty on
    rows saved before they existed (2026-10-14), when every save wrote every box.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_prefs")
    event_key = models.CharField(max_length=60, help_text="Event key from core.events.registry.")
    channel = models.CharField(max_length=20, help_text="Channel key from core.events.registry.Channel.")
    enabled = models.BooleanField(default=False, help_text="Whether this user receives this event on this channel.")
    changed_at = models.DateTimeField(null=True, blank=True, help_text="When the switch was last flipped.")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who last flipped the switch: the member, or the admin who saved it for them.",
    )

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


class TourStateManager(models.Manager["TourState"]):
    """The whole guided-tour lifecycle, fat-model style (Spec C §4).

    Every method validates ``tour_key`` against :data:`core.tours.TOURS` and
    raises ``ValueError`` on an unknown key — ``TourState.tour_key`` values are
    exactly the registered tour keys, never free-form strings.
    ``mark_completed`` / ``Member.has_completed_tour`` are Spec D's frozen
    contract for completion tracking.
    """

    def _validate_key(self, tour_key: str) -> None:
        from core.tours import TOURS

        if tour_key not in TOURS:
            raise ValueError(f"Unknown tour key {tour_key!r} — not registered in core.tours.TOURS")

    def mark_offered(self, user: User, tour_key: str) -> TourState:
        """Record that the offer was shown. Never changes an existing row's status."""
        self._validate_key(tour_key)
        state, _ = self.get_or_create(user=user, tour_key=tour_key)
        return state

    def mark_completed(self, user: User, tour_key: str) -> TourState:
        """Record completion — upgrades from absent/offered/dismissed. Spec D's contract."""
        self._validate_key(tour_key)
        state, _ = self.update_or_create(user=user, tour_key=tour_key, defaults={"status": TourState.Status.COMPLETED})
        return state

    def mark_dismissed(self, user: User, tour_key: str) -> TourState:
        """Record dismissal — unless the row is COMPLETED (completed is sticky).

        A re-run of a finished tour Esc'd halfway must not erase the completion —
        Spec D leans on this guard.
        """
        self._validate_key(tour_key)
        state, created = self.get_or_create(
            user=user, tour_key=tour_key, defaults={"status": TourState.Status.DISMISSED}
        )
        if not created and state.status != TourState.Status.COMPLETED:
            state.status = TourState.Status.DISMISSED
            state.save(update_fields=["status", "updated_at"])
        return state

    def status_for(self, user: User, tour_key: str) -> str | None:
        """This user's status for one tour; ``None`` when no row (never offered)."""
        self._validate_key(tour_key)
        try:
            return self.get(user=user, tour_key=tour_key).status
        except TourState.DoesNotExist:
            return None

    def statuses_for(self, user: User) -> dict[str, str]:
        """Every ``{tour_key: status}`` for this user in one query (the Help-page card)."""
        return dict(self.filter(user=user).values_list("tour_key", "status"))


class TourState(models.Model):
    """Per-user, per-tour guided-tour state (Spec C §4).

    One row per ``(user, tour_key)`` — created when the tour is first offered,
    upgraded when dismissed or completed. Absent row = never offered.
    """

    class Status(models.TextChoices):
        OFFERED = "offered", "Offered"
        COMPLETED = "completed", "Completed"
        DISMISSED = "dismissed", "Dismissed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tour_states",
        help_text="The user this tour state belongs to.",
    )
    tour_key = models.CharField(max_length=60, help_text="Tour key from core.tours.TOURS.")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OFFERED,
        help_text="Where this user is with this tour.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the tour was first offered to this user.")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the status last changed.")

    objects = TourStateManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "tour_key"], name="uq_tourstate_user_tour_key"),
        ]

    def __str__(self) -> str:
        return f"{self.user.email}:{self.tour_key}={self.get_status_display()}"


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
        max_length=120,
        blank=True,
        default="",
        help_text=(
            "Dedupe window bucket — empty for one-shot, else e.g. '2026-06' for monthly, or a "
            "per-object token like 'event:1234:submitted:1752345678.123456'. Wide enough for the "
            "timestamp-suffixed periods (~42+ chars) that resubmission/reminder emits build."
        ),
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

    #: How a queued digest line says who pressed Remind lead (#894); :attr:`reminded_by` reads it.
    REMINDED_BY_PREFIX = "Reminded by "

    def __str__(self) -> str:
        suffix = f"@{self.period}" if self.period else ""
        return f"{self.event_key}→{self.target_ref}[{self.channel}]{suffix}"

    # A queued digest row (#894) reads without re-rendering: the recipient (``target_ref``),
    # the event (``event_key``), when it was queued (``created_at``) and the three below.

    @property
    def recipient_user_id(self) -> int:
        """The recipient's ``User`` pk, from a per-person ``user:<pk>`` target ref."""
        prefix, _sep, pk = self.target_ref.partition(":")
        if prefix != "user":
            raise ValueError(f"Not a per-person delivery: {self.target_ref!r}")
        return int(pk)

    @property
    def request_ref(self) -> str:
        """The request a digest row is about: the first two parts of its period (``approval:12``).

        The original ask (``approval:12:request``) and a Remind lead
        (``approval:12:reminder:2026-10-23``) share it.
        """
        return ":".join(self.period.split(":")[:2])

    def record_digest(self, *, subject: str, included: int, left_out: int) -> None:
        """Stamp a sent staff digest's ledger row with what it carried (#894 part 2).

        The ``staff_digest`` ledger row has no message of its own, so its ``title`` holds the
        subject and its ``body`` the counts, read back by :attr:`digest_counts`.
        """
        self.title = subject[:255]
        self.body = json.dumps({"included": included, "left_out": left_out})
        self.save(update_fields=["title", "body"])

    @property
    def digest_counts(self) -> tuple[int, int]:
        """``(included, left_out)`` for a sent digest, as :meth:`record_digest` wrote them.

        Raises ``ValueError`` for a row that recorded none (sent before #894 part 2, or a
        day's slot kept after two failed sends): callers filter those out first.
        """
        if not self.body:
            raise ValueError(f"No digest counts recorded on {self}")
        counts = json.loads(self.body)
        return counts["included"], counts["left_out"]

    @property
    def reminded_by(self) -> str:
        """Who pressed Remind lead for a queued line, or ``""`` for the original request."""
        return self.body.removeprefix(self.REMINDED_BY_PREFIX) if self.body.startswith(self.REMINDED_BY_PREFIX) else ""


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


class ScheduledTaskRunQuerySet(models.QuerySet["ScheduledTaskRun"]):
    """Querysets for the Automations dashboard's run history."""

    def latest_per_task(self) -> dict[str, ScheduledTaskRun]:
        """The most recent run per ``task_key``, keyed by task_key.

        Two bounded queries regardless of table size: aggregate the newest ``started_at``
        per key, then fetch just those rows — instead of dragging the whole run history
        into Python. Portable across SQLite and PostgreSQL (no ``DISTINCT ON``).
        """
        latest_started = self.values("task_key").annotate(max_started=models.Max("started_at"))
        match = models.Q()
        for row in latest_started:
            match |= models.Q(task_key=row["task_key"], started_at=row["max_started"])
        if not match:
            return {}
        return {run.task_key: run for run in self.filter(match)}

    def history_for(self, key: str) -> ScheduledTaskRunQuerySet:
        """One job's runs, newest first, each with the actions it took (a fixed number of queries)."""
        actions = AutomationAction.objects.select_related("member", "actor")
        return (
            self.filter(task_key=key)
            .select_related("actor")
            .prefetch_related(models.Prefetch("actions", queryset=actions))
            .order_by("-started_at")
        )


class ScheduledTaskRun(models.Model):
    """Append-only history of every scheduled-job run (scheduled or manual "Run now").

    Written only through :func:`core.scheduled_jobs.record_run`, which opens a row RUNNING
    and marks it OK/FAILED. History for a retired job survives (``task_key`` is a free string,
    not a FK), so the record outlives any registry change.
    """

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        OK = "ok", "OK"
        FAILED = "failed", "Failed"

    task_key = models.CharField(max_length=64, db_index=True, help_text="Registry key of the job this run belongs to.")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.RUNNING,
        help_text="Where the run got to: still running, finished OK, or failed.",
    )
    trigger = models.CharField(
        max_length=16,
        choices=Trigger.choices,
        default=Trigger.SCHEDULED,
        help_text="Whether the cron dispatched this run or an admin clicked Run now.",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The admin who clicked Run now; null for scheduled runs.",
    )
    started_at = models.DateTimeField(default=timezone.now, db_index=True, help_text="When the run began.")
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the run finished; null while running or if the process died mid-run.",
    )
    error = models.TextField(blank=True, default="", help_text="Exception text captured when the run failed.")

    objects = ScheduledTaskRunQuerySet.as_manager()

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["task_key", "-started_at"], name="idx_taskrun_key_recent")]

    def __str__(self) -> str:
        return f"{self.task_key} {self.status} @ {self.started_at:%Y-%m-%d %H:%M}"

    @property
    def succeeded(self) -> bool:
        return self.status == self.Status.OK

    @property
    def failed(self) -> bool:
        return self.status == self.Status.FAILED

    @property
    def duration(self) -> timedelta | None:
        """Wall-clock run time, or ``None`` while still running."""
        if self.finished_at is None:
            return None
        return self.finished_at - self.started_at

    @property
    def is_stale_running(self) -> bool:
        """A run left RUNNING for over an hour — the process almost certainly died; the
        dashboard renders this as "Unknown" rather than a perpetual "Running…"."""
        return self.status == self.Status.RUNNING and self.started_at < timezone.now() - timedelta(hours=1)

    def mark_ok(self) -> None:
        """Record a clean finish."""
        self.status = self.Status.OK
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "finished_at"])

    def mark_failed(self, exc: BaseException) -> None:
        """Record a failure and capture its message."""
        self.status = self.Status.FAILED
        self.finished_at = timezone.now()
        self.error = str(exc)
        self.save(update_fields=["status", "finished_at", "error"])


class AutomationAction(models.Model):
    """One thing an automation did to one person: a DM, a kick, a queue entry closed (#898).

    ``ScheduledTaskRun`` says a run happened; this says what the run did, so an admin can audit
    each step on the job's history page. Generic: any automation writes here through
    :meth:`record`, under its registry key. An admin's click outside a run (cancelling a queued
    removal) is recorded with ``actor`` set and no ``run``. Rows are kept when their run is pruned.
    """

    class Kind(models.TextChoices):
        DISCORD_DM = "discord_dm", "Discord DM"
        DISCORD_KICK = "discord_kick", "Removed from Discord"
        DISCORD_REJOIN = "discord_rejoin", "Put back on Discord"
        REMOVAL_QUEUED = "removal_queued", "Queued for removal"
        REMOVAL_CANCELLED = "removal_cancelled", "Removal cancelled"
        REMOVAL_CLOSED = "removal_closed", "Removal closed"

    class Result(models.TextChoices):
        OK = "ok", "OK"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    task_key = models.CharField(max_length=64, help_text="Registry key of the automation that took this action.")
    run = models.ForeignKey(
        ScheduledTaskRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="actions",
        help_text="The run that took this action; null for an admin's click or once the run is pruned.",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices, help_text="What the automation did.")
    result = models.CharField(max_length=16, choices=Result.choices, help_text="Whether it worked.")
    subject = models.CharField(
        max_length=200, help_text="Who it was done to, as shown on the history page (a name or a Discord username)."
    )
    member = models.ForeignKey(
        "membership.Member",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The member it was done to, when there is one.",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The admin who clicked, when an admin did it; null for the automation itself.",
    )
    detail = models.TextField(blank=True, default="", help_text="Discord's answer, the error, or why it closed.")
    created_at = models.DateTimeField(default=timezone.now, help_text="When the action was taken.")

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["task_key", "-created_at"], name="idx_autoaction_key_recent")]

    def __str__(self) -> str:
        return f"{self.task_key} {self.kind} {self.subject} {self.result}"

    @property
    def tone(self) -> str:
        """The ``hub-pill--*`` modifier for this action's result."""
        return {self.Result.OK: "ok", self.Result.FAILED: "danger", self.Result.SKIPPED: "neutral"}[
            self.Result(self.result)
        ]

    @classmethod
    def record(
        cls,
        task_key: str,
        *,
        kind: str,
        result: str,
        subject: str,
        member: Any = None,
        actor: User | None = None,
        detail: str = "",
    ) -> AutomationAction:
        """Write one action, attached to the run ``record_run`` has open for ``task_key``, if any."""
        from core.scheduled_jobs import current_run

        run = current_run()
        return cls.objects.create(
            task_key=task_key,
            run=run if run is not None and run.task_key == task_key else None,
            kind=kind,
            result=result,
            subject=subject[:200],
            member=member,
            actor=actor,
            detail=detail,
        )


class ScheduledJobStateManager(models.Manager["ScheduledJobState"]):
    """The current ON/OFF state per job. Absence of a row falls back to the job's registry default
    (``ScheduledJob.default_enabled``) — enabled for most jobs, but off for a default-off job."""

    def is_enabled(self, key: str) -> bool:
        """Whether the job may run. When a state row exists, its ``enabled`` value wins.
        Otherwise fall back to the registry's declared default (``ScheduledJob.default_enabled``):
        most jobs are default-on, but a job declared ``default_enabled=False`` stays OFF until an
        admin turns it on. An unknown key (no registry entry) defaults on — preserving the original
        "absence means enabled" behavior for anything not in the registry."""
        from core.scheduled_jobs import JOBS_BY_KEY

        row = self.filter(task_key=key).first()
        if row is not None:
            return row.enabled
        job = JOBS_BY_KEY.get(key)
        return job.default_enabled if job is not None else True

    def cadences(self) -> dict[str, str]:
        """Every admin-chosen frequency, keyed by task_key. A blank choice is left out."""
        return dict(self.exclude(cadence="").values_list("task_key", "cadence"))

    def set_enabled(self, key: str, enabled: bool, *, user: "User | None" = None) -> ScheduledJobState:
        """Flip a job on/off, recording who did it."""
        row, _ = self.update_or_create(
            task_key=key,
            defaults={
                "enabled": enabled,
                "updated_by": user if (user is not None and getattr(user, "pk", None)) else None,
            },
        )
        return row

    def sync_registry(self) -> None:
        """Ensure a state row exists for every registry job so the toggle formset always has a row
        to bind, seeded with the job's declared default (``default_enabled``). Idempotent; never
        deletes a row or re-defaults an existing one (a retired job's state, or an admin's flip,
        survives)."""
        from core.scheduled_jobs import SCHEDULED_JOBS

        for job in SCHEDULED_JOBS:
            self.get_or_create(task_key=job.key, defaults={"enabled": job.default_enabled})


class ScheduledJobState(models.Model):
    """Current ON/OFF state for one scheduled job. Absence of a row falls back to the job's registry
    default (``ScheduledJob.default_enabled``): default-on jobs still "just run" on a fresh database
    with zero seeding, while a default-off job stays off until an admin turns it on."""

    task_key = models.CharField(max_length=64, unique=True, help_text="Registry key of the job this state controls.")
    enabled = models.BooleanField(
        default=True,
        help_text="When off, the dispatcher skips this job. With no row at all the job's own default applies, "
        "which is off for jobs that ship disabled.",
    )
    updated_at = models.DateTimeField(auto_now=True, help_text="When this state was last changed.")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The admin who last flipped this job on or off.",
    )
    cadence = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text="How often the job runs, chosen by an admin on Automations for a job that offers a choice. "
        "Blank means the job's own schedule.",
    )

    objects = ScheduledJobStateManager()

    class Meta:
        ordering = ["task_key"]

    def __str__(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        return f"{self.task_key} ({state})"


class FeatureSwitchManager(models.Manager["FeatureSwitch"]):
    """The current state per member feature, against the ``core.features`` registry.

    Absence of a row means ON, so a database that has never been seeded behaves exactly like
    the app did before this table existed. That is the property that makes the deploy a no-op.
    """

    def state_of(self, key: str) -> str:
        """``key``'s current state, or ON when no row (or no registry entry) exists."""
        row = self.filter(feature_key=key).first()
        return row.state if row is not None else FeatureState.ON

    def sync_registry(self) -> None:
        """Ensure a state row exists for every registry feature so the admin formset always has
        one to bind, seeded ON. Idempotent; never deletes a row or re-defaults an existing one,
        so a retired feature's state and an admin's flip both survive."""
        for feature in FEATURES:
            self.get_or_create(feature_key=feature.key, defaults={"state": FeatureState.ON})

    def as_context(self) -> dict[str, FeatureView]:
        """Every feature's state, keyed by feature key, in ONE query.

        This is what the site-wide context processor delivers, so a page that renders every nav
        entry costs one read rather than one per feature. Registry order is preserved and a
        feature with no row still appears, ON — a template never has to test for a missing key,
        and a new feature needs no migration because its absent row simply reads ON.
        """
        rows = {row.feature_key: row for row in self.all()}
        views: dict[str, FeatureView] = {}
        for feature in FEATURES:
            row = rows.get(feature.key)
            state = row.state if row is not None else FeatureState.ON
            message = (row.message.strip() if row is not None else "") or DEFAULT_SOON_MESSAGE
            views[feature.key] = FeatureView(key=feature.key, name=feature.name, state=state, message=message)
        return views


class FeatureSwitch(models.Model):
    """On / Coming soon / Hidden for one member feature, plus the Coming soon hover message.

    One row per ``core.features.FEATURES`` key. The registry owns what a feature IS and what
    turning it off does; this row owns only the admin's current choice, which is why adding a
    feature needs no migration.
    """

    feature_key = models.CharField(
        max_length=64, unique=True, help_text="Registry key of the feature this state controls."
    )
    state = models.CharField(
        max_length=10,
        choices=FeatureState.choices,
        default=FeatureState.ON,
        help_text=(
            "On is normal behaviour. Coming soon leaves the sidebar entry visible but inert, showing the message below on hover and on keyboard focus. Hidden removes the entry. For every feature but My Tab, both off states change the sidebar only: every page in the feature stays reachable by its own link, for everyone. My Tab is the exception: both off states turn tab billing off."
        ),
    )
    message = models.CharField(
        max_length=200,
        blank=True,
        help_text=f"The hover text on a Coming soon entry, e.g. 'Launching Sept 30th!'. "
        f"Blank shows '{DEFAULT_SOON_MESSAGE}'. Ignored unless the state is Coming soon.",
    )
    updated_at = models.DateTimeField(auto_now=True, help_text="When this state was last changed.")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The admin who last changed this feature's state.",
    )

    objects = FeatureSwitchManager()

    class Meta:
        ordering = ["feature_key"]

    def __str__(self) -> str:
        return f"{self.feature_key} ({self.get_state_display()})"


class FeedbackRequestError(Exception):
    """A feedback request update an admin may not make; carries the copy to show them."""


def _member_replied_q() -> models.Q:
    """The sender wrote after an admin last opened the request (or none ever has)."""
    return models.Q(member_replied_at__isnull=False) & (
        models.Q(staff_seen_at__isnull=True) | models.Q(member_replied_at__gt=models.F("staff_seen_at"))
    )


class FeedbackRequestQuerySet(models.QuerySet["FeedbackRequest"]):
    """Query helpers for the Feedback page and the admin Feedback inbox (#693)."""

    def sent_by(self, user: User) -> FeedbackRequestQuerySet:
        """``user``'s own requests, newest first, with their photos and thread loaded up front."""
        return self.filter(user=user).with_thread().order_by("-created_at")

    def with_thread(self) -> FeedbackRequestQuerySet:
        """Load each request's photos and its comments, with their authors and photos."""
        return self.prefetch_related("photos", "comments__author__member", "comments__photos")

    def received(self) -> FeedbackRequestQuerySet:
        """Requests nobody has moved past Received yet: the inbox's to-do pile."""
        return self.filter(status=FeedbackRequest.Status.RECEIVED)

    def open(self) -> FeedbackRequestQuerySet:
        """The inbox's default view: not closed, or closed but the sender has written since."""
        return self.filter(~models.Q(status__in=FeedbackRequest.CLOSED_STATUSES) | _member_replied_q())

    def closed(self) -> FeedbackRequestQuerySet:
        """Requests in a closed status (:attr:`FeedbackRequest.CLOSED_STATUSES`)."""
        return self.filter(status__in=FeedbackRequest.CLOSED_STATUSES)

    def member_replied(self) -> FeedbackRequestQuerySet:
        """Requests whose sender wrote after an admin last opened them: the Member replied pile."""
        return self.filter(_member_replied_q())


class FeedbackRequest(models.Model):
    """One bug report, feature request or piece of feedback sent from the Feedback page (#693).

    The sender is a ``User``, not a ``Member``: the Feedback page is open to any signed-in
    account. Every message after the first is a :class:`FeedbackComment` in one thread both
    sides see; posting one always tells the other side (:meth:`add_comment`). An admin moves
    the request through :class:`Status` from the Feedback inbox, and a move to a
    :attr:`NOTIFYING_STATUSES` status tells the sender (:meth:`apply_admin_update`).
    """

    class Category(models.TextChoices):
        BUG = "bug", "Bug Report"
        FEATURE = "feature", "Feature Request"
        FEEDBACK = "feedback", "General Feedback"

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        PLANNED = "planned", "Planned"
        BUILDING = "building", "Building"
        LIVE = "live", "Live"
        NOT_PLANNED = "not_planned", "Not planned"
        NOT_A_BUG = "not_a_bug", "Not a bug"
        CLOSED = "closed", "Closed"

    # A move to one of these tells the sender; a move back to Received does not.
    NOTIFYING_STATUSES: frozenset[str] = frozenset(
        {Status.PLANNED, Status.BUILDING, Status.LIVE, Status.NOT_PLANNED, Status.NOT_A_BUG, Status.CLOSED}
    )

    # The one place that says which statuses close a request. The inbox's Open view leaves them
    # out unless the sender has written since; the sender can still reply on a closed request.
    CLOSED_STATUSES: frozenset[str] = frozenset({Status.LIVE, Status.NOT_PLANNED, Status.NOT_A_BUG, Status.CLOSED})

    # General feedback has no Planned or Live: it is answered with a comment, then maybe closed.
    FEEDBACK_STATUSES: frozenset[str] = frozenset({Status.RECEIVED, Status.CLOSED})

    # What the Not a bug comment box starts with; the admin finishes it every time.
    NOT_A_BUG_OPENER = "Thanks for reporting this. It is working as intended. Here is how to do it: "

    # How a status reads in a sentence: "Your request: <subject> is <phrase>".
    _STATUS_PHRASES: dict[str, str] = {
        Status.RECEIVED: "received",
        Status.PLANNED: "planned",
        Status.BUILDING: "being built",
        Status.LIVE: "live",
        Status.NOT_PLANNED: "not planned",
        Status.NOT_A_BUG: "closed as not a bug",
        Status.CLOSED: "closed",
    }

    # The existing hub-pill tone for each status.
    _STATUS_TONES: dict[str, str] = {
        Status.RECEIVED: "neutral",
        Status.PLANNED: "primary",
        Status.BUILDING: "primary",
        Status.LIVE: "ok",
        Status.NOT_PLANNED: "neutral",
        Status.NOT_A_BUG: "neutral",
        Status.CLOSED: "neutral",
    }

    NOT_PLANNED_NEEDS_REASON = "Write a comment that tells the member why. They see it with Not planned."
    NOT_A_BUG_NEEDS_HOW = "Write a comment that tells the member the right way to do it. They see it with Not a bug."
    NOT_A_BUG_BUGS_ONLY = "Only a bug report can be Not a bug. Close it instead."
    FEEDBACK_STAYS_RECEIVED = "General feedback is Received or Closed. Answer it with a comment, then close it."

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feedback_requests",
        help_text="Who sent it. Any signed-in account, with or without a membership.",
    )
    category = models.CharField(
        max_length=10,
        choices=Category.choices,
        help_text="What kind of request the sender picked on the Feedback page.",
    )
    subject = models.CharField(max_length=200, help_text="The sender's one-line summary.")
    message = models.TextField(help_text="The sender's full message.")
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.RECEIVED,
        help_text="Where the request stands. The sender sees it on the Feedback page.",
    )
    github_issue_url = models.URLField(
        blank=True,
        default="",
        help_text="The GitHub issue tracking the work. Only admins see it.",
    )
    status_changed_at = models.DateTimeField(
        default=timezone.now,
        help_text="When the status last changed; the sender sees this date.",
    )
    live_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the sender was last told this is live, so the automatic live notice never repeats it.",
    )
    member_replied_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the sender last wrote a comment on it.",
    )
    staff_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When an admin last opened its page in the Feedback inbox.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the sender sent it.")

    objects = FeedbackRequestQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="fbreq_status_created_idx"),
            models.Index(fields=["user", "-created_at"], name="fbreq_user_created_idx"),
        ]

    def __str__(self) -> str:
        return f"#{self.pk} {self.subject} ({self.get_status_display()})"

    @property
    def status_label(self) -> str:
        """The member's word for the status: a live bug report reads Fixed."""
        if self.status == self.Status.LIVE and self.category == self.Category.BUG:
            return "Fixed"
        return self.get_status_display()

    @property
    def status_phrase(self) -> str:
        """The status as it reads after "is": "being built", "fixed", "not planned"."""
        if self.status == self.Status.LIVE and self.category == self.Category.BUG:
            return "fixed"
        return self._STATUS_PHRASES[self.status]

    @property
    def status_tone(self) -> str:
        """The ``hub-pill--<tone>`` modifier for this status."""
        return self._STATUS_TONES[self.status]

    @property
    def stays_received(self) -> bool:
        """General feedback has no Planned or Live: an admin answers it with a comment, then may close it."""
        return self.category == self.Category.FEEDBACK

    @property
    def is_closed(self) -> bool:
        """Live (Fixed), Not planned, Not a bug or Closed: see :attr:`CLOSED_STATUSES`."""
        return self.status in self.CLOSED_STATUSES

    @property
    def has_member_reply(self) -> bool:
        """The sender wrote after an admin last opened the request: the inbox's Member replied pill.

        The same rule as :meth:`FeedbackRequestQuerySet.member_replied`, read off the row.
        """
        if self.member_replied_at is None:
            return False
        return self.staff_seen_at is None or self.member_replied_at > self.staff_seen_at

    @property
    def sender_label(self) -> str:
        """The sender's name, else their email: what the inbox and the admin's toast call them."""
        from membership.names import user_name_or_email

        return user_name_or_email(self.user)

    @property
    def anchor(self) -> str:
        """The element id of this request's row on the Feedback page."""
        return f"request-{self.pk}"

    @property
    def member_url(self) -> str:
        """Absolute link to this request, open, on the sender's Feedback page."""
        from django.urls import reverse

        return f"{settings.MEMBER_BASE_URL}{reverse('hub_beta_feedback')}#{self.anchor}"

    @property
    def inbox_url(self) -> str:
        """Absolute link to this request's page in the admin Feedback inbox."""
        from django.urls import reverse

        return f"{settings.MEMBER_BASE_URL}{reverse('hub_admin_feedback_request', args=[self.pk])}"

    @classmethod
    def submit(
        cls,
        *,
        user: User,
        category: str,
        subject: str,
        message: str,
        photos: list[UploadedFile],
    ) -> FeedbackRequest:
        """Save a request sent from the Feedback page, with its photos, as Received.

        Each upload is rewound before it is stored and after, so the caller can still read it
        (the email to the Webmasters attaches the same files).
        """
        with transaction.atomic():
            request = cls.objects.create(user=user, category=category, subject=subject, message=message)
            for photo in photos:
                photo.seek(0)
                FeedbackRequestPhoto.objects.create(request=request, image=photo)
                photo.seek(0)
        return request

    @classmethod
    def update_refusal(cls, *, category: str, current_status: str, status: str, comment: str) -> str | None:
        """Why an admin may not save this status and comment, or ``None`` when they may.

        General feedback is only Received or Closed, and only a bug report can be Not a bug. A move
        to Not planned or Not a bug needs a comment in the same save, so the sender reads why, or
        the right way to do the thing; for Not a bug the unedited opener does not count.
        """
        if category == cls.Category.FEEDBACK and status not in cls.FEEDBACK_STATUSES:
            return cls.FEEDBACK_STAYS_RECEIVED
        if status == cls.Status.NOT_A_BUG and category != cls.Category.BUG:
            return cls.NOT_A_BUG_BUGS_ONLY
        moving = current_status != status
        if moving and status == cls.Status.NOT_PLANNED and not comment.strip():
            return cls.NOT_PLANNED_NEEDS_REASON
        # The opener alone, unedited, tells the sender nothing: it counts as no comment.
        if moving and status == cls.Status.NOT_A_BUG and comment.strip() in {"", cls.NOT_A_BUG_OPENER.strip()}:
            return cls.NOT_A_BUG_NEEDS_HOW
        return None

    def add_comment(self, *, author: User, body: str, photos: list[UploadedFile]) -> FeedbackComment:
        """Post a comment on this request's thread and tell the other side, every time.

        The sender writing tells the Webmasters (minus the author) and puts the request under
        Member replied in the inbox; anyone else writing is Past Lives and tells the sender.

        Args:
            author: Who wrote it: the sender, or an admin.
            body: The comment's text.
            photos: Photos posted with it, already validated by the form.

        Returns:
            The saved comment; its ``notified`` says whether anyone was told
            (:meth:`FeedbackComment.notify_other_side`).
        """
        with transaction.atomic():
            comment = self._create_comment(author=author, body=body, photos=photos)
            comment.notify_other_side()
        return comment

    def _create_comment(self, *, author: User, body: str, photos: list[UploadedFile]) -> FeedbackComment:
        """Save a comment and its photos; tell nobody. A staff comment marks the request seen.

        A sender's comment stamps ``member_replied_at`` once it commits, in
        :meth:`FeedbackComment.notify_other_side`.
        """
        comment = FeedbackComment.objects.create(request=self, author=author, body=body.strip())
        for photo in photos:
            photo.seek(0)
            FeedbackCommentPhoto.objects.create(comment=comment, image=photo)
        if comment.is_from_staff:
            self.staff_seen_at = comment.created_at
            type(self).objects.filter(pk=self.pk).update(staff_seen_at=comment.created_at)
        return comment

    def mark_seen_by_staff(self) -> None:
        """Record that an admin opened this request, which clears its Member replied pill."""
        now = timezone.now()
        type(self).objects.filter(pk=self.pk).update(staff_seen_at=now)
        self.staff_seen_at = now

    def apply_admin_update(self, *, status: str, comment: str, github_issue_url: str, actor: User) -> bool:
        """Save an admin's status and GitHub link, post their comment, and tell the sender when it matters.

        A move to a :attr:`NOTIFYING_STATUSES` status tells the sender, and a comment written
        in the same save rides in that one notice. A comment with no such move is posted as
        any comment is, and tells the sender on its own. Saving the same status again with no
        comment changes nothing and notifies nobody.

        The compare and the write run on the row locked with ``select_for_update``, so of two
        identical moves at once (a double clicked Save) only the first notifies: the second
        reads the status the first wrote.

        Args:
            status: A :class:`Status` value.
            comment: A comment to the sender, or blank; required for a move to Not planned.
            github_issue_url: The tracking issue, or blank.
            actor: The admin saving it.

        Returns:
            Whether anyone was notified: the sender, or, when the admin is the sender, the
            other Webmasters.

        Raises:
            FeedbackRequestError: General feedback moved off Received, or a move to Not planned
                with a blank comment.
        """
        return self._apply(status=status, comment=comment.strip(), github_issue_url=github_issue_url, actor=actor)

    def mark_not_a_bug(self, *, actor: User, comment: str) -> bool:
        """Close a bug report as Not a bug, with the comment that tells the sender the right way.

        One save and one notice, as :meth:`apply_admin_update`, keeping the GitHub link.

        Raises:
            FeedbackRequestError: Not a bug report, or no comment.
        """
        return self._apply(status=self.Status.NOT_A_BUG, comment=comment.strip(), github_issue_url=None, actor=actor)

    def close(self, *, actor: User, comment: str) -> bool:
        """Close any request, general feedback included, with an optional comment, in one notice."""
        return self._apply(status=self.Status.CLOSED, comment=comment.strip(), github_issue_url=None, actor=actor)

    def mark_live(self, *, actor: User) -> bool:
        """Move the request to Live (Fixed for a bug), keeping its GitHub link.

        See :meth:`apply_admin_update`.

        Raises:
            FeedbackRequestError: The request is general feedback, which stays Received.
        """
        return self._apply(status=self.Status.LIVE, comment="", github_issue_url=None, actor=actor)

    def _apply(self, *, status: str, comment: str, github_issue_url: str | None, actor: User) -> bool:
        """The locked read, compare, write and notify behind both admin updates.

        ``None`` for the link keeps what the locked row holds.
        """
        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            refusal = self.update_refusal(
                category=locked.category, current_status=locked.status, status=status, comment=comment
            )
            if refusal is not None:
                raise FeedbackRequestError(refusal)
            status_changed = status != locked.status
            moved = status_changed and status in self.NOTIFYING_STATUSES
            now = timezone.now()
            locked.status = status
            if github_issue_url is not None:
                locked.github_issue_url = github_issue_url
            if status_changed:
                locked.status_changed_at = now
            if moved and status == self.Status.LIVE:
                locked.live_notified_at = now
            locked.save()
            posted = locked._create_comment(author=actor, body=comment, photos=[]) if comment else None
            # An admin moving their own request is not told about it; their comment, if any, is
            # then a sender's comment and goes where any sender's comment goes.
            if moved and actor.pk != locked.user_id:
                locked._notify_sender(
                    actor=actor,
                    note=comment,
                    period=f"feedback:{locked.pk}:{status}:{now:%Y%m%d%H%M%S%f}",
                )
                notified = True
            else:
                notified = posted.notify_other_side() if posted is not None else False
        self.refresh_from_db()
        return notified

    def live_announcement_skip(self) -> str | None:
        """Why a release listing this request must not announce it, or ``None`` when it may.

        Already told it is live covers a request an admin marked Live by hand and one the job
        announced before (``live_notified_at``), including after an admin moved it back.
        """
        if self.stays_received:
            return "is general feedback, which stays Received"
        if self.status == self.Status.NOT_A_BUG:
            return "is closed as Not a bug"
        if self.status == self.Status.CLOSED:
            return "is Closed"
        if self.status == self.Status.LIVE:
            return "is already Live"
        if self.live_notified_at is not None:
            return "was already told it is live"
        return None

    def announce_live(self, *, release_title: str, changelog_url: str) -> str | None:
        """Mark the request Live because a release listing it shipped, and tell the sender once.

        Runs on the row locked with ``select_for_update``, re-checking
        :meth:`live_announcement_skip` there, so two overlapping job runs announce it once.

        Args:
            release_title: The changelog entry's title, named in the notice.
            changelog_url: Absolute link that opens that entry in the changelog.

        Returns:
            ``None`` when announced, else why it was skipped.
        """
        from core.events.emit import emit
        from core.events.registry import FEEDBACK_REQUEST_LIVE

        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            skip = locked.live_announcement_skip()
            if skip is not None:
                return skip
            now = timezone.now()
            locked.status = self.Status.LIVE
            locked.status_changed_at = now
            locked.live_notified_at = now
            locked.save()
            emit(
                FEEDBACK_REQUEST_LIVE,
                target=locked,
                context={
                    "user": locked.user,
                    "request_subject": locked.subject,
                    "status_phrase": locked.status_phrase,
                    "release_title": release_title,
                    "changelog_url": changelog_url,
                    "request_url": locked.member_url,
                },
                url=changelog_url,
                period=f"feedback:{locked.pk}:live-release",
            )
        self.refresh_from_db()
        return None

    def _notify_sender(self, *, actor: User, note: str, period: str) -> None:
        """Tell the sender where their request stands, with ``note`` when this update wrote one."""
        from django.utils.html import linebreaks
        from django.utils.safestring import mark_safe

        from core.events.emit import emit
        from core.events.registry import FEEDBACK_REQUEST_UPDATED

        # The copy renderer has no conditionals, so the note arrives pre-built, and empty when
        # unset. linebreaks(autoescape=True) escapes the admin's words before they are marked safe.
        note_line = f"A note from Past Lives:\n{note}\n\n" if note else ""
        note_block = (
            mark_safe("<p><strong>A note from Past Lives:</strong></p>" + linebreaks(note, autoescape=True))
            if note
            else ""
        )
        emit(
            FEEDBACK_REQUEST_UPDATED,
            actor=actor,
            target=self,
            context={
                "user": self.user,
                "request_subject": self.subject,
                "status_label": self.status_label,
                "status_phrase": self.status_phrase,
                "request_url": self.member_url,
                "note_line": note_line,
                "note_block": note_block,
            },
            url=self.member_url,
            period=period,
        )


class FeedbackRequestPhoto(models.Model):
    """One photo or screenshot sent with a :class:`FeedbackRequest` itself."""

    request = models.ForeignKey(
        FeedbackRequest,
        on_delete=models.CASCADE,
        related_name="photos",
        help_text="The request this photo was sent with.",
    )
    image = models.ImageField(upload_to="feedback/%Y/%m/", help_text="The uploaded photo, on the default storage.")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was uploaded.")

    class Meta:
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"Photo {self.pk} on request #{self.request_id}"


class FeedbackComment(models.Model):
    """One message on a :class:`FeedbackRequest`'s thread, from the sender or from Past Lives.

    Who it is from is derived, never stored: a comment by anyone but the request's sender is
    from staff, and so is one with no author (a note written before comments existed).
    """

    # What a comment with no author, or an admin with no name, signs as.
    STAFF_SIGNATURE = "Past Lives"

    # Set by notify_other_side: whether posting it told anyone. Not a column.
    notified: bool = False

    request = models.ForeignKey(
        FeedbackRequest,
        on_delete=models.CASCADE,
        related_name="comments",
        help_text="The request this comment is on.",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedback_comments",
        help_text="Who wrote it: the sender or an admin. Blank for a note carried over from before comments.",
    )
    body = models.TextField(help_text="What they wrote.")
    created_at = models.DateTimeField(default=timezone.now, help_text="When it was posted.")

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"Comment {self.pk} on request #{self.request_id}"

    @property
    def is_from_staff(self) -> bool:
        """Written by anyone but the request's sender, or carried over with no author."""
        return self.author_id is None or self.author_id != self.request.user_id

    @property
    def author_name(self) -> str:
        """The name the thread shows: the sender's label, an admin's name, else Past Lives."""
        if not self.is_from_staff:
            return self.request.sender_label
        from membership.names import user_display_name

        return (user_display_name(self.author) if self.author is not None else "") or self.STAFF_SIGNATURE

    @property
    def shows_staff_tag(self) -> bool:
        """An admin's comment signed with their own name carries a Past Lives tag; Past Lives alone does not."""
        return self.is_from_staff and self.author_name != self.STAFF_SIGNATURE

    def _notify_sender(self) -> None:
        """Tell the request's sender that Past Lives wrote this comment."""
        from core.events.emit import emit
        from core.events.registry import FEEDBACK_COMMENT_ADDED

        request = self.request
        emit(
            FEEDBACK_COMMENT_ADDED,
            actor=self.author,
            target=request,
            context={
                "user": request.user,
                "request_subject": request.subject,
                "comment_body": self.body,
                "request_url": request.member_url,
            },
            url=request.member_url,
            period=f"feedback-comment-{self.pk}",
        )

    def notify_other_side(self) -> bool:
        """Tell whoever is on the other side of this comment; the one rule for both posting paths.

        A staff comment tells the sender. A sender's comment (an admin's on their own request
        too) tells the Webmasters minus the author, and stamps ``member_replied_at``; both run
        once the comment commits, so the stamp is never older than the moment an admin could
        first see the comment, and an admin opening the page in between cannot clear it.

        Returns:
            Whether anyone was told; also kept on :attr:`notified`.
        """
        if self.is_from_staff:
            self._notify_sender()
            self.notified = True
            return True
        from core.events.resolvers import webmasters

        recipient_ids = {user.pk for user, _reason in webmasters({}) if user.pk != self.author_id}
        transaction.on_commit(lambda: self._replied(recipient_ids))
        self.notified = bool(recipient_ids)
        return self.notified

    def _replied(self, recipient_ids: set[int]) -> None:
        """After commit: put the request under Member replied, and tell ``recipient_ids``."""
        from core.events.emit import emit
        from core.events.registry import FEEDBACK_MEMBER_REPLIED

        request = self.request
        now = timezone.now()
        FeedbackRequest.objects.filter(pk=request.pk).update(member_replied_at=now)
        request.member_replied_at = now
        if not recipient_ids:
            return
        emit(
            FEEDBACK_MEMBER_REPLIED,
            actor=self.author,
            target=request,
            context={
                "sender_name": request.sender_label,
                "request_subject": request.subject,
                "comment_body": self.body,
                "inbox_url": request.inbox_url,
            },
            url=request.inbox_url,
            period=f"feedback-comment-{self.pk}",
            recipient_user_ids=recipient_ids,
        )


class FeedbackCommentPhoto(models.Model):
    """One photo or screenshot posted with a :class:`FeedbackComment`."""

    comment = models.ForeignKey(
        FeedbackComment,
        on_delete=models.CASCADE,
        related_name="photos",
        help_text="The comment this photo was posted with.",
    )
    image = models.ImageField(upload_to="feedback/%Y/%m/", help_text="The uploaded photo, on the default storage.")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was uploaded.")

    class Meta:
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"Photo {self.pk} on comment {self.comment_id}"


# ── TEMPORARY — remove on/after 2026-08-10 ─────────────────────────────────────
# Copy-review gallery anonymous comments. A throwaway review aid, NOT a
# member-facing feature. See docs/superpowers/plans/2026-07-27-copy-review-comments.md.
# Teardown = revert the feature PR; the create migration reverses cleanly.
class CopyReviewCommentManager(models.Manager["CopyReviewComment"]):
    """Active-only manager (hides soft-deleted rows) plus create/grouping helpers.

    TEMPORARY — remove on/after 2026-08-10.
    """

    def get_queryset(self) -> models.QuerySet[CopyReviewComment]:
        return super().get_queryset().filter(deleted_at__isnull=True)

    def post(self, *, section_key: str, author_name: str, body: str) -> CopyReviewComment:
        """Create a comment with a freshly generated edit_token."""
        return self.create(
            section_key=section_key,
            author_name=author_name,
            body=body,
            edit_token=secrets.token_hex(16),
        )

    def grouped(self) -> dict[str, list[CopyReviewComment]]:
        """All active comments grouped by section_key, each list oldest-first."""
        grouped: dict[str, list[CopyReviewComment]] = {}
        for comment in self.get_queryset():
            grouped.setdefault(comment.section_key, []).append(comment)
        return grouped


class CopyReviewComment(models.Model):
    """A public, unauthenticated comment on one section of the copy-review gallery.

    TEMPORARY — the whole copy-review comments feature is a ~2-week review aid.
    Remove on/after 2026-08-10 (see docs/superpowers/plans/2026-07-27-copy-review-comments.md).
    """

    section_key = models.CharField(
        max_length=200,
        db_index=True,
        help_text="Stable slug of the gallery section this comment belongs to (surface--label).",
    )
    author_name = models.CharField(
        max_length=80,
        help_text="Free-text name the commenter attributed the comment to. Not a user account.",
    )
    body = models.TextField(help_text="The comment text (max 2000 chars, enforced by the form).")
    edit_token = models.CharField(
        max_length=64,
        help_text="Secret returned once on create; required to edit or delete this comment.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the comment was posted.")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the comment was last edited.")
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the commenter soft-deletes the comment.",
    )

    objects = CopyReviewCommentManager()  # active only (deleted_at IS NULL)
    all_objects = models.Manager()  # includes soft-deleted

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.author_name} on {self.section_key} ({self.created_at:%Y-%m-%d})"

    def owned_by(self, token: str) -> bool:
        """Constant-time check that ``token`` matches this comment's edit_token."""
        return bool(token) and secrets.compare_digest(self.edit_token, token)

    def apply_edit(self, author_name: str, body: str) -> None:
        """Replace the author name and body, refreshing ``updated_at``."""
        self.author_name = author_name
        self.body = body
        self.save(update_fields=["author_name", "body", "updated_at"])

    def soft_delete(self) -> None:
        """Hide the comment from the active manager without deleting the row."""
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])

    def as_public_dict(self) -> dict[str, object]:
        """Serialize for the JSON API. NEVER includes ``edit_token``."""
        return {
            "id": self.pk,
            "section_key": self.section_key,
            "author_name": self.author_name,
            "body": self.body,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


# ── END TEMPORARY (copy-review comments) ───────────────────────────────────────
