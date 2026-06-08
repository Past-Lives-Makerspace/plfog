from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.files import delete_orphan_on_replace
from core.validators import validate_image_size
from membership.managers import MemberEmailManager

DEFAULT_PRICE_PER_SQFT = Decimal("3.75")


def _active_lease_q(prefix: str = "", today: date_type | None = None) -> Q:
    """Build the Q-object filter for active leases.

    Args:
        prefix: Field prefix for related lookups (e.g. "leases__").
        today: Reference date; defaults to today.
    """
    if today is None:
        today = timezone.now().date()
    start = f"{prefix}start_date__lte"
    end_null = f"{prefix}end_date__isnull"
    end_gte = f"{prefix}end_date__gte"
    return Q(**{start: today}) & (Q(**{end_null: True}) | Q(**{end_gte: today}))


# ---------------------------------------------------------------------------
# MembershipPlan
# ---------------------------------------------------------------------------


class MembershipPlan(models.Model):
    # Queryset annotation (set by MembershipPlanAdmin.get_queryset)
    member_count: int

    name = models.CharField(max_length=100, unique=True)
    monthly_price = models.DecimalField(max_digits=8, decimal_places=2)
    deposit_required = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Membership Plan"
        verbose_name_plural = "Membership Plans"

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# Member
# ---------------------------------------------------------------------------


class MemberQuerySet(models.QuerySet):
    def active(self) -> MemberQuerySet:
        return self.filter(status=Member.Status.ACTIVE)

    def paying(self) -> MemberQuerySet:
        """Only standard members count as paying."""
        return self.filter(member_type=Member.MemberType.STANDARD)

    def with_lease_totals(self) -> MemberQuerySet:
        active_filter = _active_lease_q(prefix="leases__")
        return self.annotate(
            active_lease_count=models.Count("leases", filter=active_filter),
            total_monthly_rent=Coalesce(
                Sum(
                    "leases__monthly_rent",
                    filter=active_filter,
                    output_field=DecimalField(),
                ),
                Value(Decimal("0.00")),
                output_field=DecimalField(),
            ),
        )


class Member(models.Model):
    # Queryset annotation (set by MemberQuerySet.with_lease_totals)
    total_monthly_rent: Decimal

    class Status(models.TextChoices):
        INVITED = "invited", "Invited"
        ACTIVE = "active", "Active"
        FORMER = "former", "Former"
        SUSPENDED = "suspended", "Suspended"

    class MemberType(models.TextChoices):
        STANDARD = "standard", "Standard"
        GUILD_LEAD = "guild_lead", "Guild Lead"
        WORK_TRADE = "work_trade", "Work-Trade"
        EMPLOYEE = "employee", "Employee"
        CONTRACTOR = "contractor", "Contractor"
        VOLUNTEER = "volunteer", "Volunteer"

    class FogRole(models.TextChoices):
        MEMBER = "member", "Member"
        GUILD_OFFICER = "guild_officer", "Guild Officer"
        ADMIN = "admin", "Admin"

    class Pronouns(models.TextChoices):
        HE_HIM = "he/him", "he/him"
        SHE_HER = "she/her", "she/her"
        THEY_THEM = "they/them", "they/them"
        HE_THEY = "he/they", "he/they"
        SHE_THEY = "she/they", "she/they"
        ALL_THREE = "he/she/they", "he/she/they"
        ZE_HIR = "ze/hir", "ze/hir"
        XE_XEM = "xe/xem", "xe/xem"
        PREFER_NOT = "prefer not to share", "Prefer not to share"

    airtable_record_id = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        help_text="Airtable record ID for bidirectional sync.",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    full_legal_name = models.CharField(max_length=255)
    preferred_name = models.CharField(max_length=255, blank=True)
    _pre_signup_email = models.EmailField(
        blank=True,
        default="",
        db_column="email",  # keep existing DB column name to avoid an extra migration
        help_text=(
            "Stored email used ONLY when this Member has no linked User. "
            "Once a User is linked, allauth.account.EmailAddress becomes the source of truth; "
            "read `member.primary_email` instead. See "
            "docs/superpowers/specs/2026-04-07-user-email-aliases-design.md for the full architecture."
        ),
    )
    phone = models.CharField(max_length=20, blank=True)
    discord_handle = models.CharField(
        max_length=100, blank=True, help_text="Discord username (e.g. user#1234 or @user)."
    )
    other_contact_info = models.CharField(
        max_length=255, blank=True, help_text="Other ways to reach this member (Instagram, Signal, etc.)."
    )
    pronouns = models.CharField(
        max_length=30,
        choices=Pronouns.choices,
        blank=True,
        default="",
        help_text="Pronouns shown in the member directory.",
    )
    about_me = models.TextField(blank=True, help_text="Short bio shown in the member directory.")
    profile_photo = models.ImageField(
        upload_to="members/profile/",
        blank=True,
        validators=[validate_image_size],
        help_text="Profile photo shown in the member directory.",
    )
    billing_name = models.CharField(max_length=255, blank=True)

    # Emergency contact
    emergency_contact_name = models.CharField(max_length=255, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    emergency_contact_relationship = models.CharField(max_length=100, blank=True)

    membership_plan = models.ForeignKey(MembershipPlan, on_delete=models.PROTECT)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    member_type = models.CharField(
        max_length=20,
        choices=MemberType.choices,
        default=MemberType.STANDARD,
        help_text="What kind of member (standard, guild lead, work-trade, etc.).",
    )
    fog_role = models.CharField(
        max_length=20,
        choices=FogRole.choices,
        default=FogRole.MEMBER,
        help_text="FOG access level: admin (full access), guild officer (no site settings), member (hub only).",
    )
    join_date = models.DateField(null=True, blank=True)
    cancellation_date = models.DateField(null=True, blank=True)
    committed_until = models.DateField(null=True, blank=True)
    show_in_directory = models.BooleanField(
        default=False, help_text="Whether this member appears in the public member directory."
    )
    directory_visibility = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Per-field public/hidden flags for the member directory card. "
            "Keys: pronouns, phone, email, discord_handle, other_contact_info, about_me, profile_photo. "
            "Missing key means public (default-on)."
        ),
    )
    instructor_slug = models.SlugField(
        max_length=255,
        blank=True,
        help_text="URL slug for this member's public instructor profile. Non-empty = teaches classes.",
    )
    instructor_website = models.URLField(blank=True, help_text="Instructor personal site.")
    instructor_social_handle = models.CharField(
        max_length=255, blank=True, help_text="e.g. @handle on primary social (instructor profile)."
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    leases = GenericRelation(
        "Lease",
        content_type_field="content_type",
        object_id_field="object_id",
    )

    objects = MemberQuerySet.as_manager()

    class Meta:
        ordering = ["full_legal_name"]
        verbose_name = "Member"
        verbose_name_plural = "Members"
        constraints = [
            models.UniqueConstraint(
                fields=["instructor_slug"],
                condition=~models.Q(instructor_slug=""),
                name="uq_member_instructor_slug",
            ),
        ]

    DIRECTORY_TOGGLEABLE_FIELDS: tuple[str, ...] = (
        "pronouns",
        "phone",
        "email",
        "discord_handle",
        "other_contact_info",
        "about_me",
        "profile_photo",
    )

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        return self.preferred_name if self.preferred_name else self.full_legal_name

    def is_public(self, field_name: str) -> bool:
        """Return True if a directory-toggleable field should appear on this member's card.

        Defaults to True (public) when the key is missing — existing members aren't
        accidentally hidden after this feature ships.
        """
        return bool(self.directory_visibility.get(field_name, True))

    @property
    def primary_email(self) -> str:
        """Return the live primary email for this member.

        THREE-EMAIL-STORE NOTE: This project has three places an email can live
        (see docs/superpowers/specs/2026-04-07-user-email-aliases-design.md):

        1. ``self._pre_signup_email`` - stored field, used ONLY when self.user is None.
        2. ``allauth.account.EmailAddress`` - source of truth for linked users.
        3. ``User.email`` - mirrored from (2) by allauth; used as a fallback only.

        Never read ``self._pre_signup_email`` directly outside of Airtable sync
        and admin-for-unlinked-members flows. Use this property instead.

        List views rendering many members must prefetch the primary EmailAddress
        rows with ``Prefetch("user__emailaddress_set", ..., to_attr="_primary_emailaddresses")``
        to avoid an N+1; when present, this property uses the prefetched list
        instead of hitting the database.
        """
        if self.user_id is None:
            return self._pre_signup_email
        user = self.user
        # Use the list populated by a view's Prefetch, if any.
        prefetched = getattr(user, "_primary_emailaddresses", None)
        if prefetched is not None:
            if prefetched:
                return prefetched[0].email
            return (user.email if user else "") or ""
        # No prefetch: single targeted query keyed on user_id (avoids a User fetch).
        from allauth.account.models import EmailAddress

        primary = EmailAddress.objects.filter(user_id=self.user_id, primary=True).first()
        if primary is not None:
            return primary.email
        return (user.email if user else "") or ""

    @property
    def initials(self) -> str:
        """Compute display initials from the linked user's name or email."""
        if self.user is None:
            return ""
        email = getattr(self.user, "email", "") or ""
        name = getattr(self.user, "get_full_name", lambda: "")() or email
        parts = name.strip().split()
        result = ""
        if parts:
            result = "".join(p[0].upper() for p in parts[:2])
        if not result and email:
            result = email[0].upper()
        return result

    @property
    def active_leases(self) -> models.QuerySet[Lease]:
        return self.leases.filter(_active_lease_q())

    @property
    def current_spaces(self) -> models.QuerySet[Space]:
        return Space.objects.filter(pk__in=self.active_leases.values("space"))

    @property
    def studio_storage_total(self) -> Decimal:
        total = self.active_leases.aggregate(
            total=Coalesce(
                Sum("monthly_rent"),
                Value(Decimal("0.00")),
                output_field=DecimalField(),
            )
        )["total"]
        return total

    @property
    def membership_monthly_dues(self) -> Decimal:
        return self.membership_plan.monthly_price

    @property
    def total_monthly_spend(self) -> Decimal:
        return self.membership_monthly_dues + self.studio_storage_total

    @property
    def is_paying(self) -> bool:
        """Only standard members are paying members."""
        return self.member_type == self.MemberType.STANDARD

    @property
    def is_fog_admin(self) -> bool:
        """True when fog_role is admin (full access)."""
        return self.fog_role == self.FogRole.ADMIN

    @property
    def is_guild_officer(self) -> bool:
        """True when fog_role is guild_officer (admin access without site settings)."""
        return self.fog_role == self.FogRole.GUILD_OFFICER

    def can_edit_guild(self, guild: Guild) -> bool:
        """True when this member may edit the given guild (admin, officer, or that guild's lead)."""
        return self.is_fog_admin or self.is_guild_officer or guild.guild_lead_id == self.pk

    @property
    def is_guild_lead(self) -> bool:
        """True when this member leads at least one guild."""
        return Guild.objects.filter(guild_lead=self).exists()

    @property
    def is_instructor(self) -> bool:
        """True when this member has a public instructor profile (instructor_slug is set)."""
        return bool(self.instructor_slug)

    @property
    def must_be_listed_in_directory(self) -> bool:
        """Roles that can never opt out of the member directory.

        Admins, Guild Officers, Guild Leads, and Instructors are public-facing —
        members need to be able to find them. They cannot hide via show_in_directory.
        """
        return self.is_fog_admin or self.is_guild_officer or self.is_guild_lead or self.is_instructor

    ADMIN_ROLE_INSTRUCTOR = "instructor"
    ADMIN_ROLE_GUEST = "guest"

    def apply_admin_role(self, picked_role: str) -> None:
        """Apply a role token from the admin Member edit form.

        Maps the dropdown's role token (`admin` / `guild_officer` / `member` /
        `instructor` / `guest`) onto the right combination of `fog_role`,
        `status`, and instructor_slug. Idempotent — re-promoting an existing
        instructor is a no-op if they already have a slug.
        """
        from django.utils.text import slugify

        valid = {c.value for c in self.FogRole} | {self.ADMIN_ROLE_INSTRUCTOR, self.ADMIN_ROLE_GUEST}
        if picked_role not in valid:
            raise ValueError(f"Invalid admin role token: {picked_role!r}")

        if picked_role == self.ADMIN_ROLE_INSTRUCTOR:
            self.fog_role = self.FogRole.MEMBER
            self.status = self.Status.ACTIVE
            if not self.instructor_slug:
                base = slugify(self.display_name or self.full_legal_name) or f"instructor-{self.pk}"
                slug = base
                n = 1
                while Member.objects.filter(instructor_slug=slug).exclude(pk=self.pk).exists():
                    n += 1
                    slug = f"{base}-{n}"
                self.instructor_slug = slug
        elif picked_role == self.ADMIN_ROLE_GUEST:
            self.fog_role = self.FogRole.MEMBER
            self.status = self.Status.FORMER
        else:
            self.fog_role = picked_role
        self.save()

    def set_fog_role(self, new_role: str, *, changed_by: Member) -> None:
        """Change this member's fog_role with permission checks.

        Admins can assign any role. Guild officers can assign member or guild_officer
        but not admin. Regular members cannot change roles.

        Args:
            new_role: The FogRole value to set.
            changed_by: The Member performing the change.

        Raises:
            PermissionError: If the caller lacks permission.
            ValueError: If the role value is invalid.
        """
        if new_role not in {c.value for c in self.FogRole}:
            raise ValueError(f"Invalid role: {new_role}")

        if changed_by.is_fog_admin:
            pass  # admins can do anything
        elif changed_by.is_guild_officer:
            if new_role == self.FogRole.ADMIN:
                raise PermissionError("Guild officers cannot grant admin access.")
        else:
            raise PermissionError("Only admins and guild officers can change roles.")

        self.fog_role = new_role
        self.save(update_fields=["fog_role"])
        self.sync_user_permissions()

    def sync_user_permissions(self) -> None:
        """Set is_staff/is_superuser on the linked User based on fog_role.

        Admin gets full access. Guild officers get staff access but not
        superuser. Members lose staff access. Skips save if nothing changed.
        """
        if self.user is None:
            return

        if self.is_fog_admin:
            new_staff, new_super = True, True
        elif self.is_guild_officer:
            new_staff, new_super = True, False
        else:
            new_staff, new_super = False, False

        if self.user.is_staff == new_staff and self.user.is_superuser == new_super:
            return

        self.user.is_staff = new_staff
        self.user.is_superuser = new_super
        self.user.save(update_fields=["is_staff", "is_superuser"])

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Member records are otherwise managed in Airtable; this override only
        # cleans up the orphaned profile_photo file when the user replaces it.
        delete_orphan_on_replace(self, "profile_photo")
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# MemberEmail
# ---------------------------------------------------------------------------


class MemberEmail(models.Model):
    """Pre-signup staging table for member email addresses.

    THREE-EMAIL-STORE NOTE (see
    docs/superpowers/specs/2026-04-07-user-email-aliases-design.md):

    This table holds known email addresses for Member records that do NOT
    yet have a linked User (typically imported from Airtable). When a User
    is linked to the Member, ``MemberEmail.objects.migrate_to_user(user)``
    promotes every row into ``allauth.account.EmailAddress`` and deletes the
    staging rows. After that, EmailAddress is the source of truth; do NOT
    read MemberEmail for login lookups on linked members.

    The ``is_primary`` field was removed in version 1.4.0 because Member
    already has a dedicated stored email (``_pre_signup_email``); a second
    primary flag on a staging row was meaningless and confusing in the
    admin inline.
    """

    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="emails",
        help_text="The unlinked member this staged email belongs to.",
    )
    email = models.EmailField(unique=True, help_text="A staged email address for this member.")

    objects = MemberEmailManager()

    class Meta:
        ordering = ["email"]
        verbose_name = "Staged Email (pre-signup)"
        verbose_name_plural = "Staged Emails (pre-signup)"

    def __str__(self) -> str:
        return f"{self.email} ({self.member.display_name})"


# ---------------------------------------------------------------------------
# Guild
# ---------------------------------------------------------------------------


class Guild(models.Model):
    # Queryset annotation (set by GuildAdmin.get_queryset)
    sublet_count: int

    name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True, help_text="Whether this guild is eligible for voting and display.")
    guild_lead = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="led_guilds",
    )
    notes = models.TextField(blank=True)
    about = models.TextField(
        blank=True,
        default="",
        help_text="Member-facing description or announcement shown on the guild page.",
    )
    banner_image = models.ImageField(
        upload_to="guilds/banners/",
        blank=True,
        validators=[validate_image_size],
        help_text="Banner image shown at the top of the guild page.",
    )
    calendar_url = models.URLField(
        blank=True,
        default="",
        help_text="Public iCal URL for this guild's Google Calendar (File → Share → Get shareable iCal link).",
    )
    calendar_color = models.CharField(
        max_length=7,
        blank=True,
        default="#4B9FEE",
        help_text="Hex color code for this guild's events on the Community Calendar (e.g. #4B9FEE).",
    )
    calendar_last_fetched_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this guild's iCal feed was last synced. Set by the calendar service.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    leases = GenericRelation(
        "Lease",
        content_type_field="content_type",
        object_id_field="object_id",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "Guild"
        verbose_name_plural = "Guilds"

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        delete_orphan_on_replace(self, "banner_image")
        super().save(*args, **kwargs)

    @property
    def active_leases(self) -> models.QuerySet[Lease]:
        return self.leases.filter(_active_lease_q())

    @property
    def sublet_revenue(self) -> Decimal:
        """Sum of monthly_rent from active leases on spaces sublet to this guild."""
        total = Lease.objects.filter(
            _active_lease_q(),
            space__sublet_guild=self,
        ).aggregate(
            total=Coalesce(
                Sum("monthly_rent", output_field=DecimalField()),
                Value(Decimal("0.00")),
                output_field=DecimalField(),
            ),
        )["total"]
        return total


class VotePreferenceQuerySet(models.QuerySet):
    def from_signed_up_members(self) -> VotePreferenceQuerySet:
        """Votes cast by members who have a linked User account.

        Excludes VotePreferences created by Airtable backfill for members
        who were imported but never signed up to the Django app. Only
        signed-up members should influence live standings or snapshots.
        """
        return self.filter(member__user__isnull=False)


class VotePreference(models.Model):
    """Persistent guild funding vote per member — updated anytime, one row per member."""

    airtable_record_id = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        help_text="Airtable record ID for bidirectional sync.",
    )
    member = models.OneToOneField(
        Member,
        on_delete=models.CASCADE,
        related_name="vote_preference",
        help_text="The member who cast this vote.",
    )
    guild_1st = models.ForeignKey(
        Guild,
        on_delete=models.CASCADE,
        related_name="first_choice_votes",
        help_text="First-choice guild (5 points).",
    )
    guild_2nd = models.ForeignKey(
        Guild,
        on_delete=models.CASCADE,
        related_name="second_choice_votes",
        help_text="Second-choice guild (3 points).",
    )
    guild_3rd = models.ForeignKey(
        Guild,
        on_delete=models.CASCADE,
        related_name="third_choice_votes",
        help_text="Third-choice guild (2 points).",
    )
    updated_at = models.DateTimeField(auto_now=True, help_text="When this vote was last changed.")

    objects = VotePreferenceQuerySet.as_manager()

    class Meta:
        verbose_name = "Vote Preference"
        verbose_name_plural = "Vote Preferences"

    def __str__(self) -> str:
        return f"{self.member.display_name}: {self.guild_1st} / {self.guild_2nd} / {self.guild_3rd}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        super().save(*args, **kwargs)
        if not getattr(self, "_skip_airtable_sync", False):
            from airtable_sync.service import sync_vote_to_airtable

            sync_vote_to_airtable(self)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        record_id = self.airtable_record_id
        result = super().delete(*args, **kwargs)
        if record_id and not getattr(self, "_skip_airtable_sync", False):
            from airtable_sync.service import delete_vote_from_airtable

            delete_vote_from_airtable(record_id)
        return result


class FundingSnapshot(models.Model):
    """Immutable historical record of a funding calculation at a point in time."""

    airtable_record_id = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        help_text="Airtable record ID for bidirectional sync.",
    )
    cycle_label = models.CharField(
        max_length=100, help_text="Human-readable label for the funding cycle (e.g. 'March 2026')."
    )
    snapshot_at = models.DateTimeField(auto_now_add=True, help_text="When this snapshot was taken.")
    contributor_count = models.PositiveIntegerField(
        help_text="Number of paying members who contributed to the funding pool."
    )
    funding_pool = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Total dollar pool (max of paying_voters × $10 and minimum_pool).",
    )
    minimum_pool = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "Minimum dollar floor applied to the funding pool at snapshot time. "
            "New snapshots default to $1,000; historical snapshots default to 0 so "
            "their original numbers are preserved."
        ),
    )
    raw_votes = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Frozen list of individual votes at snapshot time. Each entry has "
            "member_id, member_name, member_type, fog_role, is_paying, and the "
            "three guild picks (id + name). Drives the admin analyzer view."
        ),
    )
    results = models.JSONField(
        default=dict,
        encoder=DjangoJSONEncoder,
        help_text="Full calculation results including per-guild breakdowns. Decimals are serialized as strings.",
    )

    class Meta:
        ordering = ["-snapshot_at"]
        verbose_name = "Funding Snapshot"
        verbose_name_plural = "Funding Snapshots"

    def __str__(self) -> str:
        return f"{self.cycle_label} — ${self.funding_pool}"

    @classmethod
    def take(
        cls,
        *,
        title: str = "",
        minimum_pool: Decimal | int = 1000,
    ) -> FundingSnapshot | None:
        """Create a snapshot from current vote preferences.

        Args:
            title: Custom label for the snapshot. Defaults to current month/year.
            minimum_pool: Dollar floor applied to the funding pool. Pool is
                ``max(paying_voters × $10, minimum_pool)``. Defaults to $1,000.

        Returns:
            The created FundingSnapshot, or None if no votes exist.
        """
        from membership.vote_calculator import calculate_results

        preferences = VotePreference.objects.from_signed_up_members().select_related(
            "member",
            "guild_1st",
            "guild_2nd",
            "guild_3rd",
        )

        if not preferences.exists():
            return None

        raw_votes = [
            {
                "member_id": pref.member_id,
                "member_name": pref.member.display_name,
                "member_type": pref.member.member_type,
                "fog_role": pref.member.fog_role,
                "is_paying": pref.member.is_paying,
                "guild_1st_id": pref.guild_1st_id,
                "guild_1st_name": pref.guild_1st.name,
                "guild_2nd_id": pref.guild_2nd_id,
                "guild_2nd_name": pref.guild_2nd.name,
                "guild_3rd_id": pref.guild_3rd_id,
                "guild_3rd_name": pref.guild_3rd.name,
            }
            for pref in preferences
        ]

        paying_count = sum(1 for v in raw_votes if v["is_paying"])
        votes_for_calc = [
            {
                "guild_1st": v["guild_1st_name"],
                "guild_2nd": v["guild_2nd_name"],
                "guild_3rd": v["guild_3rd_name"],
            }
            for v in raw_votes
        ]

        minimum_pool_value = Decimal(minimum_pool)
        calc = calculate_results(
            votes_for_calc,
            paying_voter_count=paying_count,
            minimum_pool=minimum_pool_value,
        )

        cycle_label = title.strip() if title.strip() else timezone.now().strftime("%B %Y")

        snapshot = cls.objects.create(
            cycle_label=cycle_label,
            contributor_count=paying_count,
            funding_pool=calc["total_pool"],
            minimum_pool=minimum_pool_value,
            raw_votes=raw_votes,
            results=calc,
        )
        from core.models import SiteActivity

        SiteActivity.log(SiteActivity.Kind.FUNDING_SNAPSHOT_TAKEN, target=snapshot)
        return snapshot

    def save(self, *args: Any, **kwargs: Any) -> None:
        super().save(*args, **kwargs)
        if not getattr(self, "_skip_airtable_sync", False):
            from airtable_sync.service import sync_snapshot_to_airtable

            sync_snapshot_to_airtable(self)


# ---------------------------------------------------------------------------
# Space
# ---------------------------------------------------------------------------


class SpaceQuerySet(models.QuerySet):
    def available(self) -> SpaceQuerySet:
        return self.filter(status=Space.Status.AVAILABLE)

    def with_revenue(self) -> SpaceQuerySet:
        active_filter = _active_lease_q(prefix="leases__")
        return self.annotate(
            active_lease_rent_total=Coalesce(
                Sum(
                    "leases__monthly_rent",
                    filter=active_filter,
                    output_field=DecimalField(),
                ),
                Value(Decimal("0.00")),
                output_field=DecimalField(),
            ),
        )


class Space(models.Model):
    # Queryset annotation (set by SpaceQuerySet.with_revenue)
    active_lease_rent_total: Decimal

    class SpaceType(models.TextChoices):
        STUDIO = "studio", "Studio"
        STORAGE = "storage", "Storage"
        PARKING = "parking", "Parking"
        DESK = "desk", "Desk"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        OCCUPIED = "occupied", "Occupied"
        MAINTENANCE = "maintenance", "Maintenance"

    airtable_record_id = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        help_text="Airtable record ID for bidirectional sync.",
    )
    space_id = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=255, blank=True)
    space_type = models.CharField(
        max_length=20,
        choices=SpaceType.choices,
    )
    size_sqft = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    width = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    depth = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    rate_per_sqft = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    is_rentable = models.BooleanField(default=True)
    manual_price = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.AVAILABLE,
    )
    photo = models.ImageField(
        upload_to="spaces/",
        blank=True,
        validators=[validate_image_size],
        help_text="Optional photo of the space, shown on the space detail page.",
    )
    floorplan_ref = models.CharField(max_length=100, blank=True)
    sublet_guild = models.ForeignKey(
        "Guild",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sublets",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SpaceQuerySet.as_manager()

    class Meta:
        ordering = ["space_id"]
        verbose_name = "Space"
        verbose_name_plural = "Spaces"

    def __str__(self) -> str:
        if self.name:
            return f"{self.space_id} - {self.name}"
        return self.space_id

    def save(self, *args: Any, **kwargs: Any) -> None:
        delete_orphan_on_replace(self, "photo")
        super().save(*args, **kwargs)

    @property
    def full_price(self) -> Decimal | None:
        if self.manual_price is not None:
            return self.manual_price
        if self.size_sqft is not None:
            rate = self.rate_per_sqft if self.rate_per_sqft is not None else DEFAULT_PRICE_PER_SQFT
            return self.size_sqft * rate
        return None

    @property
    def current_occupants(self) -> list[Member | Guild]:
        """Return all active tenants (Members and Guilds) for this space."""
        active = self.leases.filter(_active_lease_q()).select_related("content_type")
        return [t for lease in active if (t := lease.tenant) is not None]

    @property
    def vacancy_value(self) -> Decimal:
        if self.status == self.Status.AVAILABLE:
            return self.full_price or Decimal("0.00")
        return Decimal("0.00")

    @property
    def actual_revenue(self) -> Decimal:
        total = self.leases.filter(_active_lease_q()).aggregate(
            total=Coalesce(
                Sum("monthly_rent"),
                Value(Decimal("0.00")),
                output_field=DecimalField(),
            )
        )["total"]
        return total

    @property
    def revenue_loss(self) -> Decimal | None:
        fp = self.full_price
        if fp is None:
            return None
        return fp - self.actual_revenue

    # Space records are managed in Airtable and pulled into Django via airtable_pull.
    # No save()/delete() sync overrides — this model is read-only from Airtable's perspective.


# ---------------------------------------------------------------------------
# Lease
# ---------------------------------------------------------------------------


class LeaseQuerySet(models.QuerySet):
    def active(self, as_of: date_type | None = None) -> LeaseQuerySet:
        return self.filter(_active_lease_q(today=as_of))


class Lease(models.Model):
    airtable_record_id = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        help_text="Airtable record ID for bidirectional sync.",
    )

    class LeaseType(models.TextChoices):
        MONTH_TO_MONTH = "month_to_month", "Month-to-Month"
        ANNUAL = "annual", "Annual"

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveIntegerField()
    tenant = GenericForeignKey("content_type", "object_id")
    space = models.ForeignKey(Space, on_delete=models.PROTECT, related_name="leases")
    lease_type = models.CharField(
        max_length=20,
        choices=LeaseType.choices,
    )
    base_price = models.DecimalField(max_digits=8, decimal_places=2)
    monthly_rent = models.DecimalField(max_digits=8, decimal_places=2)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    committed_until = models.DateField(null=True, blank=True)

    # Deposit tracking
    deposit_required = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    deposit_paid_date = models.DateField(null=True, blank=True)
    deposit_paid_amount = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    discount_reason = models.TextField(blank=True)
    is_split = models.BooleanField(default=False)
    prepaid_through = models.DateField(null=True, blank=True)

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = LeaseQuerySet.as_manager()

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "Lease"
        verbose_name_plural = "Leases"
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.tenant} @ {self.space} ({self.start_date})"

    @property
    def is_active(self) -> bool:
        today = timezone.now().date()
        if self.start_date is None or self.start_date > today:
            return False
        if self.end_date is not None and self.end_date < today:
            return False
        return True

    # Lease records are managed in Airtable and pulled into Django via airtable_pull.
    # No save()/delete() sync overrides — this model is read-only from Airtable's perspective.


# ---------------------------------------------------------------------------
# CalendarEvent
# ---------------------------------------------------------------------------


class CalendarEventQuerySet(models.QuerySet):
    def upcoming(self) -> CalendarEventQuerySet:
        """Events whose end time is now or in the future."""
        return self.filter(end_dt__gte=timezone.now())


class CalendarEvent(models.Model):
    """Cached calendar event fetched from a guild's or the general makerspace's iCal feed.

    Treat as a read-through cache — do not edit records directly; re-sync from the source.
    """

    class Source(models.TextChoices):
        GUILD = "guild", "Guild Calendar"
        GENERAL = "general", "General Calendar"
        CLASSES = "classes", "Classes (classes.pastlives.space)"

    guild = models.ForeignKey(
        "Guild",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="calendar_events",
        help_text="Guild this event belongs to. Null for general or classes events.",
    )
    feed = models.ForeignKey(
        "core.CalendarFeed",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="calendar_events",
        help_text="The named general-calendar feed this event came from. Null for guild and classes events.",
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.GUILD,
        help_text="Origin of this event: guild iCal, general makerspace iCal, or classes.pastlives.space.",
    )
    uid = models.CharField(max_length=500, db_index=True, help_text="iCal UID, unique within a source.")
    title = models.CharField(max_length=500, help_text="Event title from iCal SUMMARY field.")
    description = models.TextField(blank=True, help_text="Event description from iCal DESCRIPTION field.")
    location = models.CharField(max_length=500, blank=True, help_text="Event location from iCal LOCATION field.")
    url = models.URLField(blank=True, help_text="Event URL from iCal URL field.")
    start_dt = models.DateTimeField(help_text="Event start time, UTC-normalized.")
    end_dt = models.DateTimeField(help_text="Event end time, UTC-normalized.")
    all_day = models.BooleanField(default=False, help_text="True for all-day events (DATE not DATETIME in iCal).")
    fetched_at = models.DateTimeField(help_text="When this record was last synced from the iCal source.")

    objects = CalendarEventQuerySet.as_manager()

    class Meta:
        ordering = ["start_dt"]
        indexes = [
            models.Index(fields=["start_dt", "end_dt"], name="idx_calendarevent_start_end"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["guild", "feed", "uid"], name="uq_calendarevent_guild_feed_uid"),
        ]
        verbose_name = "Calendar Event"
        verbose_name_plural = "Calendar Events"

    def __str__(self) -> str:
        return self.title

    @property
    def source_key(self) -> str:
        """Key used to look up this event's display color in the source_colors dict."""
        if self.source == self.Source.GUILD and self.guild_id:
            return str(self.guild_id)
        if self.source == self.Source.GENERAL and self.feed_id:
            return f"feed-{self.feed_id}"
        return self.source

    @property
    def is_in_progress(self) -> bool:
        """True when the event is currently happening (start <= now < end)."""
        from django.utils import timezone

        if self.all_day:
            return False
        now = timezone.now()
        return self.start_dt <= now < self.end_dt
