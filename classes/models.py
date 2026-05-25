"""Models for the Classes app."""

from __future__ import annotations

import secrets
from datetime import date as date_type

from django.conf import settings
from django.db import models
from django.db.models import CheckConstraint, F, Q
from django.utils import timezone

from core.files import delete_orphan_on_replace
from core.images import normalize_field_if_uploaded
from core.validators import validate_image_size

DEFAULT_LIABILITY_TEXT = """ASSUMPTION OF RISK AND WAIVER OF LIABILITY

I understand that participation in classes, workshops, and activities at Past Lives Makerspace ("PLM") involves inherent risks, including but not limited to: exposure to tools, machinery, and equipment; risk of cuts, burns, eye injury, hearing damage, and other physical harm; and exposure to dust, fumes, chemicals, and other materials.

I voluntarily assume all risks associated with my participation. I hereby release, waive, and discharge PLM, its owners, officers, employees, instructors, volunteers, and agents from any and all liability, claims, demands, or causes of action arising out of or related to my participation, including negligence.

I agree to follow all safety rules, instructions, and guidelines provided by PLM and its instructors. I understand that failure to do so may result in removal from the class without refund.

I confirm that I am at least 18 years of age (or have a parent/guardian signing on my behalf), that I am physically able to participate, and that I carry my own health insurance or accept financial responsibility for any medical treatment I may require.

Past Lives Makerspace LLC, 2808 SE 9th Ave, Portland, OR 97202"""


DEFAULT_MODEL_RELEASE_TEXT = """MODEL RELEASE AND CONSENT TO USE OF IMAGE

I grant Past Lives Makerspace ("PLM"), its employees, and agents the right to photograph, video record, and otherwise capture my likeness during classes and events, and to use such images for promotional, educational, and marketing purposes including but not limited to: website, social media, printed materials, and press.

I waive any right to inspect or approve the finished images or the use to which they may be applied. I release PLM from any claims arising from the use of my likeness.

I understand that I may revoke this consent at any time by notifying PLM in writing at info@pastlives.space."""


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True, help_text="Display name (e.g. Woodworking).")
    slug = models.SlugField(max_length=100, unique=True, help_text="URL slug.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending sort; lower shows first.")
    hero_image = models.ImageField(
        upload_to="classes/categories/",
        blank=True,
        validators=[validate_image_size],
        help_text="Optional header image.",
    )
    guild = models.ForeignKey(
        "membership.Guild",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="categories",
        help_text="Optional link to the makerspace Guild that owns this category. Used for Mailchimp tagging.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "Categories"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        from django.conf import settings

        delete_orphan_on_replace(self, "hero_image")
        normalize_field_if_uploaded(self, "hero_image", settings.IMAGE_MAX_LONG_EDGE_HERO)
        super().save(*args, **kwargs)


class Instructor(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="instructor",
        help_text="Auth identity — required.",
    )
    display_name = models.CharField(max_length=255, help_text="Public name shown on class pages.")
    slug = models.SlugField(max_length=255, unique=True, help_text="URL slug for public profile.")
    bio = models.TextField(blank=True, help_text="Markdown-safe bio shown on profile.")
    photo = models.ImageField(
        upload_to="classes/instructors/",
        blank=True,
        validators=[validate_image_size],
        help_text="Profile photo.",
    )
    website = models.URLField(blank=True, help_text="Personal site.")
    social_handle = models.CharField(max_length=255, blank=True, help_text="e.g. @handle on primary social.")
    is_active = models.BooleanField(default=True, help_text="Inactive instructors hidden from public browse.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self) -> str:
        return self.display_name

    def save(self, *args, **kwargs) -> None:
        from django.conf import settings

        delete_orphan_on_replace(self, "photo")
        normalize_field_if_uploaded(self, "photo", settings.IMAGE_MAX_LONG_EDGE_PROFILE)
        super().save(*args, **kwargs)


class ClassOfferingQuerySet(models.QuerySet["ClassOffering"]):
    def public(self) -> "ClassOfferingQuerySet":
        """Published classes visible in the public portal (excludes private)."""
        return self.filter(status="published", is_private=False)

    def pending_review(self) -> "ClassOfferingQuerySet":
        return self.filter(status="pending")

    def for_instructor(self, instructor: "Instructor") -> "ClassOfferingQuerySet":
        return self.filter(instructor=instructor)


class ClassOffering(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending Review"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    class SchedulingModel(models.TextChoices):
        FIXED = "fixed", "Fixed sessions"
        FLEXIBLE = "flexible", "Flexible (arrange with instructor)"

    title = models.CharField(max_length=255, help_text="Public class title.")
    slug = models.SlugField(max_length=255, unique=True, help_text="URL slug.")
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="classes", help_text="Category grouping."
    )
    instructor = models.ForeignKey(
        Instructor, on_delete=models.PROTECT, related_name="classes", help_text="Assigned instructor."
    )
    description = models.TextField(blank=True, help_text="Class description — markdown-safe.")
    prerequisites = models.TextField(blank=True, help_text="What a student should know/own.")
    materials_included = models.TextField(blank=True, help_text="Included materials.")
    materials_to_bring = models.TextField(blank=True, help_text="What students must bring.")
    safety_requirements = models.TextField(blank=True, help_text="PPE or other safety requirements.")
    age_minimum = models.PositiveIntegerField(null=True, blank=True, help_text="Minimum age.")
    age_guardian_note = models.TextField(blank=True, help_text="Notes about minors / guardians.")
    price_cents = models.PositiveIntegerField(help_text="Full price in cents.")
    member_discount_pct = models.PositiveIntegerField(default=10, help_text="Auto-applied for verified members.")
    capacity = models.PositiveIntegerField(default=6, help_text="Maximum confirmed registrants.")
    scheduling_model = models.CharField(
        max_length=10,
        choices=SchedulingModel.choices,
        default=SchedulingModel.FIXED,
        help_text="Fixed scheduled sessions or flexible per-student scheduling.",
    )
    flexible_note = models.TextField(blank=True, help_text="Notes when scheduling_model=flexible.")
    is_private = models.BooleanField(default=False, help_text="Hidden from public portal; private registration only.")
    private_for_name = models.CharField(max_length=255, blank=True, help_text="Name shown when private.")
    recurring_pattern = models.CharField(max_length=255, blank=True, help_text="Free-text recurrence description.")
    image = models.ImageField(
        upload_to="classes/images/",
        blank=True,
        validators=[validate_image_size],
        help_text="Hero image.",
    )
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
    requires_model_release = models.BooleanField(
        default=False, help_text="When on, registrants also sign model release."
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, help_text="Lifecycle status."
    )
    created_by = models.ForeignKey(
        Instructor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Instructor who authored the class.",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Admin user who approved publication.",
    )
    published_at = models.DateTimeField(null=True, blank=True, help_text="Stamp on first publish.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ClassOfferingQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs) -> None:
        from django.conf import settings

        delete_orphan_on_replace(self, "image")
        # If the hero image is changing, also clear the stale crop box.
        if self.pk:
            try:
                old = type(self)._default_manager.only("image").get(pk=self.pk)
            except type(self).DoesNotExist:
                old = None
            new_name = getattr(self.image, "name", "") or ""
            old_name = getattr(getattr(old, "image", None), "name", "") or ""
            if old is not None and old_name and old_name != new_name:
                self.hero_crop_x = None
                self.hero_crop_y = None
                self.hero_crop_w = None
                self.hero_crop_h = None
        normalize_field_if_uploaded(self, "image", settings.IMAGE_MAX_LONG_EDGE_HERO)
        super().save(*args, **kwargs)

    def submit_for_review(self) -> None:
        if self.status != self.Status.DRAFT:
            raise ValueError(f"Only draft classes can be submitted; got {self.status}.")
        self.status = self.Status.PENDING
        self.save(update_fields=["status", "updated_at"])

    def approve(self, admin_user) -> None:
        if self.status != self.Status.PENDING:
            raise ValueError(f"Only pending classes can be approved; got {self.status}.")
        self.status = self.Status.PUBLISHED
        self.approved_by = admin_user
        self.published_at = timezone.now()
        self.save(update_fields=["status", "approved_by", "published_at", "updated_at"])

    def archive(self) -> None:
        self.status = self.Status.ARCHIVED
        self.save(update_fields=["status", "updated_at"])

    @property
    def spots_remaining(self) -> int:
        """Capacity minus current confirmed + pending registrations."""
        used = self.registrations.filter(
            status__in=[Registration.Status.CONFIRMED, Registration.Status.PENDING]
        ).count()
        return max(0, self.capacity - used)

    @property
    def display_images(self) -> list[dict]:
        """Ordered image list for the public detail gallery.

        Combines the hero (offering.image) with the gallery_images rows. When
        no images at all are uploaded, falls back to the category hero so the
        detail page never renders an empty hero. Each entry is ``{"url": str,
        "alt": str}`` so the template doesn't need to know whether a row came
        from a ClassImage or the ClassOffering itself.
        """
        items: list[dict] = []
        if self.image:
            items.append({"url": self.image.url, "alt": self.title})
        for gi in self.gallery_images.all():
            items.append({"url": gi.image.url, "alt": gi.alt_text or self.title})
        if not items and self.category and self.category.hero_image:
            items.append({"url": self.category.hero_image.url, "alt": self.category.name})
        return items

    @property
    def hero_object_position(self) -> str:
        """CSS ``object-position`` value to keep the cropped focal point centered.

        When the hero crop box is set, returns ``"X% Y%"`` where the coords are
        the crop-box center expressed as a percentage of the source image.
        Templates pair this with ``object-fit: cover`` on the hero <img>/banner.
        Returns ``"50% 50%"`` (CSS default) when the crop box or source size is
        unknown.
        """
        if not (self.hero_crop_w and self.hero_crop_h):
            return "50% 50%"
        try:
            src_w = self.image.width
            src_h = self.image.height
        except (FileNotFoundError, ValueError, AttributeError, OSError):
            return "50% 50%"
        if not (src_w and src_h):
            return "50% 50%"
        cx = (self.hero_crop_x or 0) + self.hero_crop_w / 2
        cy = (self.hero_crop_y or 0) + self.hero_crop_h / 2
        return f"{(cx / src_w) * 100:.1f}% {(cy / src_h) * 100:.1f}%"

    @property
    def first_upcoming_session_at(self):
        session = self.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at").first()
        return session.starts_at if session else None

    def duplicate(self) -> "ClassOffering":
        """Clone this offering as a fresh draft with a unique slug and title."""
        base_slug = f"{self.slug}-copy"
        slug = base_slug
        n = 1
        while ClassOffering.objects.filter(slug=slug).exists():
            n += 1
            slug = f"{base_slug}-{n}"
        self.pk = None
        self.slug = slug
        self.title = f"{self.title} (copy)"
        self.status = self.Status.DRAFT
        self.published_at = None
        self.approved_by = None
        self.save()
        return self


class ClassImage(models.Model):
    """Additional gallery image for a class.

    The ClassOffering.image field remains the single hero/banner; rows here
    are the extra gallery shots shown below the hero on the public detail
    page. Ordering uses sort_order then created_at so instructors can drag
    images around without rewriting timestamps.
    """

    class_offering = models.ForeignKey(
        ClassOffering,
        on_delete=models.CASCADE,
        related_name="gallery_images",
        help_text="Parent class offering.",
    )
    image = models.ImageField(
        upload_to="classes/images/",
        validators=[validate_image_size],
        help_text="Additional class photo.",
    )
    alt_text = models.CharField(
        max_length=255,
        blank=True,
        help_text="Short description of the image for accessibility.",
    )
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "created_at"]

    def __str__(self) -> str:
        return f"Image #{self.pk} for {self.class_offering.title}"

    def save(self, *args, **kwargs) -> None:
        from django.conf import settings

        delete_orphan_on_replace(self, "image")
        normalize_field_if_uploaded(self, "image", settings.IMAGE_MAX_LONG_EDGE_GALLERY)
        super().save(*args, **kwargs)


class ClassSession(models.Model):
    class_offering = models.ForeignKey(
        ClassOffering,
        on_delete=models.CASCADE,
        related_name="sessions",
        help_text="Parent class offering.",
    )
    starts_at = models.DateTimeField(help_text="Start (timezone-aware).")
    ends_at = models.DateTimeField(help_text="End (timezone-aware).")
    sort_order = models.PositiveIntegerField(default=0, help_text="Display order within a class.")

    class Meta:
        ordering = ["starts_at"]
        constraints = [CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="session_ends_after_starts")]

    def __str__(self) -> str:
        return f"{self.class_offering.title} — {self.starts_at:%Y-%m-%d}"


class DiscountCode(models.Model):
    code = models.CharField(max_length=40, unique=True, help_text="Uppercase code — normalized on save.")
    description = models.CharField(max_length=255, blank=True, help_text="Admin-only description.")
    discount_pct = models.PositiveIntegerField(null=True, blank=True, help_text="Percent off (0-100).")
    discount_fixed_cents = models.PositiveIntegerField(null=True, blank=True, help_text="Flat cents off.")
    valid_from = models.DateField(null=True, blank=True, help_text="First date the code is valid.")
    valid_until = models.DateField(null=True, blank=True, help_text="Last date the code is valid.")
    max_uses = models.PositiveIntegerField(null=True, blank=True, help_text="Cap total uses. Null = unlimited.")
    use_count = models.PositiveIntegerField(default=0, help_text="Incremented on each successful registration.")
    is_active = models.BooleanField(default=True, help_text="Admin toggle to disable without deleting.")
    is_approved = models.BooleanField(
        default=True, help_text="Admin-approved codes are usable. Instructor-created codes start unapproved."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(discount_pct__isnull=False) | Q(discount_fixed_cents__isnull=False)),
                name="discount_has_value",
            ),
        ]

    def __str__(self) -> str:
        return self.code

    def save(self, *args, **kwargs) -> None:
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def apply_to(self, price_cents: int) -> int:
        if self.discount_pct is not None:
            return int(price_cents * (100 - self.discount_pct) / 100)
        if self.discount_fixed_cents is not None:
            return max(0, price_cents - self.discount_fixed_cents)
        return price_cents

    def is_currently_valid(self) -> bool:
        if not self.is_active:
            return False
        if not self.is_approved:
            return False
        today = date_type.today()
        if self.valid_from and today < self.valid_from:
            return False
        if self.valid_until and today > self.valid_until:
            return False
        if self.max_uses is not None and self.use_count >= self.max_uses:
            return False
        return True


class Waiver(models.Model):
    class Kind(models.TextChoices):
        LIABILITY = "liability", "Liability"
        MODEL_RELEASE = "model_release", "Model Release"

    registration = models.ForeignKey(
        "Registration",
        on_delete=models.CASCADE,
        related_name="waivers",
        help_text="The registration this waiver belongs to.",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, help_text="Which waiver was signed.")
    waiver_text = models.TextField(help_text="Full text as shown at time of signing (audit record).")
    signature_text = models.CharField(max_length=255, help_text="Name typed as signature.")
    ip_address = models.GenericIPAddressField(null=True, blank=True, help_text="Client IP at time of signing.")
    signed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-signed_at"]
        constraints = [
            models.UniqueConstraint(fields=["registration", "kind"], name="uq_waiver_registration_kind"),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} for registration {self.registration_id}"


class RegistrationReminder(models.Model):
    registration = models.ForeignKey(
        "Registration",
        on_delete=models.CASCADE,
        related_name="reminders",
        help_text="The registration the reminder was sent to.",
    )
    session = models.ForeignKey(
        ClassSession,
        on_delete=models.CASCADE,
        related_name="reminders",
        help_text="The session the reminder referenced.",
    )
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at"]
        constraints = [
            models.UniqueConstraint(fields=["registration", "session"], name="uq_reminder_registration_session"),
        ]

    def __str__(self) -> str:
        return f"Reminder for registration {self.registration_id} → session {self.session_id}"


class Registration(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending payment"
        CONFIRMED = "confirmed", "Confirmed"
        WAITLISTED = "waitlisted", "Waitlisted"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    class_offering = models.ForeignKey(
        ClassOffering,
        on_delete=models.PROTECT,
        related_name="registrations",
        help_text="The class this registration is for.",
    )
    member = models.ForeignKey(
        "membership.Member",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="class_registrations",
        help_text="Auto-linked when email matches a verified Member email.",
    )
    first_name = models.CharField(max_length=100, help_text="Registrant first name.")
    last_name = models.CharField(max_length=100, help_text="Registrant last name.")
    pronouns = models.CharField(max_length=50, blank=True, help_text="Optional pronouns.")
    email = models.EmailField(help_text="Registrant email — drives member linking + self-serve link.")
    phone = models.CharField(max_length=20, blank=True, help_text="Optional phone.")
    address_line1 = models.CharField(max_length=255, blank=True, help_text="Street address (optional).")
    address_city = models.CharField(max_length=100, blank=True, help_text="City (optional).")
    address_state = models.CharField(max_length=50, blank=True, help_text="State or region (optional).")
    address_zip = models.CharField(max_length=20, blank=True, help_text="Postal / ZIP code (optional).")
    prior_experience = models.TextField(blank=True, help_text="Free-text prior-experience question.")
    looking_for = models.TextField(
        blank=True, help_text="Free-text 'what are you hoping to get out of this?' question."
    )
    discount_code = models.ForeignKey(
        DiscountCode,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Discount code used at registration, if any.",
    )
    amount_paid_cents = models.PositiveIntegerField(default=0, help_text="Amount actually paid (after discount).")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text="Lifecycle status.",
    )
    stripe_session_id = models.CharField(max_length=255, blank=True, help_text="Stripe Checkout Session ID.")
    stripe_payment_id = models.CharField(max_length=255, blank=True, help_text="Stripe PaymentIntent ID on confirm.")
    self_serve_token = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        help_text="Random token used in /classes/my/<token>/ self-serve URL.",
    )
    order_number = models.CharField(
        max_length=12,
        blank=True,
        unique=True,
        help_text="Human-readable confirmation number shown in emails and used by the guest lookup flow (PL-XXXX-YY).",
    )
    wants_newsletter = models.BooleanField(
        default=False,
        help_text="Did the registrant tick the newsletter opt-in box at signup?",
    )
    subscribed_to_mailchimp = models.BooleanField(default=False, help_text="Whether MailChimp subscribe succeeded.")
    cancellation_reason = models.TextField(blank=True, help_text="Internal reason recorded when an admin cancels.")
    registered_at = models.DateTimeField(auto_now_add=True, help_text="When this registration was created.")
    confirmed_at = models.DateTimeField(null=True, blank=True, help_text="When payment confirmed, if any.")
    cancelled_at = models.DateTimeField(null=True, blank=True, help_text="When this registration was cancelled.")

    class Meta:
        ordering = ["-registered_at"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["class_offering", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.email} → {self.class_offering.title}"

    def save(self, *args, **kwargs) -> None:
        creating = self._state.adding
        if creating and not self.self_serve_token:
            self.self_serve_token = secrets.token_urlsafe(48)
        if creating and not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)
        if creating and self.member_id is None:
            self.link_member_by_email()

    @staticmethod
    def _generate_order_number() -> str:
        """Generate a unique PL-XXXX-YY order number.

        XXXX uses an unambiguous alphabet (no 0/O/I/1 to avoid typos). YY is
        the last two digits of the current year. Retries up to 40 times if
        the random pick collides with an existing row.
        """
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 32 chars; no 0, O, I, 1
        year_suffix = timezone.now().strftime("%y")
        for _ in range(40):
            candidate = "PL-" + "".join(secrets.choice(alphabet) for _ in range(4)) + f"-{year_suffix}"
            if not Registration.objects.filter(order_number=candidate).exists():
                return candidate
        raise RuntimeError("Failed to generate a unique order number after 40 tries.")

    def link_member_by_email(self) -> None:
        from membership.models import Member

        match = (
            Member.objects.filter(
                user__emailaddress__email__iexact=self.email,
                user__emailaddress__verified=True,
            )
            .distinct()
            .first()
        )
        if match is not None:
            self.member = match
            super().save(update_fields=["member"])

    def cancel(self, reason: str = "") -> None:
        self.status = self.Status.CANCELLED
        self.cancelled_at = timezone.now()
        self.cancellation_reason = reason
        self.save(update_fields=["status", "cancelled_at", "cancellation_reason"])


class RegistrationQuestion(models.Model):
    """A custom question asked of every registrant on every class registration.

    Global by design — no per-class or per-category attachment. Admins curate
    the list via the Django admin. To retire a question without losing
    historical answers, uncheck ``is_active`` rather than deleting.
    """

    class QuestionType(models.TextChoices):
        SHORT_TEXT = "short_text", "Short text"
        LONG_TEXT = "long_text", "Long text"
        YES_NO = "yes_no", "Yes/No"
        SINGLE_CHOICE = "single_choice", "Single choice"

    prompt = models.CharField(max_length=500, help_text="The question shown to the registrant.")
    question_type = models.CharField(
        max_length=20,
        choices=QuestionType.choices,
        default=QuestionType.SHORT_TEXT,
        help_text="Input style — short text, paragraph, yes/no, or pick-one.",
    )
    choices_json = models.JSONField(
        default=list,
        blank=True,
        help_text="Options for SINGLE_CHOICE as a JSON list of strings; ignored for other types.",
    )
    is_required = models.BooleanField(default=False, help_text="When on, the registrant must answer.")
    is_active = models.BooleanField(
        default=True, help_text="Uncheck to retire a question without deleting historical answers."
    )
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending sort; lower shows first.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return self.prompt[:80]


class RegistrationAnswer(models.Model):
    """A registrant's answer to one RegistrationQuestion."""

    registration = models.ForeignKey(
        "Registration",
        on_delete=models.CASCADE,
        related_name="custom_answers",
        help_text="The registration the answer belongs to.",
    )
    question = models.ForeignKey(
        RegistrationQuestion,
        on_delete=models.PROTECT,
        related_name="answers",
        help_text="The question being answered.",
    )
    answer_text = models.TextField(blank=True, help_text="Free-form answer text.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["question__sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["registration", "question"], name="uq_registration_answer_question"),
        ]
        indexes = [
            models.Index(fields=["registration"]),
        ]

    def __str__(self) -> str:
        return f"Answer to #{self.question_id} on registration #{self.registration_id}"


class InstructorMessage(models.Model):
    """An email an instructor sent to a selected set of their class registrants.

    The body and recipient list are snapshotted at send time so the audit trail
    stays accurate even if registrations get cancelled or email addresses change
    afterwards.
    """

    instructor = models.ForeignKey(
        Instructor,
        on_delete=models.CASCADE,
        related_name="sent_messages",
        help_text="The instructor who composed and sent this message.",
    )
    class_offering = models.ForeignKey(
        ClassOffering,
        on_delete=models.CASCADE,
        related_name="instructor_messages",
        help_text="The class the recipients are registered for.",
    )
    subject = models.CharField(max_length=255, help_text="Email subject line.")
    body = models.TextField(help_text="Email body as composed (plain text).")
    recipient_count = models.PositiveIntegerField(help_text="Number of registrants BCC'd at send time.")
    bcc_self = models.BooleanField(default=True, help_text="Whether a copy was sent to the instructor's own email.")
    sent_at = models.DateTimeField(auto_now_add=True, help_text="When the message was sent.")

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self) -> str:
        return f"{self.subject} → {self.recipient_count} recipient(s)"


class InstructorMessageRecipient(models.Model):
    """Audit row: one registration that received an InstructorMessage at send time."""

    message = models.ForeignKey(
        InstructorMessage,
        on_delete=models.CASCADE,
        related_name="recipients",
        help_text="The message this row belongs to.",
    )
    registration = models.ForeignKey(
        "Registration",
        on_delete=models.PROTECT,
        related_name="received_instructor_messages",
        help_text="The registration the message was sent to.",
    )
    email = models.EmailField(help_text="The email address used at send time (snapshot).")

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["message", "registration"], name="uq_instructor_message_recipient"),
        ]

    def __str__(self) -> str:
        return f"{self.email} on message #{self.message_id}"


class ClassSettings(models.Model):
    liability_waiver_text = models.TextField(help_text="Full liability waiver text shown to all registrants.")
    model_release_waiver_text = models.TextField(
        help_text="Full model-release waiver text shown when a class requires it."
    )
    default_member_discount_pct = models.PositiveIntegerField(
        default=10, help_text="Percent discount auto-applied to registrations from verified Members (0 = no discount)."
    )
    reminder_hours_before = models.PositiveIntegerField(
        default=24, help_text="Hours before a class session to send the reminder email."
    )
    instructor_approval_required = models.BooleanField(
        default=True, help_text="When on, new classes go to admin for review before being published."
    )
    confirmation_email_footer = models.TextField(blank=True, help_text="Custom footer appended to confirmation emails.")

    class Meta:
        verbose_name = "Class Settings"
        verbose_name_plural = "Class Settings"

    def __str__(self) -> str:
        return "Class Settings"

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        if ClassSettings.objects.filter(pk=1).exists():
            kwargs.pop("force_insert", None)
            kwargs["force_update"] = True
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "ClassSettings":
        obj, _created = cls.objects.get_or_create(
            pk=1,
            defaults={
                "liability_waiver_text": DEFAULT_LIABILITY_TEXT,
                "model_release_waiver_text": DEFAULT_MODEL_RELEASE_TEXT,
            },
        )
        return obj
