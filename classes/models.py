"""Models for the Classes app."""

from __future__ import annotations

import secrets
from datetime import date as date_type, datetime
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.db.models import CheckConstraint, F, Q
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import strip_tags
from django.utils.timezone import localtime

from core.files import delete_orphan_on_replace
from core.images import normalize_field_if_uploaded
from core.models import HeroCropMixin
from core.validators import validate_image_size

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.core.files.uploadedfile import UploadedFile

    from membership.models import Member

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


MAX_GALLERY_IMAGES = 10


class Category(HeroCropMixin, models.Model):
    name = models.CharField(max_length=100, unique=True, help_text="Display name (e.g. Woodworking).")
    slug = models.SlugField(max_length=100, unique=True, help_text="URL slug.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending sort; lower shows first.")
    hero_image = models.ImageField(
        upload_to="classes/categories/",
        blank=True,
        validators=[validate_image_size],
        help_text="Optional header image.",
    )

    def get_hero_image_field_name(self) -> str:
        return "hero_image"

    icon_svg = models.TextField(
        blank=True,
        help_text=(
            "Inline SVG markup shown next to the guild name on public pages. "
            "Tint via currentColor. Defaults to a Lucide icon seeded for known guilds."
        ),
    )
    guild = models.ForeignKey(
        "membership.Guild",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="categories",
        help_text="Optional link to the membership Guild that owns this grouping. Used for Mailchimp tagging.",
    )

    @property
    def logo_prefix(self) -> str | None:
        """SVG logo prefix for this category's color logo in static/img/guild_logos/.

        Resolves from the linked guild's name when that maps to a logo, otherwise
        from the category's own name. Returns None when neither matches a logo file.
        """
        from membership.logos import logo_prefix_for

        if self.guild is not None:
            guild_prefix = self.guild.logo_prefix
            if guild_prefix:
                return guild_prefix
        return logo_prefix_for(self.name)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Guild"
        verbose_name_plural = "Guilds"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        from django.conf import settings

        delete_orphan_on_replace(self, "hero_image")
        normalize_field_if_uploaded(self, "hero_image", settings.IMAGE_MAX_LONG_EDGE_HERO)
        super().save(*args, **kwargs)


class ClassOfferingQuerySet(models.QuerySet["ClassOffering"]):
    def public(self) -> "ClassOfferingQuerySet":
        """Published classes visible in the public portal (excludes private)."""
        return self.filter(status="published", is_private=False)

    def bookable(self) -> "ClassOfferingQuerySet":
        """Public classes still open for sign-up, soonest first.

        Published and non-private, and either flexibly scheduled or with a
        *first* session still in the future. A dated class — single or series —
        drops out the instant its first session begins: you can't join a series
        part-way through, so a started series is never bookable. Flexible /
        undated classes sort last. (Seat availability is separate — see
        ``spots_remaining``.)
        """
        from django.db.models import Min

        now = timezone.now()
        return (
            self.public()
            .annotate(first_session_at=Min("sessions__starts_at"))
            .filter(Q(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE) | Q(first_session_at__gte=now))
            .order_by(F("first_session_at").asc(nulls_last=True), "title")
            .distinct()
        )

    def pending_review(self) -> "ClassOfferingQuerySet":
        return self.filter(status="pending")

    def awaiting_guild_lead(self, member: "Member") -> "ClassOfferingQuerySet":
        """Pending classes whose undecided guild-lead gate this member can act on.

        A class qualifies when it is PENDING, its category's guild is one this member
        leads or holds a staff role on, and it has an undecided ``GUILD_LEAD`` approval
        row.
        """
        return self.filter(
            status="pending",
            category__guild__in=member.staffed_guilds,
            approvals__role="guild_lead",
            approvals__decision="",
        ).distinct()

    def for_instructor(self, instructor: "Member") -> "ClassOfferingQuerySet":
        return self.filter(instructor=instructor)

    def editable_by(self, member: "Member") -> "ClassOfferingQuerySet":
        """Offerings this member may edit.

        Admins and guild officers may edit any class. Everyone else may edit the
        classes they instruct plus any class whose category belongs to a guild they
        lead or hold a staff role on.
        """
        if member.is_fog_admin or member.is_guild_officer:
            return self
        return self.filter(
            Q(instructor=member)
            | Q(category__guild__guild_lead=member)
            | Q(category__guild__staff_memberships__member=member)
        ).distinct()

    def spots_remaining_map(self) -> dict[int, int]:
        """Map of ``{offering_pk: spots_remaining}`` for this queryset in one query.

        Mirrors the ``ClassOffering.spots_remaining`` property but batched, so the
        catalog can show per-date seat counts without an N+1 of count queries.
        """
        from django.db.models import Count, Q

        rows = self.annotate(
            used=Count(
                "registrations",
                filter=Q(registrations__status__in=[Registration.Status.CONFIRMED, Registration.Status.PENDING]),
            )
        ).values("pk", "capacity", "used")
        return {row["pk"]: max(0, row["capacity"] - row["used"]) for row in rows}


class ClassOffering(HeroCropMixin, models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending Review"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    class SchedulingModel(models.TextChoices):
        FIXED = "fixed", "Fixed sessions"
        FLEXIBLE = "flexible", "Flexible (arrange with instructor)"

    class SchedulingType(models.TextChoices):
        SINGLE_SESSION = "single_session", "Single Session"
        SERIES_PACKAGE = "series_package", "Series Package"

    title = models.CharField(max_length=255, help_text="Public class title.")
    slug = models.SlugField(max_length=255, unique=True, help_text="URL slug.")
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="classes", help_text="Guild grouping."
    )
    instructor = models.ForeignKey(
        "membership.Member",
        on_delete=models.PROTECT,
        related_name="classes",
        null=True,
        blank=True,
        help_text="Member who teaches this class.",
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
    scheduling_type = models.CharField(
        max_length=20,
        choices=SchedulingType.choices,
        default=SchedulingType.SINGLE_SESSION,
        help_text=(
            "Single Session: one date, one seat, one payment. "
            "Series Package: one purchase enrolls the registrant in every "
            "scheduled date of this class under a single payment."
        ),
    )
    is_private = models.BooleanField(default=False, help_text="Hidden from public portal; private registration only.")
    private_for_name = models.CharField(max_length=255, blank=True, help_text="Name shown when private.")
    recurring_pattern = models.CharField(max_length=255, blank=True, help_text="Free-text recurrence description.")
    image = models.ImageField(
        upload_to="classes/images/",
        blank=True,
        validators=[validate_image_size],
        help_text="Hero image.",
    )

    def get_hero_image_field_name(self) -> str:
        return "image"

    video_url = models.URLField(
        blank=True,
        max_length=500,
        help_text="Optional YouTube link (watch, youtu.be, embed, or shorts URL). Embeds on the public class page.",
    )
    requires_model_release = models.BooleanField(
        default=False, help_text="When on, registrants also sign model release."
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, help_text="Lifecycle status."
    )
    created_by = models.ForeignKey(
        "membership.Member",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Member who authored this class.",
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
    legacy_cms_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="Drupal node UUID from classes.pastlives.space. Empty on manually-created offerings.",
    )
    legacy_image_url = models.URLField(
        blank=True,
        help_text="Hero image URL from the legacy CMS. Cleared after download_legacy_images runs.",
    )
    grouping_key = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        help_text=(
            "Links the same class offered on multiple dates into one public catalog card. "
            "Derived from the normalized title + category on save; empty offerings stand alone."
        ),
    )
    welcome_email_enabled = models.BooleanField(
        default=False,
        help_text="When on, every new registrant also receives the instructor's welcome email.",
    )
    welcome_email_subject = models.CharField(
        max_length=200, blank=True, help_text="Subject line for the instructor's welcome email."
    )
    welcome_email_body = models.TextField(
        blank=True,
        help_text="The welcome message sent to each new registrant. Plain text; line breaks are preserved.",
    )
    welcome_email_updated_at = models.DateTimeField(
        null=True, blank=True, help_text="When the welcome email content was last edited."
    )

    objects = ClassOfferingQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["legacy_cms_id"],
                condition=models.Q(legacy_cms_id__gt=""),
                name="uq_classoffering_legacy_cms_id",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def welcome_email_ready(self) -> bool:
        """True when the instructor welcome email is enabled and has subject + body.

        The send path checks this so an enabled-but-empty welcome email never goes out.
        """
        return bool(
            self.welcome_email_enabled and self.welcome_email_subject.strip() and self.welcome_email_body.strip()
        )

    def save(self, *args, **kwargs) -> None:
        from django.conf import settings

        delete_orphan_on_replace(self, "image")
        creating = self._state.adding
        old = None
        # If the hero image is changing, also clear the stale crop box.
        if self.pk:
            try:
                old = type(self)._default_manager.only("image", "grouping_key", "category_id").get(pk=self.pk)
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

        # Keep the catalog grouping key in sync with the title/category so every
        # run of the same class — single one-offs AND multi-session series alike —
        # collapses into one public card. The card lists each run as its own
        # bookable option (a single date, or a full multi-session date-set), each
        # keeping its own seats. Series no longer opt out of grouping: two runs of
        # the same series (e.g. Blacksmithing 101 offered Jun 5–19 and again
        # Jul 11–25) should read as "one class, two date-set options" — one card,
        # not two.
        from classes.grouping import grouping_key_for

        self.grouping_key = grouping_key_for(self.title, self.category_id)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and {"title", "category", "category_id"} & set(update_fields):
            kwargs["update_fields"] = [*update_fields, "grouping_key"]

        super().save(*args, **kwargs)

        # When a grouped class moves to a new category, sync siblings so the
        # group stays coherent (same grouping_key across all dates).
        if old is not None and old.category_id != self.category_id and old.grouping_key:
            type(self)._default_manager.filter(
                grouping_key=old.grouping_key,
            ).exclude(pk=self.pk).update(
                category_id=self.category_id,
                grouping_key=self.grouping_key,
            )

        if creating:
            from classes import activity

            activity.log(CmsActivity.Kind.CLASS_CREATED, class_offering=self)

    @property
    def required_review_roles(self) -> list[str]:
        """Reviewer roles whose approval is needed to publish this offering.

        Admin is always required. Guild Lead is added when the class's category
        is linked to a guild that has a lead set — otherwise we'd be blocking
        on a role that has no human attached.
        """
        roles: list[str] = [ClassApproval.Role.ADMIN]
        if (
            self.category_id
            and self.category.guild_id
            and self.category.guild is not None
            and self.category.guild.guild_lead_id
        ):
            roles.append(ClassApproval.Role.GUILD_LEAD)
        return roles

    def submit_for_review(self) -> list["ClassApproval"]:
        """Move from DRAFT to PENDING and open only the first-stage review gate.

        Approval is sequential: the first stage is the Guild Lead when the
        class's category links a guild with a lead, otherwise the Admin. The
        Admin gate is only created later, once the Guild Lead approves (see
        ``on_review_decision_recorded``). This keeps an admin from publishing
        before the Guild Lead has weighed in.

        Notifies the first-stage reviewer (in-app + email) directly from the
        model so every submit path — quick-submit, create, and edit — fans out
        the same way. Returns the freshly created approval row(s) so callers
        can introspect the result.
        """
        if self.status != self.Status.DRAFT:
            raise ValueError(f"Only draft classes can be submitted; got {self.status}.")
        self.status = self.Status.PENDING
        self.save(update_fields=["status", "updated_at"])
        # Clear out any stale approval rows from a prior submission cycle, then
        # open only the first-stage gate for this fresh round.
        self.approvals.all().delete()
        row = self._create_first_stage_approval()
        from classes import activity

        activity.log(
            CmsActivity.Kind.CLASS_SUBMITTED,
            class_offering=self,
            payload={"required_roles": list(self.required_review_roles), "first_stage": row.role},
        )
        self._notify_first_stage_reviewer(row)
        return [row]

    def _create_first_stage_approval(self) -> "ClassApproval":
        """Create the single approval row that opens stage one of review.

        Guild Lead when the category's guild has a lead; Admin otherwise.
        """
        roles = self.required_review_roles
        first_role = (
            ClassApproval.Role.GUILD_LEAD if ClassApproval.Role.GUILD_LEAD in roles else ClassApproval.Role.ADMIN
        )
        return ClassApproval.objects.create(class_offering=self, role=first_role)

    def _notify_first_stage_reviewer(self, row: "ClassApproval") -> None:
        """Fan out the stage-one notification + email for a freshly opened gate.

        The guild-lead branch routes through :func:`classes.emails.send_guild_lead_review_request`
        and the admin branch through :func:`classes.emails.send_admin_review_request`. Each now
        fires a single ``class_review_requested`` event that owns BOTH the dedicated
        ``review_request`` email and (for the guild-lead branch) the in-app row to the guild's
        leadership — so opted-in leadership receive exactly one email and one bell row, never the
        old dispatch + dedicated-send pair. The admin branch stays email-only, as today.
        """
        from classes import emails

        if row.role == ClassApproval.Role.GUILD_LEAD:
            emails.send_guild_lead_review_request(self, row)
        else:
            emails.send_admin_review_request(self, row)

    def approve(self, admin_user) -> "ClassApproval":
        """Record an admin approval via the ClassApproval pathway.

        Maintained for callers (views, tests) that already used this name.
        Creates a fresh ADMIN approval row if one doesn't exist for the
        current cycle and decides it APPROVED on the admin's behalf. Returns
        the decided row so callers can email the instructor the outcome.
        """
        if self.status != self.Status.PENDING:
            raise ValueError(f"Only pending classes can be approved; got {self.status}.")
        row = self.approvals.filter(role=ClassApproval.Role.ADMIN, decision="").first() or ClassApproval.objects.create(
            class_offering=self, role=ClassApproval.Role.ADMIN
        )
        # Pin the offering instance so the lifecycle hook (which may publish)
        # mutates *this* object's status. The filter path would otherwise load
        # a separate ClassOffering instance, leaving self.status stale.
        row.class_offering = self
        row.decide(ClassApproval.Decision.APPROVED, user=admin_user)
        return row

    def archive(self) -> None:
        self.status = self.Status.ARCHIVED
        self.save(update_fields=["status", "updated_at"])
        from classes import activity
        from core import notifications

        activity.log(CmsActivity.Kind.CLASS_ARCHIVED, class_offering=self)
        notifications.dispatch(
            "class_cancelled",
            notifications.active_member_users(),
            title="A class was cancelled",
            body=self.title,
            url="/classes/",
        )

    def promote_next_from_waitlist(self) -> "Registration | None":
        """Notify the next waitlisted person when a confirmed spot opens.

        Called after a confirmed registration cancels or refunds. Picks the
        oldest WAITLISTED row that hasn't been notified yet, stamps
        ``waitlist_notified_at``, and emails them a claim link. Does
        nothing when no spots have actually opened (e.g. capacity bumps
        elsewhere) or no eligible waitlist row exists.

        Returns the notified registration so callers can introspect for
        logging or tests.
        """
        if self.spots_remaining <= 0:
            return None
        next_up = (
            self.registrations.filter(
                status=Registration.Status.WAITLISTED,
                waitlist_notified_at__isnull=True,
            )
            .order_by("registered_at")
            .first()
        )
        if next_up is None:
            return None
        next_up.waitlist_notified_at = timezone.now()
        next_up.save(update_fields=["waitlist_notified_at"])
        # Lazy imports to avoid a circular dependency between models.py and the
        # emails / activity helpers.
        from classes import activity
        from classes.emails import send_waitlist_spot_opened

        # ``send_waitlist_spot_opened`` emits the single ``waitlist_spot_available`` event
        # that fans out BOTH the claim email (to the registrant) and the in-app row (to the
        # promoted member) — replacing the old dedicated send + the in-app ``dispatch`` that
        # used to follow it here.
        send_waitlist_spot_opened(next_up)
        activity.log(
            CmsActivity.Kind.WAITLIST_NOTIFIED,
            class_offering=self,
            registration=next_up,
        )
        return next_up

    def on_review_decision_recorded(self, row: "ClassApproval") -> None:
        """Lifecycle hook: called by ClassApproval.decide.

        APPROVED: if every required role has now signed off, publish.
        CHANGES_REQUESTED / DENIED: bounce back to DRAFT so the instructor
        can edit and resubmit. Per the locked decision in PLAN.md §14,
        a guild-lead denial is recoverable (returns to DRAFT) rather than
        archival; admin-level archival is a separate explicit action.
        """
        from classes import activity

        from core import notifications

        if row.decision == ClassApproval.Decision.APPROVED:
            activity.log(
                CmsActivity.Kind.CLASS_APPROVED,
                class_offering=self,
                actor=row.decided_by,
                payload={"role": row.role},
            )
            # Stage-1 → Stage-2 escalation: a Guild Lead's approval opens the
            # Admin gate (if admin review is still required and not yet open)
            # and notifies staff for executive validation. We do not publish on
            # this branch — publication waits for the admin to sign off.
            if (
                row.role == ClassApproval.Role.GUILD_LEAD
                and ClassApproval.Role.ADMIN in self.required_review_roles
                and not self.approvals.filter(role=ClassApproval.Role.ADMIN).exists()
            ):
                admin_row = ClassApproval.objects.create(class_offering=self, role=ClassApproval.Role.ADMIN)
                self._escalate_to_admin(admin_row, guild_lead=row.decided_by)
            required = set(self.required_review_roles)
            approved = {r.role for r in self.approvals.filter(decision=ClassApproval.Decision.APPROVED)}
            if required.issubset(approved):
                self.status = self.Status.PUBLISHED
                self.approved_by = row.decided_by
                self.published_at = timezone.now()
                self.save(update_fields=["status", "approved_by", "published_at", "updated_at"])
                activity.log(
                    CmsActivity.Kind.CLASS_PUBLISHED,
                    class_offering=self,
                    actor=row.decided_by,
                )
                # The instructor's "Your class was approved" bell row + the rich "live!"
                # email now both fan out from the single ``instructor_class_approved`` event
                # emitted by ``classes.emails.send_class_review_decision`` (called by the
                # view right after the publishing decision). Dispatching it here too would
                # double the bell row, so the decision event owns it.
                notifications.dispatch(
                    "class_published",
                    notifications.active_member_users(),
                    title="New class published",
                    body=self.title,
                    url=f"/classes/{self.slug}/",
                )
        elif row.decision == ClassApproval.Decision.CHANGES_REQUESTED:
            self.status = self.Status.DRAFT
            self.save(update_fields=["status", "updated_at"])
            activity.log(
                CmsActivity.Kind.CLASS_CHANGES_REQUESTED,
                class_offering=self,
                actor=row.decided_by,
                payload={"role": row.role, "notes_excerpt": (row.notes or "")[:200]},
            )
            # The instructor's "Changes requested" bell row + the rich changes email now
            # both fan out from the single ``instructor_changes_requested`` event emitted by
            # ``classes.emails.send_class_review_decision`` (called by the view right after
            # the decision). Dispatching it here too would double the bell row.
        elif row.decision == ClassApproval.Decision.DENIED:
            self.status = self.Status.DRAFT
            self.save(update_fields=["status", "updated_at"])
            activity.log(
                CmsActivity.Kind.CLASS_DENIED,
                class_offering=self,
                actor=row.decided_by,
                payload={"role": row.role, "notes_excerpt": (row.notes or "")[:200]},
            )

    def _escalate_to_admin(self, admin_row: "ClassApproval", *, guild_lead: "User | None") -> None:
        """Fire the stage-two admin escalation after a Guild Lead approves.

        Emits the executive-validation request as one ``class_validation_requested``
        event (admin email + FOG_ADMINS in-app) via
        :func:`classes.emails.send_admin_validation_request`. The Guild Lead is named
        in the copy so admins know who already vouched for the class.
        """
        from classes import emails

        emails.send_admin_validation_request(self, admin_row)

    def add_gallery_images(self, files: list[UploadedFile]) -> None:
        """Create ClassImage rows from uploaded files, appended after existing ones.

        Raises:
            ValidationError: If adding ``files`` would push the offering over
                ``MAX_GALLERY_IMAGES``. The batch is rejected whole — no rows are
                created — so the caller can surface one clear message.
        """
        from django.core.exceptions import ValidationError

        current = self.gallery_images.count()
        if current + len(files) > MAX_GALLERY_IMAGES:
            raise ValidationError(f"A class can have at most {MAX_GALLERY_IMAGES} images.")
        for offset, img_file in enumerate(files):
            ClassImage.objects.create(class_offering=self, image=img_file, sort_order=current + offset)

    @property
    def spots_remaining(self) -> int:
        """Capacity minus current confirmed + pending registrations."""
        used = self.registrations.filter(
            status__in=[Registration.Status.CONFIRMED, Registration.Status.PENDING]
        ).count()
        return max(0, self.capacity - used)

    @property
    def is_series(self) -> bool:
        """True when one purchase covers every scheduled session of this offering."""
        return self.scheduling_type == self.SchedulingType.SERIES_PACKAGE

    @property
    def is_single(self) -> bool:
        """True when each ticket is for a single date (the default behaviour)."""
        return self.scheduling_type == self.SchedulingType.SINGLE_SESSION

    @property
    def series_session_count(self) -> int:
        """Number of sessions a series ticket covers (0 for none scheduled yet)."""
        return self.sessions.count()

    @property
    def has_started(self) -> bool:
        """True once the first scheduled session has begun (dated classes only)."""
        earliest = self.earliest_session_at
        return earliest is not None and earliest < timezone.now()

    @property
    def is_bookable(self) -> bool:
        """Whether sign-ups are still open on timing grounds.

        Flexible classes are always bookable. A dated class — single or series —
        is bookable only until its first session starts; you can't join after it
        has begun. Seat availability is handled separately via
        ``spots_remaining`` (a sold-out future class is still "bookable" here and
        routes to the waitlist).
        """
        if self.scheduling_model == self.SchedulingModel.FLEXIBLE:
            return True
        earliest = self.earliest_session_at
        return earliest is not None and earliest >= timezone.now()

    @property
    def member_price_cents(self) -> int | None:
        """Discounted price in cents for verified members.

        Returns ``None`` when this offering has no member discount, so callers
        can treat ``None`` as "no separate member price to show".
        """
        if not self.member_discount_pct:
            return None
        return int(self.price_cents * (100 - self.member_discount_pct) / 100)

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
    def first_upcoming_session_at(self) -> datetime | None:
        session = self.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at").first()
        return session.starts_at if session else None

    @property
    def earliest_session_at(self) -> datetime | None:
        """First session ever — past or future. Used as fallback when no upcoming session exists."""
        session = self.sessions.order_by("starts_at").first()
        return session.starts_at if session else None

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        """Trim ``text`` to at most ``limit`` chars on a word boundary with an ellipsis.

        Never cuts a word in half: when truncation is needed we keep whole words
        and append a single ``…`` (U+2026). Only when the first word alone is
        longer than the window do we hard-cut mid-word.

        Args:
            text: The source string (already plain text, whitespace-collapsed).
            limit: The maximum allowed length of the returned string.

        Returns:
            ``text`` unchanged when it already fits, otherwise a word-bounded
            prefix ending in ``…`` whose length is ``<= limit``.
        """
        if len(text) <= limit:
            return text
        window = text[: limit - 1]
        cut = window.rsplit(" ", 1)[0] if " " in window else window
        return f"{cut.rstrip(' -–—,;:.')}…"

    def _seo_date_label(self) -> str:
        """Localized 'Mon D, YYYY' label for the offering's defining session.

        Prefers the next upcoming session so live pages read as a future event;
        falls back to the earliest session so expired/archived offerings still
        carry a distinguishing, indexable date. Returns ``""`` when the offering
        has no sessions at all.
        """
        dt = self.first_upcoming_session_at or self.earliest_session_at
        if dt is None:
            return ""
        return date_format(localtime(dt), "M j, Y")

    @property
    def seo_title(self) -> str:
        """Unique, ≤60-char class-identifying title for the page ``<title>``.

        Starts from the date-stripped base title and greedily appends the
        session date then the instructor, dropping segments from the right when
        over the 60-char budget. The date is added before the instructor because
        it is the stronger uniqueness signal for sibling offerings of the same
        class on different dates. The brand suffix is intentionally omitted to
        stay within Google's title width.

        Returns:
            A plain-text title of length 1–60 (HTML auto-escaped at render time).
        """
        from classes.templatetags.classes_tags import strip_date_suffix

        base = strip_date_suffix(self.title).strip()
        result = base
        date_label = self._seo_date_label()
        if date_label:
            candidate = f"{result} — {date_label}"
            if len(candidate) <= 60:
                result = candidate
        if self.instructor:
            name = (self.instructor.display_name or "").strip()
            first_name = name.split(" ")[0]
            # Skip the instructor segment when the title already names them, so
            # "Blacksmithing 101 with Glen" + instructor "Glen Morris" doesn't
            # read "... with Glen — <date> with Glen Morris".
            mentions_instructor = name.lower() in result.lower() or f"with {first_name}".lower() in result.lower()
            candidate = f"{result} with {name}"
            if name and not mentions_instructor and len(candidate) <= 60:
                result = candidate
        return self._truncate(result, 60)

    @property
    def seo_description(self) -> str:
        """≤160-char plain-text meta description for the page ``<head>``.

        Uses the offering's own description with HTML stripped and whitespace
        collapsed; when blank, falls back to a category-aware sentence so every
        page still emits a meaningful, distinct description. Trimmed on a word
        boundary so it never ends mid-word.

        Returns:
            A plain-text description of length 1–160 (HTML auto-escaped at
            render time).
        """
        from classes.templatetags.classes_tags import strip_date_suffix

        raw = " ".join(strip_tags(self.description or "").split())
        if not raw:
            base = strip_date_suffix(self.title).strip()
            raw = f"{base} at Past Lives Makerspace in Portland, OR. {self.category.name} class — register online."
        return self._truncate(raw, 160)

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

    def duplicate_as_new_run(self) -> "ClassOffering":
        """Clone as a fresh draft "run" of the SAME class on a new set of dates.

        Unlike :meth:`duplicate`, the title is kept verbatim so the new run shares
        this class's ``grouping_key`` and collapses into the same public catalog
        card — it becomes another date-set option rather than a separate class.
        The clone starts with no sessions (a new pk has no related rows yet) so
        the instructor/admin fills in fresh dates, and as a DRAFT so it isn't
        public until reviewed/published. ``legacy_cms_id`` is cleared: a
        hand-added run is locally authored, not a synced legacy node, and the
        partial unique constraint would otherwise reject the duplicate.
        """
        base_slug = f"{self.slug}-run"
        slug = base_slug
        n = 1
        while ClassOffering.objects.filter(slug=slug).exists():
            n += 1
            slug = f"{base_slug}-{n}"
        self.pk = None
        self.slug = slug
        self.status = self.Status.DRAFT
        self.published_at = None
        self.approved_by = None
        self.legacy_cms_id = ""
        self.save()
        return self


class ClassApprovalQuerySet(models.QuerySet["ClassApproval"]):
    def pending(self) -> "ClassApprovalQuerySet":
        return self.filter(decision="")

    def for_offering(self, offering: "ClassOffering") -> "ClassApprovalQuerySet":
        return self.filter(class_offering=offering)


class ClassApproval(models.Model):
    """One reviewer gate on a ClassOffering submission.

    When an instructor calls ``ClassOffering.submit_for_review()``, one row
    is created per required reviewer role (``Role.ADMIN`` always; plus
    ``Role.GUILD_LEAD`` when the class's category is linked to a guild that
    has a lead). Each row gets a unique ``token`` so the emailed reviewer
    can act without a hub login.

    Rows start with ``decision = ""`` (still pending). Calling ``decide()``
    on a row records the decision and triggers the lifecycle hook on the
    offering (publish when every required row is APPROVED; back to DRAFT
    when any reviewer requests changes or denies).
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        GUILD_LEAD = "guild_lead", "Guild Lead"

    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        CHANGES_REQUESTED = "changes_requested", "Changes Requested"
        DENIED = "denied", "Denied"

    class_offering = models.ForeignKey(
        "ClassOffering",
        on_delete=models.CASCADE,
        related_name="approvals",
        help_text="The class submission this review row gates.",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        help_text="Which reviewer gate this row represents.",
    )
    decision = models.CharField(
        max_length=20,
        choices=Decision.choices,
        blank=True,
        default="",
        help_text="Reviewer's verdict; empty means still pending.",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Authenticated user who recorded the decision, when known.",
    )
    notes = models.TextField(
        blank=True,
        help_text="Reviewer comments shown to the instructor.",
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Random token used in the emailed /classes/review/<token>/ link.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the review was requested.")
    decided_at = models.DateTimeField(null=True, blank=True, help_text="When the reviewer acted.")

    objects = ClassApprovalQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["class_offering", "role"]),
        ]

    def __str__(self) -> str:
        state = self.get_decision_display() if self.decision else "Pending"
        return f"{self.get_role_display()} review of {self.class_offering_id}: {state}"

    def save(self, *args, **kwargs) -> None:
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def decide(self, decision: str, user=None, notes: str = "") -> None:
        """Record a reviewer decision and trigger the offering's lifecycle hook."""
        if decision not in {
            self.Decision.APPROVED,
            self.Decision.CHANGES_REQUESTED,
            self.Decision.DENIED,
        }:
            raise ValueError(f"Unknown decision: {decision!r}")
        self.decision = decision
        self.decided_by = user
        self.notes = notes
        self.decided_at = timezone.now()
        self.save(update_fields=["decision", "decided_by", "notes", "decided_at"])
        self.class_offering.on_review_decision_recorded(self)


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

    def clean(self) -> None:
        from django.core.exceptions import ValidationError

        super().clean()
        existing = ClassImage.objects.filter(class_offering=self.class_offering)
        if self.pk:
            existing = existing.exclude(pk=self.pk)
        if existing.count() >= MAX_GALLERY_IMAGES:
            raise ValidationError(f"A class can have at most {MAX_GALLERY_IMAGES} images.")

    def save(self, *args, **kwargs) -> None:
        from django.conf import settings

        delete_orphan_on_replace(self, "image")
        normalize_field_if_uploaded(self, "image", settings.IMAGE_MAX_LONG_EDGE_GALLERY)
        super().save(*args, **kwargs)


class ClassSessionQuerySet(models.QuerySet["ClassSession"]):
    def upcoming_public(self) -> "ClassSessionQuerySet":
        """Future sessions whose offering is publicly visible (published + non-private).

        Mirrors ``ClassOfferingQuerySet.public()`` across the FK (``status="published"``,
        ``is_private=False``) rather than ``bookable()``: a part-started series is no
        longer *bookable* as a whole, but its still-future sessions remain real,
        dated, purchasable inventory and should be counted.
        """
        return self.filter(
            starts_at__gte=timezone.now(),
            class_offering__status="published",
            class_offering__is_private=False,
        )

    def upcoming_public_count(self) -> int:
        """How many purchasable, dated sessions are live in the public catalog."""
        return self.upcoming_public().count()


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

    objects = ClassSessionQuerySet.as_manager()

    class Meta:
        ordering = ["starts_at"]
        constraints = [CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="session_ends_after_starts")]

    def __str__(self) -> str:
        return f"{self.class_offering.title} — {self.starts_at:%Y-%m-%d}"


class DiscountCodeQuerySet(models.QuerySet["DiscountCode"]):
    def best_auto_apply_for(self, offering: "ClassOffering", base_price_cents: int) -> "DiscountCode | None":
        """The class-scoped auto-apply code that drops ``base_price_cents`` furthest.

        Walks this offering's currently-valid, approved, active auto-apply codes
        and returns the one yielding the lowest final price, or ``None`` when no
        such code qualifies.
        """
        best: DiscountCode | None = None
        best_price: int | None = None
        for code in self.filter(
            class_offering=offering,
            is_active=True,
            is_approved=True,
            auto_apply=True,
        ):
            if not code.is_currently_valid():
                continue
            final = code.apply_to(base_price_cents)
            if best_price is None or final < best_price:
                best = code
                best_price = final
        return best


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
    class_offering = models.ForeignKey(
        "ClassOffering",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="discount_codes",
        help_text=(
            "When set, this code only applies to that one class. "
            "When null, the code is global and any class can use it."
        ),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="User who created this code (audit + lets instructors manage their own codes).",
    )
    auto_apply = models.BooleanField(
        default=False,
        help_text=(
            "When on, the code is automatically applied for any eligible registrant. "
            "Useful for class-scoped promotional pricing without making customers type a code."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = DiscountCodeQuerySet.as_manager()

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
        creating = self._state.adding
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)
        if creating:
            from classes import activity

            activity.log(
                CmsActivity.Kind.DISCOUNT_CODE_CREATED,
                class_offering=self.class_offering,
                actor=self.created_by,
                payload={"code": self.code, "auto_apply": self.auto_apply},
            )

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
    # Transient (non-persisted) attribute a view sets before a status-changing
    # save() to attribute a confirm/refund action in the audit feed. Unset on a
    # fresh instance — read via getattr(..., None).
    _acting_user: "User | None"

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
    waitlist_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Stamped when this waitlisted registrant has been emailed that a "
            "spot opened up. Used to avoid double-notifying and to expire the "
            "claim window."
        ),
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
    create_account = models.BooleanField(
        default=False,
        help_text=(
            "Did an anonymous registrant opt into having a passwordless Past Lives account created once their "
            "booking is confirmed? Always False for already-logged-in registrants."
        ),
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
        prior_status = None
        if not creating:
            prior_status = type(self)._default_manager.only("status").get(pk=self.pk).status
        if creating and not self.self_serve_token:
            self.self_serve_token = secrets.token_urlsafe(48)
        if creating and not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)
        if creating and self.member_id is None:
            self.link_member_by_email()
        self._dispatch_status_notification(creating, prior_status)

    def _dispatch_status_notification(self, creating: bool, prior_status: str | None) -> None:
        """Dispatch in-app notifications triggered by registration status transitions."""
        from classes import activity
        from core import notifications

        user = self.member.user if (self.member is not None and self.member.user is not None) else None
        # ``_acting_user`` is a transient (non-persisted) attribute a view sets
        # before a status-changing save() to attribute confirm/refund actions in
        # the audit feed. Unset (e.g. the Stripe webhook) → None → "System".
        acting = getattr(self, "_acting_user", None)
        if creating:
            if self.status == self.Status.WAITLISTED:
                activity.log(
                    CmsActivity.Kind.WAITLIST_JOINED, class_offering=self.class_offering, registration=self, actor=user
                )
                # The "Added to the waitlist" in-app row + the dedicated waitlist email now
                # both fan out from the single ``waitlist_confirmed`` event emitted by
                # ``classes.emails.send_waitlist_joined_confirmation`` (called by the register
                # view right after this save). Dispatching it here too would double the bell row.
            else:
                activity.log(
                    CmsActivity.Kind.REGISTRATION_CREATED,
                    class_offering=self.class_offering,
                    registration=self,
                    actor=user,
                )
        elif prior_status is not None and prior_status != self.status:
            if self.status == self.Status.CONFIRMED:
                activity.log(
                    CmsActivity.Kind.REGISTRATION_CONFIRMED,
                    class_offering=self.class_offering,
                    registration=self,
                    actor=acting,
                )
                # The in-app "Registration confirmed" row + the confirmation email now
                # both fan out from a single ``registration_confirmed`` event emitted by
                # ``classes.emails.send_registration_confirmation`` (called right after
                # every CONFIRMED transition — free-class view + paid webhook). Dispatching
                # the in-app row here too would double the bell row, so the event owns it.
            elif self.status == self.Status.REFUNDED:
                activity.log(
                    CmsActivity.Kind.REGISTRATION_REFUNDED,
                    class_offering=self.class_offering,
                    registration=self,
                    actor=acting,
                )
                if user is not None:
                    notifications.dispatch(
                        "refund_issued",
                        [user],
                        title="Refund issued",
                        body=self.class_offering.title,
                        url="/classes/account/",
                    )

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

    def cancel(self, reason: str = "", actor: "User | None" = None) -> None:
        """Cancel this registration and record who did it.

        ``actor`` is the authenticated user who triggered the cancellation —
        an admin from the admin tab, or the registrant from the self-serve
        page. It is threaded into the activity log so the audit feed shows a
        name rather than "System". ``None`` when no human acted (e.g. an
        automated path).
        """
        previously_held_a_spot = self.status in (self.Status.CONFIRMED, self.Status.PENDING)
        was_waitlisted = self.status == self.Status.WAITLISTED
        self.status = self.Status.CANCELLED
        self.cancelled_at = timezone.now()
        self.cancellation_reason = reason
        self.save(update_fields=["status", "cancelled_at", "cancellation_reason"])
        from classes import activity

        activity.log(
            CmsActivity.Kind.WAITLIST_LEFT if was_waitlisted else CmsActivity.Kind.REGISTRATION_CANCELLED,
            class_offering=self.class_offering,
            registration=self,
            actor=actor,
            payload={"reason": reason} if reason else {},
        )
        if previously_held_a_spot:
            self.class_offering.promote_next_from_waitlist()

    def mark_refunded(self, reason: str = "", actor: "User | None" = None) -> None:
        """Record this registration as refunded — record-only, issues no Stripe refund.

        The actual money refund is issued by hand in the Stripe dashboard; this
        records the decision, frees the spot, and promotes the waitlist. The
        REFUNDED status transition in ``save()`` logs the audit entry and notifies
        the registrant; ``actor`` is threaded through ``_acting_user`` so the feed
        attributes the admin rather than "System".
        """
        previously_held_a_spot = self.status in (self.Status.CONFIRMED, self.Status.PENDING)
        self._acting_user = actor
        self.status = self.Status.REFUNDED
        self.cancellation_reason = reason
        self.save(update_fields=["status", "cancellation_reason"])
        if previously_held_a_spot:
            self.class_offering.promote_next_from_waitlist()

    def move_to(self, target: "ClassOffering", actor: "User | None" = None) -> None:
        """Reassign this registration to a different class, keeping payment as-is.

        No price reconciliation: ``amount_paid_cents`` is unchanged. The source
        class's waitlist is promoted if this registration was holding a spot
        there. Raises ``ValueError`` if ``target`` is the current class.
        """
        if target.pk == self.class_offering_id:
            raise ValueError("Cannot move a registration to its current class.")
        source = self.class_offering
        held_spot = self.status in (self.Status.CONFIRMED, self.Status.PENDING)
        self.class_offering = target
        self.save(update_fields=["class_offering"])
        from classes import activity

        activity.log(
            CmsActivity.Kind.REGISTRATION_MOVED,
            class_offering=target,
            registration=self,
            actor=actor,
            payload={"from": source.title, "to": target.title},
        )
        if held_spot:
            source.promote_next_from_waitlist()

    @property
    def waitlist_position(self) -> int | None:
        """Rank among WAITLISTED rows for the same class, lowest = first in line.

        Returns ``None`` for non-waitlisted registrations.
        """
        if self.status != self.Status.WAITLISTED:
            return None
        ahead = Registration.objects.filter(
            class_offering=self.class_offering,
            status=self.Status.WAITLISTED,
            registered_at__lt=self.registered_at,
        ).count()
        return ahead + 1


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
    mailchimp_tag = models.CharField(
        max_length=80,
        blank=True,
        default="",
        help_text=(
            "Optional Mailchimp tag prefix for this question's answers. Leave blank to auto-derive a tag "
            "from the prompt. Only Yes/No and Single Choice answers are pushed to Mailchimp as tags."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return self.prompt[:80]

    @property
    def is_taggable(self) -> bool:
        """Whether this question's answers should be pushed to Mailchimp as tags.

        Only Yes/No and Single Choice answers segment cleanly. Free-text
        (short/long) answers make poor tags, so they're recorded on the
        registration but never sent to Mailchimp.
        """
        return self.question_type in (self.QuestionType.YES_NO, self.QuestionType.SINGLE_CHOICE)

    def tag_for(self, answer_text: str) -> str | None:
        """Build the Mailchimp tag for this question's given answer, or None.

        Returns None for non-taggable question types, blank answers, and a
        Yes/No "no" (we only tag the affirmative so segments key on opt-in).
        The tag prefix is the admin-set ``mailchimp_tag`` when present, else a
        slug of the prompt. Single-choice answers append the answer slug; a
        Yes/No "yes" uses the prefix alone.
        """
        from django.utils.text import slugify

        if not self.is_taggable:
            return None
        value = (answer_text or "").strip()
        if not value:
            return None
        prefix = slugify(self.mailchimp_tag) if self.mailchimp_tag else f"q-{slugify(self.prompt)[:40]}"
        if not prefix:
            return None
        if self.question_type == self.QuestionType.YES_NO:
            if value.strip().lower() not in ("yes", "true", "on", "1"):
                return None
            return prefix
        answer_slug = slugify(value)[:40]
        if not answer_slug:
            return None
        return f"{prefix}-{answer_slug}"


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
        "membership.Member",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_messages",
        help_text="The member/instructor who sent this, or NULL if sent by an admin.",
    )
    sent_by = models.ForeignKey(
        "membership.Member",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_class_messages",
        help_text="The user who actually sent the message (admin or instructor).",
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


class CmsActivity(models.Model):
    """Append-only event log for every meaningful CMS happening.

    One row per event. Written via ``classes.activity.log()`` from each
    workflow point (class lifecycle, registration lifecycle, waitlist,
    discount code redemption, etc.) so the admin Activity tab can show a
    single chronological feed. The Kind enum below is the canonical list of
    events the UI knows about; ``payload`` carries any free-form per-kind
    detail the feed wants to render.
    """

    class Kind(models.TextChoices):
        CLASS_CREATED = "class_created", "Class created"
        CLASS_SUBMITTED = "class_submitted", "Submitted for review"
        CLASS_APPROVED = "class_approved", "Approved"
        CLASS_CHANGES_REQUESTED = "class_changes_requested", "Changes requested"
        CLASS_DENIED = "class_denied", "Declined"
        CLASS_PUBLISHED = "class_published", "Published"
        CLASS_ARCHIVED = "class_archived", "Archived"
        REGISTRATION_CREATED = "registration_created", "Registered"
        REGISTRATION_CONFIRMED = "registration_confirmed", "Payment confirmed"
        REGISTRATION_CANCELLED = "registration_cancelled", "Cancelled"
        REGISTRATION_REFUNDED = "registration_refunded", "Refunded"
        REGISTRATION_MOVED = "registration_moved", "Moved"
        WAITLIST_JOINED = "waitlist_joined", "Joined waitlist"
        WAITLIST_NOTIFIED = "waitlist_notified", "Notified of open spot"
        WAITLIST_LEFT = "waitlist_left", "Left waitlist"
        DISCOUNT_CODE_CREATED = "discount_code_created", "Discount code created"
        DISCOUNT_CODE_REDEEMED = "discount_code_redeemed", "Discount code redeemed"

    kind = models.CharField(max_length=40, choices=Kind.choices, help_text="What happened.")
    class_offering = models.ForeignKey(
        "ClassOffering",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activity",
        help_text="Class this event belongs to, when applicable.",
    )
    registration = models.ForeignKey(
        "Registration",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activity",
        help_text="Registration this event belongs to, when applicable.",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="User who triggered this. Null for system or anonymous events.",
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Free-form per-kind detail: discount code, notes excerpt, refund "
            "amount, etc. The feed UI is the only consumer."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["class_offering", "-created_at"]),
            models.Index(fields=["kind", "-created_at"]),
        ]
        verbose_name_plural = "CMS activity"

    def __str__(self) -> str:
        return f"{self.get_kind_display()} @ {self.created_at:%Y-%m-%d %H:%M}"


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
    waitlist_claim_window_hours = models.PositiveIntegerField(
        default=24,
        help_text=(
            "When a waitlisted person is notified that a spot opened, how many "
            "hours they have to register before we move on to the next person."
        ),
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
