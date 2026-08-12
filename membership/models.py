from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime as datetime_type
from datetime import time as time_type
from datetime import timedelta
from decimal import Decimal
from html import unescape
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import BooleanField, Case, CharField, Count, DecimalField, Exists, OuterRef, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.html import escape, format_html, strip_tags
from django.utils.safestring import SafeString

from core.files import delete_orphan_on_replace
from core.images import normalize_field_if_uploaded
from core.models import HeroCropMixin
from core.validators import validate_document, validate_image_size
from membership.managers import MemberEmailManager

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from classes.models import ClassOffering
    from core.events.channels import Message

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


def _nth_weekday(month_anchor: date_type, weekday: Any, ordinal: int) -> date_type:
    """The ordinal-th ``weekday`` of ``month_anchor``'s month (ordinal 5 = last)."""
    from dateutil.relativedelta import relativedelta

    if ordinal == 5:
        return month_anchor + relativedelta(day=31, weekday=weekday(-1))
    return month_anchor + relativedelta(day=1, weekday=weekday(ordinal))


def _compute_next_meeting(
    today: date_type,
    *,
    cadence: str,
    weekday: int | None,
    week_of_month: int | None,
    override: date_type | None,
    is_tba: bool,
) -> date_type | None:
    """The next guild-meeting date from a cadence config, or None for TBA.

    Precedence: an explicit TBA flag wins, then a future manual override, then
    the monthly recurrence. Every cadence is monthly-based (monthly, every two
    months, or every three months) anchored on the nth weekday of the month;
    when this month's instance has already passed it rolls forward by the
    cadence interval. Returns None when nothing is configured.
    """
    if is_tba:
        return None
    if override is not None and override >= today:
        return override
    intervals: dict[str, int] = {
        Guild.MeetingCadence.MONTHLY: 1,
        Guild.MeetingCadence.EVERY_2_MONTHS: 2,
        Guild.MeetingCadence.EVERY_3_MONTHS: 3,
    }
    interval = intervals.get(cadence)
    if interval is None or weekday is None or week_of_month is None:
        return None
    from dateutil.relativedelta import FR, MO, SA, SU, TH, TU, WE, relativedelta

    wd = (MO, TU, WE, TH, FR, SA, SU)[weekday]
    first = today.replace(day=1)
    candidate = _nth_weekday(first, wd, week_of_month)
    if candidate < today:
        candidate = _nth_weekday(first + relativedelta(months=interval), wd, week_of_month)
    return candidate


def _format_time_range(start: datetime_type, end: datetime_type) -> str:
    """'6:00–9:00 PM' when both share a meridiem, else '11:00 AM–1:00 PM' (local datetimes)."""
    if start.strftime("%p") == end.strftime("%p"):
        return f"{start.strftime('%-I:%M')}–{end.strftime('%-I:%M %p')}"
    return f"{start.strftime('%-I:%M %p')}–{end.strftime('%-I:%M %p')}"


class NextMeeting(NamedTuple):
    """A guild's soonest upcoming meeting, for the two "Next Meeting" surfaces.

    ``when`` is the start datetime (aware). ``has_time`` is False only for a legacy cadence
    that has no ``meeting_time`` set, so the card can render the date without a bogus midnight
    time; an event-derived meeting always carries a real time and its own ``location``.
    """

    when: datetime_type
    location: str
    has_time: bool


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

    def with_email_status(self) -> MemberQuerySet:
        """Annotate ``has_email`` and ``email_gap`` for the Missing-email report.

        ``has_email`` mirrors the :attr:`Member.primary_email` property at the DB
        level so the SQL filter and the Python property can never disagree:
        unlinked members have an email when ``_pre_signup_email`` is non-blank;
        linked members have one when a primary ``EmailAddress`` exists *or* the
        mirror (``user.email``) is non-blank. ``email_gap`` records *why* a member
        is emailless as a :class:`Member.EmailGap` value.

        ``_has_primary_email`` is annotated first so the second ``annotate()`` can
        reference it; the ``EmailAddress`` import stays local to avoid a
        module-level allauth dependency (matching ``primary_email``).
        """
        from allauth.account.models import EmailAddress

        has_primary = Exists(EmailAddress.objects.filter(user_id=OuterRef("user_id"), primary=True))
        return self.annotate(_has_primary_email=has_primary).annotate(
            has_email=Case(
                When(Q(user__isnull=True) & ~Q(_pre_signup_email=""), then=Value(True)),
                When(_has_primary_email=True, then=Value(True)),
                When(Q(user__isnull=False) & ~Q(user__email=""), then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            ),
            email_gap=Case(
                When(Q(user__isnull=True) & Q(_pre_signup_email=""), then=Value(Member.EmailGap.NO_AIRTABLE_EMAIL)),
                When(
                    Q(user__isnull=False) & Q(_has_primary_email=False) & Q(user__email=""),
                    then=Value(Member.EmailGap.NO_ACCOUNT_EMAIL),
                ),
                default=Value(""),
                output_field=CharField(),
            ),
        )

    def missing_email(self) -> MemberQuerySet:
        """Only members whose primary_email resolves blank (no usable email)."""
        return self.with_email_status().filter(has_email=False)

    def with_skill(self, slug: str) -> MemberQuerySet:
        """Members who list an approved skill with the given slug."""
        return self.filter(skills__skill__slug=slug, skills__skill__status=Skill.Status.APPROVED)

    def open_for_commissions(self) -> MemberQuerySet:
        """Members who have flagged themselves open for commissions."""
        return self.filter(open_for_commissions=True)

    def search_skills(self, text: str) -> MemberQuerySet:
        """Members whose display name or an approved skill name contains ``text`` (case-insensitive)."""
        approved = models.Q(skills__skill__status=Skill.Status.APPROVED)
        return self.filter(
            models.Q(preferred_name__icontains=text)
            | models.Q(full_legal_name__icontains=text)
            | (approved & models.Q(skills__skill__name__icontains=text))
        ).distinct()

    def directory_visible(self) -> MemberQuerySet:
        """Active members listed in the directory: opted in, or holding a must-show role.

        The single source of truth for "who appears in the member directory" — the hub
        directory page, :meth:`Guild.roster_members`, and the Discord ``/members`` command
        all build on this filter so they can never drift. A member appears when they are
        ACTIVE and either opted in (``show_in_directory``) or hold a role the community
        needs to reach: admin, guild officer, guild lead, or instructor.
        """
        must_show = (
            models.Q(fog_role=Member.FogRole.ADMIN)
            | models.Q(fog_role=Member.FogRole.GUILD_OFFICER)
            | models.Q(led_guilds__isnull=False)
            | models.Q(instructor_slug__gt="")
        )
        return self.filter(status=Member.Status.ACTIVE).filter(models.Q(show_in_directory=True) | must_show).distinct()


@dataclass(frozen=True)
class ProfileCompleteness:
    """Result of :attr:`Member.profile_completeness` — a profile-completion checklist.

    ``missing`` is the ordered list of still-empty field labels (member-friendly text),
    ``complete`` is True when nothing is missing, and ``percent`` is the 0–100 share of
    checked fields that are filled in. Pure derived data — drives the home dashboard's
    "Finish your profile" nudge and any progress display from one source of truth.

    ``essentials_complete`` is the same check *excluding the directory-listing preference*
    — every profile **content** signal (photo, bio, pronouns, Discord) is filled, regardless
    of whether the member opts to be listed. The onboarding gate uses this so a member who
    legitimately hides from the directory is still counted as onboarded (``complete`` would
    leave them stuck forever). See :attr:`Member.is_onboarded`.
    """

    missing: list[str]
    complete: bool
    percent: int
    essentials_complete: bool


@dataclass(frozen=True)
class OnboardingStep:
    """One row in the home "Get started" onboarding checklist (:attr:`Member.onboarding`).

    A derived status row that links to the existing page which completes it — the member
    never adds/removes/toggles steps here, they *do* them elsewhere. ``optional`` marks the
    recommended-but-not-required voting step; ``hint`` carries the profile percent while
    that step is undone (empty otherwise).
    """

    key: str
    label: str
    done: bool
    url: str
    optional: bool
    hint: str


@dataclass(frozen=True)
class OnboardingChecklist:
    """The three-step home onboarding checklist for a member (:attr:`Member.onboarding`).

    ``required_done`` / ``required_total`` count only the non-optional steps (profile +
    guilds), so the optional voting step can never make the progress read "2 of 3" forever;
    ``complete`` mirrors :attr:`Member.is_onboarded`.
    """

    steps: list[OnboardingStep]
    required_done: int
    required_total: int
    complete: bool


class Member(models.Model):
    # Queryset annotation (set by MemberQuerySet.with_lease_totals)
    total_monthly_rent: Decimal
    # Queryset annotations (set by MemberQuerySet.with_email_status)
    has_email: bool
    email_gap: str

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

    class EmailGap(models.TextChoices):
        """Why a member has no usable email (labels only; no field stores this).

        Derived at query time by :meth:`MemberQuerySet.with_email_status` and read
        via :attr:`Member.email_gap_label` in the Missing-email report.
        """

        NO_AIRTABLE_EMAIL = "no_airtable_email", "Never signed up — no email on file from Airtable"
        NO_ACCOUNT_EMAIL = "no_account_email", "Signed up, but has no email on their account"

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
    discord_user_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text=(
            "The member's VERIFIED numeric Discord account id (snowflake), set when they link Discord via "
            "OAuth. Used to DM them notifications through the FOG bot. Distinct from the unverified, "
            "free-text discord_handle — never use that for delivery."
        ),
    )
    discord_linked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the member linked their Discord account for DM notifications (null = not linked).",
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
        default=True,
        help_text=(
            "Whether this member appears in the public member directory. New members are listed by "
            "default; they can opt out any time in profile settings."
        ),
    )
    directory_visibility = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Per-field public/hidden flags for the member directory card. "
            "Keys: pronouns, phone, email, discord_handle, about_me, profile_photo, skills. "
            "Missing key means public (default-on)."
        ),
    )
    open_for_commissions = models.BooleanField(
        default=False,
        help_text="When on, the member shows an 'Open for commissions!' badge and appears in that filter.",
    )
    commission_note = models.CharField(
        max_length=280,
        blank=True,
        help_text="Short note on the kind of paid or commissioned work the member welcomes.",
    )
    instructor_slug = models.SlugField(
        max_length=255,
        blank=True,
        help_text="URL slug for this member's public instructor profile. Non-empty = teaches classes.",
    )
    instructor_bio = models.TextField(
        blank=True,
        help_text="Teaching bio shown on the public instructor page — separate from the member-directory bio.",
    )
    can_self_approve_discounts = models.BooleanField(
        default=False,
        help_text=(
            "When on, this member may approve (activate) their own discount codes without waiting for an "
            "admin. Granted per-member from the Manage Members page. Admins can approve anyone's codes."
        ),
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    welcome_dismissed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the member dismissed the first-login 'set up your profile' welcome modal "
            "(null = not dismissed yet). Set once they act on it, so it never shows again."
        ),
    )
    onboarding_dismissed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the member dismissed the home 'Get started' checklist card; null = never "
            "dismissed. Does NOT affect is_onboarded — only hides the card."
        ),
    )
    guided_tours_enabled = models.BooleanField(
        default=True,
        help_text="When off, tours are never auto-offered. Manual starts from the Help page still work.",
    )
    instructor_oriented_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the member completed the instructor orientation (or an admin granted teaching "
            "access); null = teaching portal locked. Cleared when an admin revokes access."
        ),
    )
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
        "about_me",
        "profile_photo",
        "skills",
    )

    MAX_SKILLS = 15

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        return self.preferred_name if self.preferred_name else self.full_legal_name

    @property
    def discord_is_linked(self) -> bool:
        """Whether this member has a verified Discord account linked for DM notifications."""
        return bool(self.discord_user_id)

    @property
    def has_started_profile(self) -> bool:
        """Whether the member has customized any part of their profile yet.

        True once a photo, bio, pronouns, or Discord (typed handle or a linked
        account) is set. Drives the first-login welcome nudge: brand-new members
        with an empty profile see it once; anyone who has already customized
        anything never does — without touching or backfilling existing rows.
        """
        return bool(self.profile_photo or self.about_me or self.pronouns or self.discord_handle or self.discord_user_id)

    @property
    def profile_completeness(self) -> ProfileCompleteness:
        """Which member-directory profile fields are filled in, as a checklist + percent.

        Checks the public-facing fields a new member is nudged to complete: a photo, a
        short bio, pronouns, a Discord link (a typed handle or a verified linked account),
        and being listed in the member directory. Returns the still-missing field labels,
        a complete flag, and a 0–100 percent, so the home dashboard's "Finish your profile"
        card reads from one source. Kept in step with :attr:`has_started_profile` (the same
        photo/bio/pronouns/Discord signals) so the first-login modal and this persistent
        nudge never disagree. Pure derived data — no query, no migration.
        """
        content_checks: list[tuple[str, bool]] = [
            ("Profile photo", bool(self.profile_photo)),
            ("Short bio", bool(self.about_me)),
            ("Pronouns", bool(self.pronouns)),
            ("Discord link", bool(self.discord_is_linked or self.discord_handle)),
        ]
        # The directory-listing preference is an opt-out, not a "content" signal — it's part
        # of the completeness percent/checklist but is EXCLUDED from ``essentials_complete``
        # (the onboarding gate), so opting out never blocks onboarding.
        checks: list[tuple[str, bool]] = [*content_checks, ("Directory listing", bool(self.show_in_directory))]
        missing = [label for label, ok in checks if not ok]
        filled = len(checks) - len(missing)
        percent = round(filled / len(checks) * 100)
        essentials_complete = all(ok for _, ok in content_checks)
        return ProfileCompleteness(
            missing=missing, complete=not missing, percent=percent, essentials_complete=essentials_complete
        )

    def dismiss_welcome(self) -> None:
        """Mark the first-login profile welcome modal as dismissed so it never shows again."""
        self.welcome_dismissed_at = timezone.now()
        self.save(update_fields=["welcome_dismissed_at"])

    def has_completed_tour(self, key: str) -> bool:
        """Whether this member finished the guided tour ``key`` (Spec C/D's frozen contract).

        Raises ``ValueError`` on a key not registered in ``core.tours.TOURS``
        (via the manager's fail-loud validation).
        """
        from core.models import TourState

        user = self.user
        if user is None:
            return False
        return TourState.objects.status_for(user, key) == TourState.Status.COMPLETED

    # --- Home onboarding ("Get started" checklist) ---

    @cached_property
    def _profile_essentials_done(self) -> bool:
        """Whether the profile **content** essentials are filled (photo/bio/pronouns/Discord).

        Reads :attr:`profile_completeness` but uses its ``essentials_complete`` — which
        excludes the directory-listing opt-out — so a member who hides from the directory
        is still onboardable. Cached so the three onboarding reads don't recompute it.
        """
        return self.profile_completeness.essentials_complete

    @cached_property
    def _has_joined_guild(self) -> bool:
        """Whether the member has officially joined at least one guild. Cached (one query)."""
        return self.joined_guilds.exists()

    @property
    def _has_voting_preference(self) -> bool:
        """Whether the member has set a guild funding vote (a :class:`VotePreference` row)."""
        return VotePreference.objects.filter(member=self).exists()

    @property
    def is_onboarded(self) -> bool:
        """Whether the member has finished the required first-week setup.

        True once their profile **content essentials** are filled AND they've joined at
        least one guild. Voting is a recommended, **optional** step and does NOT affect
        this. Uses ``_profile_essentials_done`` (not ``profile_completeness.complete``) so
        the directory-listing opt-out can never make a member permanently un-onboardable.
        """
        return self._profile_essentials_done and self._has_joined_guild

    @property
    def onboarding(self) -> OnboardingChecklist:
        """The three-step home "Get started" checklist for this member.

        Each step is a derived status row linking to the page that completes it — the
        profile editor, the My Guilds settings tab, and the voting page. ``required_done`` /
        ``required_total`` count only profile + guilds (voting is optional); ``complete``
        equals :attr:`is_onboarded`.
        """
        from django.urls import reverse

        profile = self.profile_completeness
        profile_done = profile.essentials_complete
        steps = [
            OnboardingStep(
                key="profile",
                label="Set up your profile",
                done=profile_done,
                url=f"{reverse('hub_user_settings')}?tab=profile",
                optional=False,
                hint="" if profile_done else f"{profile.percent}% complete",
            ),
            OnboardingStep(
                key="guilds",
                label="Join your guilds",
                done=self._has_joined_guild,
                url=f"{reverse('hub_user_settings')}?tab=guilds",
                optional=False,
                hint="",
            ),
            OnboardingStep(
                key="discord",
                label="Connect your Discord",
                # Keyed specifically off ``discord_is_linked`` (a linked OAuth account), NOT the
                # free-text ``discord_handle`` — a typed handle does not satisfy this step.
                done=self.discord_is_linked,
                url=reverse("hub_discord_connect"),
                optional=True,
                hint="" if self.discord_is_linked else "We'll set up your guilds instantly",
            ),
            OnboardingStep(
                key="voting",
                label="Set a voting preference",
                done=self._has_voting_preference,
                url=reverse("hub_guild_voting"),
                optional=True,
                hint="",
            ),
        ]
        required = [step for step in steps if not step.optional]
        required_done = sum(1 for step in required if step.done)
        return OnboardingChecklist(
            steps=steps,
            required_done=required_done,
            required_total=len(required),
            complete=required_done == len(required),
        )

    @property
    def show_onboarding(self) -> bool:
        """Whether to show the home "Get started" card: not onboarded and not dismissed."""
        return self.onboarding_dismissed_at is None and not self.is_onboarded

    def dismiss_onboarding(self) -> None:
        """Hide the home "Get started" checklist card (sticky). Does not affect is_onboarded."""
        self.onboarding_dismissed_at = timezone.now()
        self.save(update_fields=["onboarding_dismissed_at"])

    def link_discord(self, discord_user_id: str, handle: str = "") -> None:
        """Record a verified Discord account id for DM notifications.

        Stores the snowflake and stamps :attr:`discord_linked_at`. Called by the OAuth
        linking service (:func:`core.events.discord_oauth.link_member_from_code`) after
        the member authorizes the FOG bot.

        Args:
            discord_user_id: The member's numeric Discord id (snowflake).
            handle: The member's Discord username (or global name). Used to fill
                :attr:`discord_handle` **only when it is currently blank** — a handle the
                member typed themselves is never overwritten.
        """
        self.discord_user_id = discord_user_id.strip()
        self.discord_linked_at = timezone.now()
        update_fields = ["discord_user_id", "discord_linked_at"]
        if handle and not self.discord_handle:
            self.discord_handle = handle.strip()
            update_fields.append("discord_handle")
        self.save(update_fields=update_fields)

    def unlink_discord(self) -> None:
        """Clear the linked Discord account (the member opts out of DMs entirely)."""
        self.discord_user_id = ""
        self.discord_linked_at = None
        self.save(update_fields=["discord_user_id", "discord_linked_at"])

    def is_public(self, field_name: str) -> bool:
        """Return True if a directory-toggleable field should appear on this member's card.

        Defaults to True (public) when the key is missing — existing members aren't
        accidentally hidden after this feature ships.
        """
        return bool(self.directory_visibility.get(field_name, True))

    @property
    def approved_skills(self) -> models.QuerySet[MemberSkill]:
        """This member's skills whose vocabulary entry is approved, ready for display."""
        return self.skills.filter(skill__status=Skill.Status.APPROVED).select_related("skill__category")

    @property
    def directory_contacts(self) -> models.QuerySet[MemberContact]:
        """Contacts this member has flagged to show on their member-directory card."""
        return self.contacts.filter(show_in_directory=True)

    @property
    def instructor_page_contacts(self) -> models.QuerySet[MemberContact]:
        """Contacts this member has flagged to show on their public instructor page."""
        return self.contacts.filter(show_on_instructor_page=True)

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
    def email_gap_label(self) -> str:
        """Human reason this member is emailless, or "" when they have an email.

        Requires the ``email_gap`` annotation from
        :meth:`MemberQuerySet.with_email_status`; it is only rendered in the
        missing-email view, so accessing it on an un-annotated instance fails
        loudly (``AttributeError``) rather than hiding a bug.
        """
        return self.EmailGap(self.email_gap).label if self.email_gap else ""

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
        """True when this member may edit the given guild.

        Editors are admins, officers, the guild's lead (the ``Guild.guild_lead`` FK),
        and anyone holding a staff role on the guild — every staff role carries the
        same edit authority as the lead.
        """
        return self.is_fog_admin or self.is_guild_officer or guild.guild_lead_id == self.pk or guild.is_staffed_by(self)

    def can_manage_orientations(self, guild: Guild) -> bool:
        """True when this member may run the guild's orientations.

        Anyone who can edit the guild can run its orientations — that now includes
        every staff member (orienters are a staff role). Role-based — use
        ``membership.permissions.can_manage_orientations`` in views to honor
        ``view_as`` preview mode.
        """
        return self.can_edit_guild(guild)

    def can_edit_class(self, offering: ClassOffering) -> bool:
        """True when this member may edit the class offering.

        Editors are admins/officers, the lead or any staff member of the class's
        category's guild, or the class's own instructor. Role-based — use
        ``membership.permissions.can_edit_class`` in views to honor ``view_as``
        preview mode.
        """
        if self.is_fog_admin or self.is_guild_officer:
            return True
        guild = offering.category.guild
        if guild is not None and (guild.guild_lead_id == self.pk or guild.is_staffed_by(self)):
            return True
        return offering.instructor_id == self.pk

    @property
    def is_guild_lead(self) -> bool:
        """True when this member leads at least one guild."""
        return Guild.objects.filter(guild_lead=self).exists()

    @property
    def is_guild_staff(self) -> bool:
        """True when this member holds a staff role on at least one guild.

        Staff (co-leads, secretaries, treasurers, orienters) all carry full
        guild-lead permissions on the guilds where they hold a role.
        """
        return self.guild_staff_roles.exists()

    @property
    def staffed_guilds(self) -> models.QuerySet[Guild]:
        """Guilds this member leads or holds any staff role on (i.e. has lead authority)."""
        return Guild.objects.filter(models.Q(guild_lead=self) | models.Q(staff_memberships__member=self)).distinct()

    @property
    def joined_guilds(self) -> models.QuerySet[Guild]:
        """Guilds this member has explicitly joined (via :class:`GuildMembership`), name-ordered."""
        return Guild.objects.filter(memberships__member=self).order_by("name")

    @property
    def is_instructor(self) -> bool:
        """True when this member has a public instructor profile (instructor_slug is set)."""
        return bool(self.instructor_slug)

    @property
    def can_create_classes(self) -> bool:
        """True when this member may enter the teaching portal and create classes.

        The single source of truth for the teaching gate (Spec D §4):
        ``teaching_member_required`` reads only this. Deliberately independent of
        the Instructor *role* (``instructor_slug`` — the public page) and of tour
        state (deleting a ``TourState`` row must never revoke a permission).
        """
        return self.instructor_oriented_at is not None

    def complete_instructor_orientation(self) -> None:
        """Member-facing completion of the instructor orientation — unlocks the teaching portal.

        Idempotent: an already-unlocked member returns without side effects, so a
        double-submit never logs a second activity row. Writes no ``TourState``
        (``instructor_oriented_at`` is the entire record — Spec D §4).

        Raises:
            ValueError: If the member is not ACTIVE — orientation can't fix an
                inactive account (those keep their 403 at the portal door).
        """
        from core.models import SiteActivity

        if self.status != self.Status.ACTIVE:
            raise ValueError(f"Member {self.pk} is not active — cannot complete the instructor orientation")
        if self.instructor_oriented_at is not None:
            return
        self.instructor_oriented_at = timezone.now()
        self.save(update_fields=["instructor_oriented_at"])
        SiteActivity.log(SiteActivity.Kind.INSTRUCTOR_ORIENTED, actor=self.user, target=self)

    def grant_teaching(self, *, granted_by: "Member | None") -> None:
        """Admin override: unlock the teaching portal for this member.

        Idempotent — granting an already-unlocked member is a no-op (no duplicate
        activity row, original timestamp kept). ``granted_by=None`` covers a
        superuser acting without a linked Member (emergency access) — the
        activity row is then system-attributed.
        """
        from core.models import SiteActivity

        if self.instructor_oriented_at is not None:
            return
        self.instructor_oriented_at = timezone.now()
        self.save(update_fields=["instructor_oriented_at"])
        SiteActivity.log(
            SiteActivity.Kind.TEACHING_GRANTED,
            actor=granted_by.user if granted_by is not None else None,
            target=self,
        )

    def revoke_teaching(self, *, revoked_by: "Member | None") -> None:
        """Admin override: lock the teaching portal for this member.

        Existing classes are untouched — drafts, pending, and published offerings
        keep their status, registrations, and emails; the member simply can't enter
        the teach portal or create new classes until re-unlocked. Two roads back:
        an admin grant, or completing the orientation again. Idempotent — revoking
        an already-locked member is a no-op (no log).
        """
        from core.models import SiteActivity

        if self.instructor_oriented_at is None:
            return
        self.instructor_oriented_at = None
        self.save(update_fields=["instructor_oriented_at"])
        SiteActivity.log(
            SiteActivity.Kind.TEACHING_REVOKED,
            actor=revoked_by.user if revoked_by is not None else None,
            target=self,
        )

    def is_oriented_for(self, guild: Guild) -> bool:
        """True when the member has a completed orientation for this guild."""
        return self.orientation_bookings.filter(guild=guild, is_completed=True).exists()

    def active_orientation_for(self, guild: Guild) -> OrientationBooking | None:
        """The member's live (requested or confirmed) orientation booking for this guild, if any."""
        return self.orientation_bookings.filter(
            guild=guild,
            status__in=[OrientationBooking.Status.REQUESTED, OrientationBooking.Status.CONFIRMED],
        ).first()

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

        unlocked_via_promotion = False
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
                # First-time promotion implies the teaching unlock (Spec D §5) — an
                # explicit "make them an Instructor" must not strand them at the
                # orientation redirect. Scoped to this slug-minting branch ON
                # PURPOSE: admin_member_edit re-applies the role on every save and
                # the form pre-fills "Instructor" for any slug holder, so an
                # unscoped hook would silently re-grant a revoked instructor's
                # access on any routine member edit.
                if self.instructor_oriented_at is None:
                    self.instructor_oriented_at = timezone.now()
                    unlocked_via_promotion = True
        elif picked_role == self.ADMIN_ROLE_GUEST:
            self.fog_role = self.FogRole.MEMBER
            self.status = self.Status.FORMER
        else:
            self.fog_role = picked_role
        self.save()
        if unlocked_via_promotion:
            from core.models import SiteActivity

            # System-attributed row (actor=None) — the method has no actor parameter.
            SiteActivity.log(
                SiteActivity.Kind.TEACHING_GRANTED,
                actor=None,
                target=self,
                payload={"via": "instructor_promotion"},
            )

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

    def send_login_invite(self) -> None:
        """Email this member a first-time sign-in link (one intentional email).

        Distinct from :meth:`core.models.Invite.create_and_send`, which rejects
        people who are already members — every member trips that guard now. This is
        the path for an existing member who has never signed in.

        It first idempotently provisions a User + verified primary email (so
        login-by-code works), then emits the branded ``member.login_invite`` email
        with a link to the login-code page, the member's email pre-filled. Re-sends
        always go out (a fresh idempotency ``period`` per send, like the invite).

        Raises:
            ValueError: if the member has no email on file (nothing to send to).
        """
        from urllib.parse import urlencode

        from django.contrib.sites.models import Site

        from core.events.emit import emit
        from membership.services.provisioning import provision_user_for_member

        user = provision_user_for_member(self)
        if user is None:
            raise ValueError(f"Cannot send a login invite to member {self.pk}: no email on file.")

        email = self.primary_email
        current_site = Site.objects.get_current()
        protocol = "https" if not settings.DEBUG else "http"
        query = urlencode({"email": email})
        login_url = f"{protocol}://{current_site.domain}/accounts/login/code/?{query}"

        emit(
            "member.login_invite",
            target=self,
            context={"user": user, "member_name": self.display_name, "login_url": login_url},
            period=f"login_invite:{timezone.now():%Y%m%d%H%M%S%f}",
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Member records are otherwise managed in Airtable; this override only
        # cleans up the orphaned profile_photo file when the user replaces it.
        delete_orphan_on_replace(self, "profile_photo")
        super().save(*args, **kwargs)


class MemberContact(models.Model):
    """A labeled contact method on a Member, with per-surface placement.

    One list per member. Absorbs the former fixed ``other_contact_info`` /
    ``instructor_website`` / ``instructor_social_handle`` fields — a website is just a
    contact flagged "show on instructor page." ``phone`` and ``discord_handle`` stay
    first-class on :class:`Member`; they are not contacts.
    """

    # Naive email detection: exactly one ``@`` between non-space runs, with a dotted domain.
    _EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="contacts",
        help_text="The member this contact belongs to.",
    )
    label = models.CharField(max_length=100, help_text="What to call this contact, e.g. 'Website' or 'Instagram'.")
    value = models.CharField(max_length=255, help_text="The contact itself — an email, URL, handle, or free text.")
    show_in_directory = models.BooleanField(default=True, help_text="Show this contact on the member's directory card.")
    show_on_instructor_page = models.BooleanField(
        default=False, help_text="Show this contact on the member's public instructor page."
    )
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.label}: {self.value} ({self.member.display_name})"

    @property
    def as_link(self) -> SafeString:
        """Render :attr:`value` as a hyperlink when it clearly is one, else escaped text.

        An email address becomes a ``mailto:`` link; an ``http(s)://`` or ``www.`` URL
        becomes an external link (``www.`` is promoted to ``https://``); anything else
        — a social handle, a phone number, free text — renders as escaped plain text.
        """
        value = self.value.strip()
        if self._EMAIL_RE.match(value):
            return format_html('<a href="mailto:{}">{}</a>', value, value)
        lowered = value.lower()
        if lowered.startswith(("http://", "https://")):
            return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', value, value)
        if lowered.startswith("www."):
            return format_html('<a href="https://{}" target="_blank" rel="noopener">{}</a>', value, value)
        return format_html("{}", value)


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


@dataclass(frozen=True)
class VirtualLink:
    """A computed, non-stored link rendered alongside a guild's real ``GuildLink`` rows.

    Duck-types the ``.label`` / ``.url`` a template reads off a real ``GuildLink``, so the
    shared guild-detail links loop renders it identically. Used for the always-present
    "[Guild] Classes" link (see :meth:`Guild.classes_link`) — it lives only in the render
    context, so it needs no migration, reflects a rename, and can't be edited or deleted.
    """

    label: str
    url: str


class GuildManager(models.Manager["Guild"]):
    """Default manager that hides soft-deleted guilds from every query."""

    def get_queryset(self) -> models.QuerySet[Guild]:
        return super().get_queryset().filter(deleted_at__isnull=True)

    def directory(self) -> models.QuerySet[Guild]:
        """Active guilds for the directory: featured first, then alphabetical.

        Every active guild is public and appears everywhere; soft-deleting or
        deactivating a guild (``is_active=False``) is the only way to hide it.
        """
        return self.filter(is_active=True).order_by("-is_featured", "name")

    def for_discord_channel(self, channel_id: str) -> Guild | None:
        """The active guild whose Discord channel is ``channel_id``, or ``None`` if unmapped.

        Used by the slash-command platform to auto-detect which guild a member means
        when they run a guild command *in* that guild's channel. ``.first()`` (not
        ``.get()``) so an accidental duplicate mapping degrades to the disambiguation
        fallback instead of raising; a blank ``channel_id`` short-circuits to ``None``.
        """
        if not channel_id:
            return None
        return self.filter(is_active=True, discord_channel_id=channel_id).first()

    def matching(self, query: str) -> models.QuerySet[Guild]:
        """Active guilds whose name or slug contains ``query`` (case-insensitive).

        Used by the Discord slash commands to resolve a member-typed ``guild`` option
        leniently to a guild. A blank ``query`` matches nothing (the caller then shows
        the "which guild?" reply).
        """
        query = (query or "").strip()
        if not query:
            return self.none()
        return self.filter(is_active=True).filter(models.Q(name__icontains=query) | models.Q(slug__icontains=query))


class Guild(HeroCropMixin, models.Model):
    # Queryset annotation (set by GuildAdmin.get_queryset)
    sublet_count: int

    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(
        max_length=120,
        unique=True,
        blank=True,
        help_text="URL slug for the guild's public page, auto-generated from the name (stable across renames).",
    )
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
    essential_rules = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Short essential/safety rules shown on your printable flyer. "
            "Keep it brief — your full About is too long to print."
        ),
    )
    banner_image = models.ImageField(
        upload_to="guilds/banners/",
        blank=True,
        validators=[validate_image_size],
        help_text="Banner image shown at the top of the guild page.",
    )

    def get_hero_image_field_name(self) -> str:
        return "banner_image"

    calendar_url = models.URLField(
        blank=True,
        default="",
        help_text="Public iCal URL for this guild's Google Calendar (File → Share → Get shareable iCal link).",
    )
    discord_role_ids = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Discord role id(s) assigned AND removed together when a member joins or leaves this "
            "guild in the app (outbound sync). A list of role-id strings — most guilds have one; a "
            "collapsed guild (e.g. Glass) keeps two roles in lockstep. Empty disables outbound role "
            "sync for this guild. Asymmetry to note: outbound assigns EVERY id listed here, while "
            "inbound (a member's Discord reaction) accepts ANY emoji mapped to the guild."
        ),
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
    youtube_url = models.URLField(blank=True, default="", help_text="Optional YouTube video shown on the guild page.")
    meeting_schedule = models.TextField(
        blank=True, default="", help_text="When/where the guild meets, e.g. 'Tuesdays 6pm, Studio B'."
    )

    class MeetingCadence(models.TextChoices):
        NONE = "none", "No regular meeting"
        MONTHLY = "monthly", "Monthly"
        EVERY_2_MONTHS = "every_2_months", "Every 2 months"
        EVERY_3_MONTHS = "every_3_months", "Every 3 months"

    meeting_cadence = models.CharField(
        max_length=20,
        choices=MeetingCadence.choices,
        default=MeetingCadence.NONE,
        help_text="How often the guild meets — drives the auto-computed next-meeting date.",
    )
    meeting_weekday = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Day of week the guild meets (0=Mon … 6=Sun)."
    )
    meeting_week_of_month = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="For monthly meetings: which week (1–4, or 5 for the last) of the month."
    )
    meeting_time = models.TimeField(null=True, blank=True, help_text="Start time of the meeting.")
    meeting_location = models.CharField(
        max_length=200, blank=True, default="", help_text="Where the meeting happens, e.g. 'Studio B'."
    )
    meeting_next_override = models.DateField(
        null=True, blank=True, help_text="A specific one-off next-meeting date that overrides the cadence."
    )
    meeting_is_tba = models.BooleanField(
        default=False, help_text="Force the next meeting to show as TBA even when a cadence is set."
    )
    contact_email = models.EmailField(
        blank=True, default="", help_text="Optional guild contact email shown on the page."
    )
    discord_url = models.URLField(
        blank=True, default="", help_text="Link to the guild's Discord channel, shown as a button on the page."
    )
    discord_webhook_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text=(
            "A Discord webhook for THIS guild's own channel. Guild announcements also post here. "
            "Blank = nothing posts to your channel."
        ),
    )
    discord_post_enabled = models.BooleanField(
        default=True,
        help_text=(
            "Also post this guild's announcements to your own Discord channel "
            "(in addition to the makerspace-wide channel)."
        ),
    )
    discord_channel_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text=(
            "This guild's Discord channel id (right-click the channel → Copy Channel ID with "
            "Developer Mode on). When a member runs a guild slash command in this channel, we know "
            "which guild they mean. This is NOT the webhook URL above — leave blank if you don't use "
            "channel auto-detection."
        ),
    )
    discord_welcome_message = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Shown to the member in Discord (their private confirmation) and posted in your guild's "
            "Discord channel when someone joins via /join-guild. This is separate from your guild "
            "Welcome email. Write it in your voice (a lead's welcome). Blank uses a generic welcome."
        ),
    )
    website_url = models.URLField(
        blank=True, default="", help_text="Link to the guild's external website, shown as a button on the page."
    )
    show_members = models.BooleanField(
        default=False, help_text="Show the opt-in members roster on the public guild page."
    )
    is_featured = models.BooleanField(
        default=False, help_text="Pin this guild to the top of the public guilds directory."
    )
    featured_class = models.ForeignKey(
        "classes.ClassOffering",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="A class to spotlight at the top of the guild page.",
    )
    faq_label = models.CharField(
        max_length=50,
        default="FAQ",
        help_text="Heading for this guild's FAQ / info section on the guild page — e.g. 'Ceramics Info'.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the guild is soft-deleted; hidden from voting, the directory, and every member-facing page.",
    )
    leases = GenericRelation(
        "Lease",
        content_type_field="content_type",
        object_id_field="object_id",
    )

    objects = GuildManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["name"]
        verbose_name = "Guild"
        verbose_name_plural = "Guilds"

    def __str__(self) -> str:
        return self.name

    @property
    def logo_prefix(self) -> str | None:
        """Map the guild name to its SVG logo prefix in static/img/guild_logos/."""
        from membership.logos import logo_prefix_for

        return logo_prefix_for(self.name)

    def classes_link(self, *, guilds_surface: bool = False) -> VirtualLink:
        """The always-present "[Guild] Classes" link for this guild's detail page.

        Virtual (never a stored :class:`GuildLink`): computed each render so its label
        reflects a rename, it needs no migration, and it can't be edited or deleted — it
        exists only in the detail view's ``links`` context, never in the edit Links formset.
        The URL points at the public class catalog pre-filtered to this guild via
        ``?guild=<slug>``.

        Args:
            guilds_surface: True when rendering on the guilds host, where the classes app
                lives on the book host. The path is then prefixed with
                ``settings.BOOK_BASE_URL`` (mirroring the guild page's existing cross-surface
                class links); on the members surface it stays root-relative.

        Returns:
            A :class:`VirtualLink` with the recomputed label and surface-correct URL.
        """
        from django.urls import reverse

        path = f"{reverse('classes:public_list')}?guild={self.slug}"
        url = f"{settings.BOOK_BASE_URL}{path}" if guilds_surface else path
        return VirtualLink(label=f"{self.name} Classes", url=url)

    @property
    def vanity_url(self) -> str:
        """Absolute, human-typable share URL, e.g. https://pastlives.app/g/ceramics/.

        Lives on the member host and 301-redirects to the public guest guild page. It is
        the single source of truth for what the QR encodes and the flyer prints.
        """
        from django.urls import reverse

        return f"{settings.MEMBER_BASE_URL}{reverse('guild_vanity', args=[self.slug])}"

    def qr_svg(self) -> str:
        """Inline, CSS-scalable SVG of this guild's vanity-URL QR (crisp at any print size)."""
        from membership.qr import qr_svg as render_qr

        return render_qr(self.vanity_url)

    def qr_png_bytes(self) -> bytes:
        """PNG bytes of the same QR (segno's native writer — no Pillow)."""
        from membership.qr import qr_png_bytes as render_png

        return render_png(self.vanity_url)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = self._unique_slug()
        delete_orphan_on_replace(self, "banner_image")
        super().save(*args, **kwargs)

    def soft_delete(self) -> None:
        """Hide the guild everywhere without destroying its data or breaking its relations.

        Sets ``deleted_at`` so the default manager filters it out of voting, the directory,
        and every member-facing listing. Memberships, leases, classes, and orientation
        history are preserved, and an admin can restore it by clearing ``deleted_at``.
        """
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])

    def _unique_slug(self) -> str:
        """A URL slug derived from the guild name, suffixed (``-2``, ``-3``…) to stay unique."""
        from django.utils.text import slugify

        base = slugify(self.name) or "guild"
        slug = base
        n = 2
        # Check against *all* guilds, including soft-deleted ones, since the DB unique
        # constraint spans every row — the default manager would hide a deleted collision.
        while Guild.all_objects.exclude(pk=self.pk).filter(slug=slug).exists():
            slug = f"{base}-{n}"
            n += 1
        return slug

    def add_gallery_images(self, files: list[Any]) -> None:
        """Create GuildImage rows from uploaded files, appending after existing ones."""
        start = self.gallery_images.count()
        for i, img_file in enumerate(files):
            GuildImage.objects.create(guild=self, image=img_file, sort_order=start + i)

    def roster_members(self) -> models.QuerySet[Member]:
        """Active joined members, filtered by directory privacy (same rule as member_directory)."""
        return Member.objects.directory_visible().filter(guild_memberships__guild=self)

    @property
    def next_meeting_at(self) -> date_type | None:
        """The next guild-meeting date from the cadence config + override, or None (TBA)."""
        return _compute_next_meeting(
            timezone.localdate(),
            cadence=self.meeting_cadence,
            weekday=self.meeting_weekday,
            week_of_month=self.meeting_week_of_month,
            override=self.meeting_next_override,
            is_tba=self.meeting_is_tba,
        )

    def studio_hours(self) -> list[CommunityEvent]:
        """This guild's standing STUDIO_HOURS rows, ordered by weekday-of-anchor then time.

        Iterates the prefetched ``events`` cache (the guild page prefetches it), so rendering
        the Studio Hours card and its sibling helpers adds no extra query per guild.
        """
        rows = [
            event
            for event in self.events.all()
            if event.event_type == CommunityEvent.EventType.STUDIO_HOURS
            and event.moderation_state == CommunityEvent.ModerationState.PUBLISHED
        ]
        return sorted(
            rows,
            key=lambda event: (
                timezone.localtime(event.starts_at).weekday(),
                timezone.localtime(event.starts_at).time(),
            ),
        )

    def studio_hours_display(self) -> list[dict[str, str]]:
        """Render dicts (``weekday_label`` / ``time_range`` / ``location`` / ``note``) for the
        guild-page Studio Hours card, one per standing block. Empty list ⇒ the card shows its
        "No studio hours set yet." empty state."""
        blocks: list[dict[str, str]] = []
        for row in self.studio_hours():
            local_start = timezone.localtime(row.starts_at)
            local_end = timezone.localtime(row.ends_at)
            blocks.append(
                {
                    "weekday_label": f"{local_start.strftime('%A')}s",
                    "time_range": _format_time_range(local_start, local_end),
                    "location": row.location,
                    "note": row.description,
                }
            )
        return blocks

    def next_meeting_occurrence(self) -> NextMeeting | None:
        """The soonest upcoming meeting, across event-based GUILD_MEETING occurrences and the
        legacy cadence — so a weekly meeting entered as an event surfaces in the "Next Meeting"
        card without duplicating recurrence logic.

        Returns a :class:`NextMeeting` (``when`` datetime + ``location`` + ``has_time``) so both
        the aside card and the stat chip render coherently: an event carries its own time and
        location; the legacy cadence carries ``meeting_time``/``meeting_location`` only when set.
        ``None`` ⇒ TBA. Reads the prefetched ``events`` cache (no per-guild query).
        """
        now = timezone.now()
        today = timezone.localdate()
        horizon = today + timedelta(days=370)
        candidates: list[NextMeeting] = []
        for event in self.events.all():
            if (
                event.event_type != CommunityEvent.EventType.GUILD_MEETING
                or event.moderation_state != CommunityEvent.ModerationState.PUBLISHED
            ):
                continue
            for occurrence in event.occurrences_in(today, horizon):
                if occurrence >= now:
                    candidates.append(NextMeeting(when=occurrence, location=event.location, has_time=True))
                    break
        legacy_date = self.next_meeting_at
        if legacy_date is not None:
            aware = timezone.make_aware(datetime_type.combine(legacy_date, self.meeting_time or time_type()))
            candidates.append(
                NextMeeting(when=aware, location=self.meeting_location, has_time=self.meeting_time is not None)
            )
        if not candidates:
            return None
        return min(candidates, key=lambda candidate: candidate.when)

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

    def is_staffed_by(self, member: Member) -> bool:
        """True when ``member`` holds any staff role on this guild (separate from the lead FK)."""
        return self.staff_memberships.filter(member=member).exists()

    def staff_by_role(self) -> list[tuple[str, list[GuildStaffMembership]]]:
        """Staff memberships grouped by display title, for display.

        Preset roles come first in role-declaration order, then any custom titles
        alphabetically (case-insensitive), so the list is stable. Returns
        ``(title_label, [memberships])`` pairs, omitting titles that have no members.
        Each membership carries its ``member`` and ``pk`` for display and removal.
        """
        by_title: dict[str, list[GuildStaffMembership]] = {}
        for staff in self.staff_memberships.select_related("member"):
            by_title.setdefault(staff.display_title, []).append(staff)
        grouped: list[tuple[str, list[GuildStaffMembership]]] = []
        # Preset roles first, in their declaration order.
        for role in GuildStaffMembership.Role:
            rows = by_title.pop(role.label, None)
            if rows:
                rows.sort(key=lambda s: (s.member.full_legal_name or "").lower())
                grouped.append((role.label, rows))
        # Then any custom titles, alphabetically.
        for title in sorted(by_title, key=str.lower):
            rows = by_title[title]
            rows.sort(key=lambda s: (s.member.full_legal_name or "").lower())
            grouped.append((title, rows))
        return grouped

    @staticmethod
    def _staff_title_sort_key(staff: GuildStaffMembership) -> tuple[int, str]:
        """Order a staff row's title: presets first (role-declaration order), then custom titles alphabetically.

        Shared by the title-ordering in :meth:`staff_by_member` so a person's badges read the same way the
        :meth:`staff_by_role` headings do.
        """
        role_order = {role.value: index for index, role in enumerate(GuildStaffMembership.Role)}
        if staff.role:
            return (role_order[staff.role], "")
        return (len(role_order), staff.custom_title.lower())

    def staff_by_member(self) -> list[tuple[Member, list[GuildStaffMembership]]]:
        """Each staff member once, with all their staff-title rows, for badge display.

        Members are sorted by name (case-insensitive, matching :meth:`staff_by_role`). Within a member,
        rows are ordered presets-first (role-declaration order) then custom titles alphabetically. Built
        from a single ``select_related`` query, so iterating the result hits no extra queries.
        """
        members: dict[int, Member] = {}
        rows_by_member: dict[int, list[GuildStaffMembership]] = {}
        for staff in self.staff_memberships.select_related("member"):
            members[staff.member_id] = staff.member
            rows_by_member.setdefault(staff.member_id, []).append(staff)
        ordered_ids = sorted(rows_by_member, key=lambda mid: (members[mid].full_legal_name or "").lower())
        grouped: list[tuple[Member, list[GuildStaffMembership]]] = []
        for member_id in ordered_ids:
            rows = rows_by_member[member_id]
            rows.sort(key=self._staff_title_sort_key)
            grouped.append((members[member_id], rows))
        return grouped

    def leadership_members(self) -> list[Member]:
        """The guild lead plus every staff member, de-duplicated — all who hold lead authority.

        Fans out the emails and notifications that previously went to the lead alone.
        """
        members: list[Member] = []
        seen: set[int] = set()
        if self.guild_lead_id is not None and self.guild_lead is not None:
            members.append(self.guild_lead)
            seen.add(self.guild_lead_id)
        for staff in self.staff_memberships.select_related("member"):
            if staff.member_id not in seen:
                members.append(staff.member)
                seen.add(staff.member_id)
        return members

    def announcement_recipients(self) -> list[tuple["User", str]]:
        """The exact ``(User, reason)`` list a guild announcement email fans out to.

        Delegates to the real send resolver so the lead-facing count/list can never
        drift from delivery. NOT directory-privacy filtered (guild members hear from
        their own guild regardless of directory visibility) and NOT last_login-gated.
        """
        from core.events import resolvers
        from core.events.registry import Recipients

        return resolvers.resolve(Recipients.GUILD_MEMBERS, {"guild": self})

    def mailing_list_emails_deduped(self, member_emails: set[str]) -> list[str]:
        """The guild's custom mailing-list addresses, normalized and de-duped against members.

        Returns each :class:`GuildMailingListEmail`'s address ``.strip().lower()``-normalized
        and sorted, with any address that already belongs to a member removed. Used by BOTH
        the lead-facing display and the delivery fan-out, so the "Your Mailing List" section
        and the actual send can never drift.

        Args:
            member_emails: The already lower-cased member ``user.email`` addresses to exclude —
                the same collision key the send fan-out uses (see
                :meth:`GuildAnnouncement.notify_members`).

        Returns:
            The sorted, de-duplicated custom addresses that are not also members.
        """
        custom = {(row.email or "").strip().lower() for row in self.mailing_list_emails.all()}
        custom.discard("")
        return sorted(custom - member_emails)


class GuildStaffMembership(models.Model):
    """A member's leadership role on a guild, beyond the single ``Guild.guild_lead`` FK.

    Every staff role grants the **same** authority as the guild lead: staff may edit
    the guild page, manage and approve its classes, run its orientations, and receive
    every email the lead does (class-review and orientation requests). The role is a
    label for display and organization — it is not a permission tier. The primary
    ``Guild.guild_lead`` stays an admin-assigned FK and is never managed here.
    """

    class Role(models.TextChoices):
        CO_LEAD = "co_lead", "Guild Lead"
        SECRETARY = "secretary", "Secretary"
        TREASURER = "treasurer", "Treasurer"
        ORIENTER = "orienter", "Orientator"

    guild = models.ForeignKey(
        Guild,
        on_delete=models.CASCADE,
        related_name="staff_memberships",
        help_text="The guild this staff role applies to.",
    )
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="guild_staff_roles",
        help_text="The member holding the staff role.",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        blank=True,
        default="",
        help_text="A preset officer role. Leave blank when this entry uses a custom title instead.",
    )
    custom_title = models.CharField(
        max_length=60,
        blank=True,
        default="",
        help_text="A free-text officer title (e.g. 'Studio Technician') used instead of a preset role.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When this role was assigned.")

    class Meta:
        ordering = ["role", "member__full_legal_name"]
        constraints = [
            # Exactly one of role / custom_title is set — fail loudly on a blank-or-both row.
            models.CheckConstraint(
                name="ck_guildstaff_role_xor_custom_title",
                condition=(
                    (models.Q(role="") & ~models.Q(custom_title="")) | (~models.Q(role="") & models.Q(custom_title=""))
                ),
            ),
            # No duplicate preset role per member per guild.
            models.UniqueConstraint(
                fields=["guild", "member", "role"],
                condition=~models.Q(role=""),
                name="uq_guildstaff_member_role",
            ),
            # No duplicate custom title per member per guild.
            models.UniqueConstraint(
                fields=["guild", "member", "custom_title"],
                condition=~models.Q(custom_title=""),
                name="uq_guildstaff_member_custom_title",
            ),
        ]

    @property
    def display_title(self) -> str:
        """The label to show for this staff entry — the custom title if set, else the preset role's label."""
        return self.custom_title or self.get_role_display()

    def __str__(self) -> str:
        return f"{self.member.display_name} — {self.display_title} of {self.guild.name}"


class GuildImage(models.Model):
    """Gallery image for a guild page. Up to 10, enforced in the form."""

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="gallery_images", help_text="Parent guild.")
    image = models.ImageField(upload_to="guilds/images/", validators=[validate_image_size], help_text="Gallery photo.")
    alt_text = models.CharField(max_length=255, blank=True, help_text="Accessibility description.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "created_at"]

    def __str__(self) -> str:
        return f"Image #{self.pk} for {self.guild.name}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        from django.conf import settings

        delete_orphan_on_replace(self, "image")
        normalize_field_if_uploaded(self, "image", settings.IMAGE_MAX_LONG_EDGE_GALLERY)
        super().save(*args, **kwargs)


class GuildFAQItem(models.Model):
    """A question/answer pair shown in the guild page FAQ section.

    An answer may also carry a YouTube embed and/or one attached document — either an
    uploaded file or a link to an external doc (at most one of the two).
    """

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="faq_items", help_text="Parent guild.")
    question = models.CharField(max_length=500, help_text="The question.")
    answer = models.TextField(help_text="The answer.")
    video_url = models.URLField(
        blank=True,
        default="",
        help_text="Optional YouTube link shown with this answer (watch, youtu.be, embed, or shorts URL).",
    )
    document = models.FileField(
        upload_to="guilds/faq/",
        blank=True,
        validators=[validate_document],
        help_text="Optional document (PDF, Word, slides, spreadsheet…) shown with this answer. "
        "Leave blank to link an external doc instead.",
    )
    document_url = models.URLField(
        blank=True,
        default="",
        help_text="Optional link to an external doc for this answer. Leave blank if you uploaded a file.",
    )
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")

    class Meta:
        ordering = ["sort_order"]
        constraints = [
            # A document is optional, but it can't be BOTH an upload and a link.
            models.CheckConstraint(
                condition=Q(document="") | Q(document_url=""),
                name="ck_guildfaqitem_doc_not_both",
            ),
        ]

    def __str__(self) -> str:
        return self.question

    @property
    def has_document(self) -> bool:
        return bool(self.document) or bool(self.document_url)

    @property
    def document_display_name(self) -> str:
        """The uploaded file's base name, else the link URL, else ``""``."""
        if self.document and self.document.name:
            return self.document.name.rsplit("/", 1)[-1]
        return self.document_url

    @property
    def document_href(self) -> str:
        """Where the document link points — the uploaded file's URL or the external link."""
        return self.document.url if self.document else self.document_url

    def save(self, *args: Any, **kwargs: Any) -> None:
        delete_orphan_on_replace(self, "document")
        super().save(*args, **kwargs)


class GuildLink(models.Model):
    """A named external link shown in the guild page sidebar."""

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="links", help_text="Parent guild.")
    label = models.CharField(max_length=100, help_text="Display text, e.g. 'Discord'.")
    url = models.URLField(help_text="Destination URL.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")

    class Meta:
        ordering = ["sort_order"]

    def __str__(self) -> str:
        return f"{self.label} ({self.guild.name})"


@dataclass(frozen=True)
class MailingListImportResult:
    """The outcome of a :meth:`GuildMailingListEmail.import_from_text` run.

    Carries the per-outcome counts so the view can flash a friendly summary without
    re-reading the database.
    """

    imported: int
    skipped_existing: int
    skipped_members: int
    skipped_invalid: int

    @property
    def created_any(self) -> bool:
        """Whether the import created at least one row (drives success vs error feedback)."""
        return self.imported > 0

    @property
    def summary(self) -> str:
        """A member-friendly one-line summary of the import outcome."""
        skips: list[str] = []
        if self.skipped_existing:
            skips.append(f"{self.skipped_existing} already on your list")
        if self.skipped_members == 1:
            skips.append("1 that is a member")
        elif self.skipped_members:
            skips.append(f"{self.skipped_members} that are members")
        if self.skipped_invalid:
            skips.append(f"{self.skipped_invalid} invalid")
        skip_clause = ""
        if skips:
            if len(skips) == 1:
                skip_clause = f" Skipped {skips[0]}."
            else:
                skip_clause = f" Skipped {', '.join(skips[:-1])}, and {skips[-1]}."
        if not self.created_any:
            if skips:
                return f"No new addresses imported.{skip_clause}"
            return "No email addresses found in that file."
        plural = "address" if self.imported == 1 else "addresses"
        return f"Imported {self.imported} {plural}.{skip_clause}"


class GuildMailingListEmail(models.Model):
    """A non-member email address that also receives a guild's announcement emails.

    Guild leads add booster / partner / personal addresses here so people who aren't
    members of the guild still get its announcement emails. Delivery is additive — the
    guild's members are still emailed and these addresses ride alongside, deduped against
    the member roster (see :meth:`GuildAnnouncement.notify_members`).
    """

    guild = models.ForeignKey(
        Guild,
        on_delete=models.CASCADE,
        related_name="mailing_list_emails",
        help_text="The guild whose announcements this address receives.",
    )
    email = models.EmailField(
        max_length=254,
        help_text="A non-member email address that also receives this guild's announcement emails.",
    )
    label = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Optional — who this is (e.g. 'Front desk', 'Partner org').",
    )
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["guild", "email"], name="uq_guildmailinglistemail_guild_email"),
        ]

    def __str__(self) -> str:
        return f"{self.email} ({self.guild.name})"

    @classmethod
    def import_from_text(cls, guild: Guild, raw_text: str) -> MailingListImportResult:
        """Bulk-add custom addresses from an uploaded / pasted list, leniently.

        Splits ``raw_text`` into lines; within a line a single comma separating an email
        from a non-email second field is read as ``email, label``, otherwise every
        comma-separated token is treated as its own address. Each candidate email is
        validated and lower-cased, and a row is created — skipping tokens that fail
        validation, addresses already on the guild's custom list, and any that match a
        member's email (case-insensitive, the same collision key delivery uses).

        Args:
            guild: The guild to import the addresses onto.
            raw_text: The decoded file / paste contents.

        Returns:
            A :class:`MailingListImportResult` with the per-outcome counts.
        """
        from django.core.exceptions import ValidationError
        from django.core.validators import validate_email

        def _is_email(token: str) -> bool:
            try:
                validate_email(token)
            except ValidationError:
                return False
            return True

        member_emails = {(user.email or "").strip().lower() for user, _reason in guild.announcement_recipients()}
        existing = {
            (email or "").strip().lower() for email in guild.mailing_list_emails.values_list("email", flat=True)
        }
        next_order = (guild.mailing_list_emails.aggregate(top=models.Max("sort_order"))["top"] or 0) + 1

        imported = skipped_existing = skipped_members = skipped_invalid = 0
        for raw_line in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            fields = [field.strip() for field in raw_line.split(",") if field.strip()]
            if not fields:
                continue
            if len(fields) == 2 and not _is_email(fields[1]):
                candidates = [(fields[0], fields[1])]
            else:
                candidates = [(field, "") for field in fields]
            for token, label in candidates:
                if not _is_email(token):
                    skipped_invalid += 1
                    continue
                address = token.lower()
                if address in member_emails:
                    skipped_members += 1
                    continue
                if address in existing:
                    skipped_existing += 1
                    continue
                cls.objects.create(guild=guild, email=address, label=label, sort_order=next_order)
                existing.add(address)
                next_order += 1
                imported += 1
        return MailingListImportResult(
            imported=imported,
            skipped_existing=skipped_existing,
            skipped_members=skipped_members,
            skipped_invalid=skipped_invalid,
        )


class OrgInfoPage(HeroCropMixin, models.Model):
    """Singleton (pk=1) org-wide info page: map, parking, who-to-contact, code of conduct.

    Reuses the guild page's content shapes (hero banner, Markdown sections, FAQ, links)
    but is org-scoped and never participates in guild voting, funding, or the directory.
    Load the one row via :meth:`load`, exactly like ``SiteConfiguration``.
    """

    banner_image = models.ImageField(
        upload_to="org/banner/",
        blank=True,
        validators=[validate_image_size],
        help_text="Optional hero banner across the top of the Space & Org Info page.",
    )
    intro = models.TextField(
        blank=True,
        default="",
        help_text="Welcome / overview blurb shown at the top of the page. Supports Markdown.",
    )
    floorplan_image = models.ImageField(
        upload_to="org/floorplan/",
        blank=True,
        validators=[validate_image_size],
        help_text="Annotated floor plan — guild locations, restrooms, emergency exits. Click-to-zoom on the page.",
    )
    floorplan_caption = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text="Caption shown under the map, e.g. 'Guild locations, restrooms, and emergency exits.'",
    )
    parking = models.TextField(
        blank=True,
        default="",
        help_text="Parking & arrival info. Supports Markdown.",
    )
    who_to_contact = models.TextField(
        blank=True,
        default="",
        help_text="Org structure / who's-who / who to contact for what. Supports Markdown (a list or headings).",
    )
    code_of_conduct = models.TextField(
        blank=True,
        default="",
        help_text="Code of conduct body. Supports Markdown. Leave blank to link out instead.",
    )
    code_of_conduct_url = models.URLField(
        blank=True,
        default="",
        help_text="Optional external Code of Conduct link, used only when the body above is blank.",
    )
    updated_at = models.DateTimeField(auto_now=True, help_text="When this page was last edited.")

    class Meta:
        verbose_name = "Space & Org Info Page"
        verbose_name_plural = "Space & Org Info Page"

    def __str__(self) -> str:
        return "Space & Org Info"

    def get_hero_image_field_name(self) -> str:
        return "banner_image"

    @property
    def has_code_of_conduct(self) -> bool:
        """True when a written body or an external link is set — drives the section/nav."""
        return bool(self.code_of_conduct or self.code_of_conduct_url)

    def intro_parts(self) -> dict[str, SafeString]:
        """The rendered intro split for the Help hero — the lead HTML vs. the admonition tail.

        The Help landing hero shows only the intro's lead copy; admonition blocks (the
        seeded ``!!! tip``) drop into normal flow below the hero. Splits the *rendered*
        page-content HTML at the first admonition ``<div>``, so it behaves the same for
        Markdown and rich-editor HTML intros (the latter simply has no admonitions).
        Both halves are sanitized by ``render_page_content`` — safe to render directly.
        """
        from django.utils.safestring import mark_safe

        from membership.markdown import render_page_content

        html = render_page_content(self.intro or "", profile="help")
        match = re.search(r'<div[^>]*class="[^"]*admonition', html)
        if match is None:
            return {"lead": mark_safe(html), "admonitions": mark_safe("")}  # noqa: S308 — sanitized above
        idx = match.start()
        return {"lead": mark_safe(html[:idx]), "admonitions": mark_safe(html[idx:])}  # noqa: S308 — sanitized above

    def save(self, *args: Any, **kwargs: Any) -> None:
        from django.conf import settings

        self.pk = 1
        delete_orphan_on_replace(self, "banner_image")
        delete_orphan_on_replace(self, "floorplan_image")
        # Normalize the floor plan at the HERO long edge (larger than gallery) so its
        # annotations stay legible when a member zooms in.
        normalize_field_if_uploaded(self, "floorplan_image", settings.IMAGE_MAX_LONG_EDGE_HERO)
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "OrgInfoPage":
        """Load the singleton row, creating it with defaults if needed."""
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj


class OrgFAQItem(models.Model):
    """A question/answer pair in the Space & Org Info page FAQ — mirrors ``GuildFAQItem``.

    An answer may also carry a YouTube embed and/or one attached document — either an
    uploaded file or a link to an external doc (at most one of the two).
    """

    page = models.ForeignKey(
        OrgInfoPage, on_delete=models.CASCADE, related_name="faq_items", help_text="Parent org-info page."
    )
    question = models.CharField(max_length=500, help_text="The question.")
    answer = models.TextField(help_text="The answer.")
    video_url = models.URLField(
        blank=True,
        default="",
        help_text="Optional YouTube link shown with this answer (watch, youtu.be, embed, or shorts URL).",
    )
    document = models.FileField(
        upload_to="org/faq/",
        blank=True,
        validators=[validate_document],
        help_text="Optional document (PDF, Word, slides, spreadsheet…) shown with this answer. "
        "Leave blank to link an external doc instead.",
    )
    document_url = models.URLField(
        blank=True,
        default="",
        help_text="Optional link to an external doc for this answer. Leave blank if you uploaded a file.",
    )
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")

    class Meta:
        ordering = ["sort_order"]
        constraints = [
            # A document is optional, but it can't be BOTH an upload and a link.
            models.CheckConstraint(
                condition=Q(document="") | Q(document_url=""),
                name="ck_orgfaqitem_doc_not_both",
            ),
        ]

    def __str__(self) -> str:
        return self.question

    @property
    def has_document(self) -> bool:
        return bool(self.document) or bool(self.document_url)

    @property
    def document_display_name(self) -> str:
        """The uploaded file's base name, else the link URL, else ``""``."""
        if self.document and self.document.name:
            return self.document.name.rsplit("/", 1)[-1]
        return self.document_url

    @property
    def document_href(self) -> str:
        """Where the document link points — the uploaded file's URL or the external link."""
        return self.document.url if self.document else self.document_url

    def save(self, *args: Any, **kwargs: Any) -> None:
        delete_orphan_on_replace(self, "document")
        super().save(*args, **kwargs)


class OrgLink(models.Model):
    """A named external link shown in the Space & Org Info page sidebar — mirrors ``GuildLink``."""

    page = models.ForeignKey(
        OrgInfoPage, on_delete=models.CASCADE, related_name="links", help_text="Parent org-info page."
    )
    label = models.CharField(max_length=100, help_text="Display text, e.g. 'Member Guide'.")
    url = models.URLField(help_text="Destination URL.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")

    class Meta:
        ordering = ["sort_order"]

    def __str__(self) -> str:
        return f"{self.label} (Space & Org Info)"


class HelpCategoryQuerySet(models.QuerySet):
    def with_published_counts(self) -> "HelpCategoryQuerySet":
        """Annotate each category with the number of published guides it holds."""
        return self.annotate(published_count=Count("articles", filter=Q(articles__is_published=True)))

    def nonempty(self) -> "HelpCategoryQuerySet":
        """Only categories with at least one published guide — call after ``with_published_counts``."""
        return self.filter(published_count__gt=0)

    def landing_ranked(self) -> "HelpCategoryQuerySet":
        """Audience-rank ordering for the Help landing: member → developer → instructor → guild_lead → admin.

        ``{% regroup %}`` only groups *adjacent* rows, so this ordering is what keeps each
        audience heading contiguous on the landing grid (global ``(sort_order, pk)`` alone
        would interleave the groups). ``audience_heading`` carries the group's display label.
        """
        rank = Case(
            When(audience=HelpCategory.Audience.MEMBER, then=Value(0)),
            When(audience=HelpCategory.Audience.DEVELOPER, then=Value(1)),
            When(audience=HelpCategory.Audience.INSTRUCTOR, then=Value(2)),
            When(audience=HelpCategory.Audience.GUILD_LEAD, then=Value(3)),
            When(audience=HelpCategory.Audience.ADMIN, then=Value(4)),
            output_field=models.IntegerField(),
        )
        heading = Case(
            When(audience=HelpCategory.Audience.MEMBER, then=Value("For every member")),
            When(audience=HelpCategory.Audience.DEVELOPER, then=Value("For developers")),
            When(audience=HelpCategory.Audience.INSTRUCTOR, then=Value("Teaching")),
            When(audience=HelpCategory.Audience.GUILD_LEAD, then=Value("Running a guild")),
            When(audience=HelpCategory.Audience.ADMIN, then=Value("Admin")),
            output_field=CharField(),
        )
        return self.annotate(audience_rank=rank, audience_heading=heading).order_by("audience_rank", "sort_order", "pk")


class HelpCategory(models.Model):
    """A help-center category — groups :class:`WikiArticle` guides on the Help landing page."""

    class Audience(models.TextChoices):
        MEMBER = "member", "Members"
        DEVELOPER = "developer", "Developers"
        GUILD_LEAD = "guild_lead", "Guild leads & staff"
        INSTRUCTOR = "instructor", "Instructors"
        ADMIN = "admin", "Admins"

    name = models.CharField(
        max_length=100,
        help_text="Category name shown on the Help landing page, e.g. 'Running a guild'.",
    )
    slug = models.SlugField(
        max_length=120,
        unique=True,
        blank=True,
        help_text="URL segment (/help/<slug>/). Auto-filled from the name; stable once set.",
    )
    audience = models.CharField(
        max_length=20,
        choices=Audience.choices,
        default=Audience.MEMBER,
        help_text="Who this category is for — groups it on the landing page and drives the badge.",
    )
    description = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text="One-liner under the name on the landing card.",
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text="Order within its audience group; lower shows first.",
    )

    objects = HelpCategoryQuerySet.as_manager()

    class Meta:
        ordering = ["sort_order", "pk"]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_audience_display()})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Slugify the name into an empty slug (de-duplicated) and place new rows at the end.

        The slug stays stable once set — only filled when blank, same idiom as
        :meth:`WikiArticle.save`. A new row created with the default ``sort_order=0`` gets
        ``max(existing sort_order) + 10`` so an admin-created category lands at the end
        instead of jumping to the front (seeded categories pass explicit values).
        """
        from django.utils.text import slugify

        if not self.slug:
            base = slugify(self.name) or "category"
            slug = base
            n = 2
            siblings = HelpCategory.objects.exclude(pk=self.pk)
            while siblings.filter(slug=slug).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        if self._state.adding and self.sort_order == 0:
            current_max = HelpCategory.objects.aggregate(models.Max("sort_order"))["sort_order__max"]
            self.sort_order = (current_max or 0) + 10
        super().save(*args, **kwargs)


# Regex-based Markdown stripping for snippets/leads — deliberately no HTML round
# trip, so raw HTML an author typed (e.g. a literal <script>) survives into the
# plain text and gets escaped exactly once, at snippet-build time.
_MD_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_HEADING_MARK_RE = re.compile(r"^#{1,6}\s*", flags=re.MULTILINE)
_MD_ANCHOR_RE = re.compile(r"\{#[^}]*\}")
_MD_EMPHASIS_RE = re.compile(r"[*_`]")

# One pass over the help-rendered body: h2/h3 headings that carry an id.
_HELP_TOC_HEADING_RE = re.compile(r'<h([23])[^>]*\bid="([^"]+)"[^>]*>(.*?)</h\1>', re.DOTALL)
# Paragraph blocks of a rich-editor HTML body — lead_text's HTML-mode analog of splitting
# Markdown on blank lines (headings/lists fall outside <p> and are skipped naturally).
_HTML_PARAGRAPH_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)


def _markdown_to_text(source: str) -> str:
    """Strip Markdown syntax to whitespace-normalized plain text."""
    text = _MD_CODE_FENCE_RE.sub(" ", source)
    text = _MD_IMAGE_RE.sub(r"\1", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_HEADING_MARK_RE.sub("", text)
    text = _MD_ANCHOR_RE.sub("", text)
    text = _MD_EMPHASIS_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _source_to_text(source: str) -> str:
    """Flatten a dual-mode body — Markdown *or* rich-editor HTML — to plain text.

    The same sniff as :func:`membership.markdown.render_page_content`: HTML bodies (saved
    by the /help/edit/ rich editors) are tag-stripped via the email sanitizer's flattener;
    everything else is Markdown-stripped as before. Feeds search snippets and lead text.
    """
    from core.html_sanitize import rich_html_to_text
    from membership.markdown import looks_like_html

    if looks_like_html(source):
        return rich_html_to_text(source)
    return _markdown_to_text(source)


class WikiArticleQuerySet(models.QuerySet):
    def published(self) -> "WikiArticleQuerySet":
        """Only the guides members should see — drafts stay off the public page."""
        return self.filter(is_published=True)

    def search(self, q: str) -> "WikiArticleQuerySet":
        """Plain help-center search: split ``q`` on whitespace and AND one clause per term.

        Each term matches ``icontains`` across title, body, and category name; chained
        filters AND the terms (a whole-string match would return zero results for
        "how do I book an orientation"). Published, listed guides only — drafts and
        ``help_content.UNLISTED_SLUGS`` never surface. Empty ``q`` returns ``none()``
        (the view shows the prompt state instead).
        """
        terms = q.split()
        if not terms:
            return self.none()
        from membership.help_content import UNLISTED_SLUGS

        qs = self.published().exclude(slug__in=UNLISTED_SLUGS).select_related("category")
        for term in terms:
            qs = qs.filter(Q(title__icontains=term) | Q(body__icontains=term) | Q(category__name__icontains=term))
        return qs


class WikiArticle(models.Model):
    """A titled Markdown guide on the Wiki page, deep-linkable by slug anchor.

    Ordered inline children of the :class:`OrgInfoPage` singleton, exactly like
    :class:`OrgFAQItem` — the editor reuses that formset wiring. Rendered as a
    table-of-contents entry plus an anchored ``<section id="{slug}">`` so Discord, email,
    or other pages can link to a guide by name.
    """

    page = models.ForeignKey(
        OrgInfoPage,
        on_delete=models.CASCADE,
        related_name="articles",
        help_text="Parent Wiki page (the singleton).",
    )
    title = models.CharField(max_length=200, help_text="Guide heading, e.g. 'Guild voting'.")
    slug = models.SlugField(
        max_length=220,
        blank=True,
        help_text="URL anchor for deep links. Auto-filled from the title when left blank.",
    )
    body = models.TextField(help_text="The guide body. Supports Markdown.")
    category = models.ForeignKey(
        HelpCategory,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="articles",
        help_text="Where this guide lives in the help center. Uncategorized guides don't appear on the landing grid.",
    )
    related_articles = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="related_to",
        help_text=(
            "Hand-picked guides shown under 'Related guides'. "
            "Same-category guides fill any remaining slots automatically."
        ),
    )
    sort_order = models.PositiveIntegerField(default=0, help_text="Order on the page; lower shows first.")
    is_published = models.BooleanField(
        default=True,
        help_text="Uncheck to keep a draft off the public page while writing.",
    )

    objects = WikiArticleQuerySet.as_manager()

    class Meta:
        ordering = ["sort_order", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["page", "slug"], name="uq_wikiarticle_page_slug"),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def audience(self) -> str:
        """The category's audience — one source of truth; uncategorized guides read as member-facing."""
        category = self.category
        if category is None:
            return HelpCategory.Audience.MEMBER
        return category.audience

    @property
    def url_category_segment(self) -> str:
        """The category segment of this guide's URL — the reserved ``more`` when uncategorized,
        so every published article always has a canonical URL."""
        return self.category.slug if self.category is not None else "more"

    def get_absolute_url(self) -> str:
        """Canonical help-center URL: ``/help/<category>/<article>/``."""
        return reverse(
            "hub_help_article",
            kwargs={"category_slug": self.url_category_segment, "article_slug": self.slug},
        )

    def lead_text(self, limit: int = 160) -> str:
        """First paragraph of the body, stripped to plain text, truncated on a word boundary.

        Used by category rows, the landing's "All guides" list, and search fallbacks.
        Heading-only and image-only blocks are skipped — they aren't lead copy. Dual-mode:
        a rich-editor HTML body iterates its ``<p>`` blocks instead of Markdown blocks.
        """
        from membership.markdown import looks_like_html

        if looks_like_html(self.body):
            blocks = [f"<p>{inner}</p>" for inner in _HTML_PARAGRAPH_RE.findall(self.body)]
        else:
            blocks = [b for b in re.split(r"\n\s*\n", self.body) if not b.lstrip().startswith("#")]
        for block in blocks:
            text = _source_to_text(block)
            if not text:
                continue
            if len(text) <= limit:
                return text
            return text[:limit].rsplit(" ", 1)[0] + "…"
        return ""

    def search_snippet(self, q: str, radius: int = 90) -> str:
        """An HTML-escaped window around the first hit of ``q``, the match wrapped in ``<mark>``.

        The body — Markdown or rich-editor HTML — is stripped to plain text; escaping
        happens *before* the ``<mark>`` insertion, so the return value is safe to
        ``mark_safe`` in the template. Title-only hits (no term in the body) fall back
        to the lead text.
        """
        text = _source_to_text(self.body)
        lowered = text.lower()
        hit_start = -1
        hit_len = 0
        for term in q.split():
            idx = lowered.find(term.lower())
            if idx != -1 and (hit_start == -1 or idx < hit_start):
                hit_start, hit_len = idx, len(term)
        if hit_start == -1:
            return escape(self.lead_text())
        start = max(0, hit_start - radius)
        end = min(len(text), hit_start + hit_len + radius)
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(text) else ""
        return (
            prefix
            + escape(text[start:hit_start])
            + "<mark>"
            + escape(text[hit_start : hit_start + hit_len])
            + "</mark>"
            + escape(text[hit_start + hit_len : end])
            + suffix
        )

    def related_for_display(self, limit: int = 3) -> list["WikiArticle"]:
        """Guides for the "Related guides" footer — explicit picks first, same-category fill after.

        Explicit published ``related_articles`` lead, in the order they were picked
        (the M2M through rows' insertion order — seed order). Same-category published
        siblings fill remaining slots by ``(sort_order, pk)``, excluding this article
        and the already-picked; uncategorized guides get explicit picks only. Fewer
        than ``limit`` exist → return fewer (the template hides an empty footer).
        """
        through = WikiArticle.related_articles.through
        seed_order = list(
            through.objects.filter(from_wikiarticle=self).order_by("pk").values_list("to_wikiarticle_id", flat=True)
        )
        explicit = {a.pk: a for a in self.related_articles.filter(is_published=True).select_related("category")}
        picked = [explicit[pk] for pk in seed_order if pk in explicit][:limit]
        if self.category_id is not None and len(picked) < limit:
            fill = (
                WikiArticle.objects.published()
                .filter(category_id=self.category_id)
                .exclude(pk__in={self.pk, *(a.pk for a in picked)})
                .select_related("category")
                .order_by("sort_order", "pk")
            )
            picked.extend(fill[: limit - len(picked)])
        return picked

    def next_in_category(self) -> "WikiArticle | None":
        """The published neighbor after this guide in its category, ``(sort_order, pk)`` order.

        ``None`` at the end of the category and for uncategorized guides — the
        template hides the missing side.
        """
        if self.category_id is None:
            return None
        return (
            WikiArticle.objects.published()
            .filter(category_id=self.category_id)
            .filter(Q(sort_order__gt=self.sort_order) | Q(sort_order=self.sort_order, pk__gt=self.pk))
            .order_by("sort_order", "pk")
            .first()
        )

    def previous_in_category(self) -> "WikiArticle | None":
        """The published neighbor before this guide in its category — see :meth:`next_in_category`."""
        if self.category_id is None:
            return None
        return (
            WikiArticle.objects.published()
            .filter(category_id=self.category_id)
            .filter(Q(sort_order__lt=self.sort_order) | Q(sort_order=self.sort_order, pk__lt=self.pk))
            .order_by("-sort_order", "-pk")
            .first()
        )

    def toc(self) -> list[tuple[int, str, str]]:
        """``(level, anchor_id, text)`` for the h2/h3 headings *with ids* in the rendered body.

        One regex pass over the sanitized output — testable, no client JS. Headings
        without ids are skipped; an article with none yields ``[]`` and the TOC block
        hides. Rich-editor HTML bodies route through the page-content sanitizer, whose
        headings carry no ids — so their TOC is empty by design.
        """
        from membership.markdown import render_page_content

        html = render_page_content(self.body, profile="help")
        return [
            (int(level), anchor, unescape(strip_tags(inner)).strip())
            for level, anchor, inner in _HELP_TOC_HEADING_RE.findall(html)
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Slugify the title into an empty slug, de-duplicating within the page.

        Business logic on the model, per house style. The slug stays stable once set (so an
        existing deep link never breaks) because we only fill it when blank.
        """
        from django.utils.text import slugify

        if not self.slug:
            base = slugify(self.title) or "article"
            slug = base
            n = 2
            siblings = WikiArticle.objects.filter(page=self.page).exclude(pk=self.pk)
            while siblings.filter(slug=slug).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)


class GuildAnnouncementQuerySet(models.QuerySet):
    def active(self) -> "GuildAnnouncementQuerySet":
        """Announcements still showing — no expiry, or expiring today or later."""
        return self.filter(Q(expires_at__isnull=True) | Q(expires_at__gte=timezone.localdate()))

    def published(self) -> "GuildAnnouncementQuerySet":
        """Only announcements that are live — a lead's direct post or an approved proposal.

        Everything a member reads on the guild page, home feed, or activity list must be
        Published; a pending/changes-requested/declined proposal is never public.
        """
        return self.filter(moderation_state=GuildAnnouncement.ModerationState.PUBLISHED)

    def awaiting_review(self) -> "GuildAnnouncementQuerySet":
        """Member proposals waiting on a lead/admin decision (the reviewer queue)."""
        return self.filter(moderation_state=GuildAnnouncement.ModerationState.PENDING)

    def for_member(self, member: "Member") -> "GuildAnnouncementQuerySet":
        """Announcements from the guilds this member has explicitly joined."""
        return self.filter(guild__memberships__member=member)


class InvalidAnnouncementTransition(ValueError):
    """Raised when a :class:`GuildAnnouncement` lifecycle method is called from a state
    that does not permit it (e.g. approving an already-declined proposal)."""


class GuildAnnouncement(models.Model):
    """A news post on a guild page.

    Posting an announcement notifies the guild's members via :meth:`notify_members`
    (the ``guild.announcement`` event on the notification spine): an in-app bell row,
    an opt-out email, and a Discord broadcast. The view calls ``notify_members`` once,
    after the row is saved.
    """

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="announcements", help_text="Parent guild.")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who posted it.",
    )
    title = models.CharField(max_length=300, help_text="Announcement headline.")
    body = models.TextField(help_text="Announcement body.")
    published_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateField(
        null=True,
        blank=True,
        help_text="Last day this announcement shows on the guild page. Blank = never expires.",
    )
    send_email = models.BooleanField(
        default=True,
        help_text="Also email this announcement to members who joined the guild.",
    )

    class DiscordChannel(models.TextChoices):
        GUILD = "guild", "Our Guild Channel"
        GENERAL = "general", "#general-chat"
        LEADERSHIP = "leadership", "#leadership"
        OFFICERS = "officers", "#guild-officers"
        NONE = "none", "Don't post to Discord"

    discord_channel = models.CharField(
        max_length=20,
        choices=DiscordChannel.choices,
        default=DiscordChannel.GUILD,
        help_text="Which Discord channel this announcement posted to (or 'none').",
    )

    class ModerationState(models.TextChoices):
        PUBLISHED = "published", "Published"  # live on the guild page (a lead post or an approved proposal)
        PENDING = "pending", "Pending review"  # a member's proposal awaiting a decision; not yet public
        CHANGES_REQUESTED = "changes_requested", "Changes requested"  # sent back to the proposer to edit + resubmit
        DECLINED = "declined", "Declined"  # turned down; never posted

    # --- Moderation (member-proposal review lifecycle) ------------------------
    moderation_state = models.CharField(
        max_length=20,
        choices=ModerationState.choices,
        default=ModerationState.PUBLISHED,
        help_text=(
            "Where this announcement is in the review flow. Leads/staff/admins post already "
            "Published; a member's proposal starts Pending until a lead or admin approves it."
        ),
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The member who proposed this announcement (for posts that went through review).",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The lead or admin who approved, declined, or requested changes.",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, help_text="When the review decision was recorded.")
    review_notes = models.TextField(
        blank=True,
        default="",
        help_text="The reviewer's note to the proposer (shown on a decline or a changes-requested).",
    )
    updated_at = models.DateTimeField(auto_now=True, help_text="When this announcement was last edited.")

    objects = GuildAnnouncementQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at"]

    def __str__(self) -> str:
        return f"{self.title} ({self.guild.name})"

    @property
    def is_active(self) -> bool:
        """Visible on the page — never expires, or the expiry is today or later."""
        return self.expires_at is None or self.expires_at >= timezone.localdate()

    def resolve_discord_webhook(self) -> str:
        """Map :attr:`discord_channel` to the webhook URL this announcement posts to.

        Returns ``""`` for :attr:`DiscordChannel.NONE` or any channel whose webhook is
        unset — the emit path treats a blank result as "no Discord post" (a stripped,
        best-effort echo). The makerspace-wide #general-chat / #leadership / #guild-officers
        webhooks live on :class:`core.models.SiteConfiguration`; "Our Guild Channel" is the guild's own
        ``discord_webhook_url``.

        Returns:
            The chosen webhook URL, or ``""`` when the channel is "none" or unconfigured.

        Raises:
            ValueError: If ``discord_channel`` holds an unknown value (fail loudly).
        """
        try:
            return resolve_channel_webhook(self.discord_channel, self.guild)
        except ValueError as exc:
            raise ValueError(f"Unknown Discord channel '{self.discord_channel}' on announcement {self.pk}.") from exc

    def notify_members(
        self,
        *,
        discord_mention: str = "",
        email_message: "Message | None" = None,
        selected_user_ids: "set[int] | None" = None,
        selected_custom_emails: "list[str] | None" = None,
    ) -> None:
        """Emit ``guild.announcement`` to the guild's members (Decision 4).

        Recipients resolve to every active member of this guild (scoped — not
        site-wide); they receive an in-app bell row (always), an email (opt-out), and
        a single Discord broadcast. The copy is DB-editable; the merge fields come from
        this announcement. Called once by the create view after the row is saved.

        The author's controls are honored here: when ``send_email`` is off the
        per-recipient email channel is suppressed (the in-app bell still fires), and the
        persisted ``discord_channel`` choice picks exactly where the single Discord echo
        posts (:meth:`resolve_discord_webhook`). A resolved webhook of ``""`` (the "Don't
        post to Discord" choice, or an unconfigured channel) suppresses the guild
        broadcast entirely — the bell + email still deliver.

        The ``period`` is keyed to this announcement's pk so re-saving never double-
        notifies, while a different announcement (a different pk) still notifies.

        Args:
            discord_mention: An opt-in Discord ping literal (``"@here"`` / ``"@everyone"``,
                or ``""`` for none), threaded onto the single Discord echo. Default off, so
                every existing caller (the guild-edit create view, the proposal-approve path)
                is byte-unaffected.
            email_message: A pre-rendered branded EMAIL :class:`Message` override. When given
                (the compose wizard passes the same shell its Step-2 preview renders), it
                replaces the default copy-mode guild email so the sent email matches the
                preview; when ``None`` (every existing caller) the copy-mode email stands.
            selected_user_ids: A per-announcement EMAIL **subset** of member ``pk`` values (the
                compose wizard's recipient checklist). ``None`` (every existing caller — the
                guild-edit create view, :meth:`approve`) emails every member as before; a set
                narrows the member email to those ``pk`` values while the in-app bell + Discord
                post still reach the whole guild (see ``emit(email_only_user_ids=…)``).
            selected_custom_emails: A per-announcement subset of the guild's custom mailing-list
                addresses (lower-cased). ``None`` (every existing caller) sends the FULL custom
                list — the mailing-list feature is never regressed; a list narrows the additive
                ``extra_emails`` to those addresses only. A selection only ever *narrows*.
        """
        from django.urls import reverse

        from core.events.channels import Channel
        from core.events.emit import emit
        from membership.orientations import _absolute_url

        guild_url = _absolute_url(reverse("hub_guild_detail", args=[self.guild.slug]))
        webhook = self.resolve_discord_webhook()
        # Custom (non-member) mailing-list addresses ride along ADDITIVELY, deduped against the
        # member roster on the lower-cased ``user.email`` key. The FULL deduped list is always
        # computed (the mailing-list choke point stays exactly as shipped) and only NARROWED when
        # a ``selected_custom_emails`` subset is passed — so ``None`` still sends everyone. Only
        # when email is on: turning "Also send email" off suppresses the custom addresses too.
        extra_emails: list[str] | None = None
        if self.send_email:
            member_emails = {
                (user.email or "").strip().lower() for user, _reason in self.guild.announcement_recipients()
            }
            all_custom = self.guild.mailing_list_emails_deduped(member_emails)
            extra_emails = (
                all_custom
                if selected_custom_emails is None
                else [addr for addr in all_custom if addr in set(selected_custom_emails)]
            )
        emit(
            "guild_announcement",
            actor=self.author,
            target=self,
            context={
                "guild": self.guild,
                "member_name": "there",
                "guild_name": self.guild.name,
                "announcement_title": self.title,
                "announcement_body": self.body,
                "guild_url": guild_url,
                "discord_broadcast_webhook": webhook,
            },
            url=guild_url,
            period=f"announcement:{self.pk}",
            messages={Channel.EMAIL: email_message} if email_message is not None else None,
            suppress_email=not self.send_email,
            suppress_guild_broadcast=(webhook == ""),
            discord_mention=discord_mention,
            extra_emails=extra_emails,
            email_only_user_ids=selected_user_ids,
        )

    # --- Review lifecycle (member proposals) ----------------------------------

    def submit_for_review(self, *, submitted_by: "User") -> None:
        """Enter (or re-enter) the review queue — the member-proposal path.

        Used for BOTH the first submit and a resubmit after a changes-requested edit.
        Sets ``PENDING``, records the proposer as the author (so the eventual post is
        credited to them), clears any prior verdict, and notifies the guild's leadership.
        A pending proposal is never public — nothing posts until :meth:`approve`.

        Raises:
            InvalidAnnouncementTransition: If the announcement is already published or declined.
        """
        if self.pk is not None and self.moderation_state not in (
            self.ModerationState.PENDING,
            self.ModerationState.CHANGES_REQUESTED,
        ):
            raise InvalidAnnouncementTransition(
                f"Cannot submit an announcement in state '{self.moderation_state}' for review."
            )
        self.moderation_state = self.ModerationState.PENDING
        self.submitted_by = submitted_by
        self.author = submitted_by
        self.reviewed_by = None
        self.reviewed_at = None
        self.review_notes = ""
        if self.pk is None:
            self.save()
        else:
            self.save(
                update_fields=[
                    "moderation_state",
                    "submitted_by",
                    "author",
                    "reviewed_by",
                    "reviewed_at",
                    "review_notes",
                    "updated_at",
                ]
            )
        self._emit_submitted()

    def withdraw(self, *, by: "User") -> None:
        """The proposer pulls back their own not-yet-published proposal — deletes the row.

        Raises:
            InvalidAnnouncementTransition: If the announcement is published or declined.
        """
        if self.moderation_state not in (self.ModerationState.PENDING, self.ModerationState.CHANGES_REQUESTED):
            raise InvalidAnnouncementTransition(f"Cannot withdraw an announcement in state '{self.moderation_state}'.")
        self.delete()

    def approve(
        self,
        *,
        reviewer: "User",
        send_email: bool | None = None,
        discord_channel: str | None = None,
    ) -> None:
        """Single reviewer decision → live. Records the reviewer, posts it, notifies.

        The reviewer — not the proposer — owns the outbound channels: pass ``send_email``
        to set whether the opt-out guild-member email goes out, and ``discord_channel`` to
        choose which Discord channel the single echo posts to. Both default to the values
        already on the row when omitted, so the send options live and persist here rather
        than being mutated by the caller.

        Resets ``published_at`` to now so the announcement sorts and dates from when it
        actually went live (not when it was drafted), then fires :meth:`notify_members`
        (the guild-page post, the opt-out guild-member email, and the guild's Discord
        post) and tells the proposer it's up.

        Raises:
            InvalidAnnouncementTransition: If the announcement is not awaiting a decision.
        """
        if self.moderation_state not in (self.ModerationState.PENDING, self.ModerationState.CHANGES_REQUESTED):
            raise InvalidAnnouncementTransition(f"Cannot approve an announcement in state '{self.moderation_state}'.")
        if send_email is not None:
            self.send_email = send_email
        if discord_channel:
            self.discord_channel = discord_channel
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.moderation_state = self.ModerationState.PUBLISHED
        self.published_at = timezone.now()
        self.save(
            update_fields=[
                "reviewed_by",
                "reviewed_at",
                "moderation_state",
                "published_at",
                "send_email",
                "discord_channel",
                "updated_at",
            ]
        )
        self.notify_members()
        self._emit_decision(
            "guild_announcement.approved",
            url=self._guild_url(),
            period=f"announcement:{self.pk}:approved",
        )

    def request_changes(self, *, reviewer: "User", notes: str) -> None:
        """Send a pending proposal back to the proposer with a note to fix + resubmit.

        Raises:
            InvalidAnnouncementTransition: If the announcement is not currently pending.
            ValueError: If ``notes`` is blank (a changes request must explain what to fix).
        """
        if self.moderation_state != self.ModerationState.PENDING:
            raise InvalidAnnouncementTransition(
                f"Cannot request changes on an announcement in state '{self.moderation_state}'."
            )
        if not (notes or "").strip():
            raise ValueError("A changes request needs a note so the proposer knows what to fix.")
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.review_notes = notes
        self.moderation_state = self.ModerationState.CHANGES_REQUESTED
        self.save(update_fields=["reviewed_by", "reviewed_at", "review_notes", "moderation_state", "updated_at"])
        from django.urls import reverse

        edit_url = f"{settings.MEMBER_BASE_URL}{reverse('hub_guild_announcement_propose_edit', args=[self.pk])}"
        self._emit_decision(
            "guild_announcement.changes_requested",
            url=edit_url,
            period=f"announcement:{self.pk}:changes:{self.reviewed_at.timestamp()}",
        )

    def decline(self, *, reviewer: "User", notes: str) -> None:
        """Reject a proposal (it was never posted — nothing to unwind).

        Raises:
            InvalidAnnouncementTransition: If the announcement is not awaiting a decision.
            ValueError: If ``notes`` is blank (a decline must explain why).
        """
        if self.moderation_state not in (self.ModerationState.PENDING, self.ModerationState.CHANGES_REQUESTED):
            raise InvalidAnnouncementTransition(f"Cannot decline an announcement in state '{self.moderation_state}'.")
        if not (notes or "").strip():
            raise ValueError("A decline needs a note so the proposer knows why.")
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.review_notes = notes
        self.moderation_state = self.ModerationState.DECLINED
        self.save(update_fields=["reviewed_by", "reviewed_at", "review_notes", "moderation_state", "updated_at"])
        self._emit_decision(
            "guild_announcement.declined",
            url=self._guild_url(),
            period=f"announcement:{self.pk}:declined",
        )

    def _guild_url(self) -> str:
        """Absolute URL of this announcement's guild page (for notification links)."""
        from django.urls import reverse

        from membership.orientations import _absolute_url

        return _absolute_url(reverse("hub_guild_detail", args=[self.guild.slug]))

    def _proposer_display_name(self) -> str:
        """A friendly name for the proposer, for notification copy."""
        user = self.submitted_by
        if user is None:
            return "A Past Lives member"
        full = (user.get_full_name() or "").strip()
        if full:
            return full
        member = getattr(user, "member", None)
        if member is not None and member.display_name:
            return member.display_name
        return user.get_username()

    def _emit_submitted(self) -> None:
        """Notify the guild's leadership that a member's announcement awaits review.

        A fresh, timestamped ``period`` per submit round so a resubmit re-notifies
        reviewers instead of being deduped away by :func:`core.events.emit.emit`.
        """
        from django.urls import reverse

        from core.events.emit import emit

        review_url = (
            f"{settings.MEMBER_BASE_URL}{reverse('hub_guild_announcement_review_queue')}#announcement-{self.pk}"
        )
        emit(
            "guild_announcement.submitted",
            actor=self.submitted_by,
            target=self,
            context={
                "guild": self.guild,
                "guild_name": self.guild.name,
                "announcement_title": self.title,
                "proposer_name": self._proposer_display_name(),
                "review_url": review_url,
            },
            url=review_url,
            period=f"announcement:{self.pk}:submitted:{timezone.now().timestamp()}",
        )

    def _emit_decision(self, event_key: str, *, url: str, period: str) -> None:
        """Notify the proposer of a reviewer decision (approve / changes / decline).

        Builds the full superset context; each event's curated copy uses only its own
        documented placeholders (extra keys are ignored by the safe renderer).
        """
        from core.events.emit import emit

        emit(
            event_key,
            actor=self.reviewed_by,
            target=self,
            context={
                "user": self.submitted_by,
                "guild": self.guild,
                "guild_name": self.guild.name,
                "announcement_title": self.title,
                "announcement_body": self.body,
                "review_notes": self.review_notes,
                "guild_url": self._guild_url(),
                "action_url": url,
            },
            url=url,
            period=period,
        )


def resolve_channel_webhook(channel: str, guild: "Guild | None" = None) -> str:
    """Map a :class:`GuildAnnouncement.DiscordChannel` value to its webhook URL.

    Audience-agnostic twin of :meth:`GuildAnnouncement.resolve_discord_webhook` — it needs
    no announcement instance, so both the guild composer and the site-wide composer share
    one resolver. ``GUILD`` → the guild's own ``discord_webhook_url`` (``""`` when no guild
    is given — a site-wide audience has no guild channel); ``GENERAL`` / ``LEADERSHIP`` /
    ``OFFICERS`` → the makerspace-wide :class:`~core.models.SiteConfiguration` webhooks;
    ``NONE`` → ``""``.

    Args:
        channel: A ``GuildAnnouncement.DiscordChannel`` value.
        guild: The guild owning the ``GUILD`` channel, or ``None`` for a site-wide send.

    Returns:
        The resolved webhook URL, or ``""`` when the channel is "none" / unconfigured.

    Raises:
        ValueError: If ``channel`` holds an unknown value (fail loudly).
    """
    from core.models import SiteConfiguration

    channels = GuildAnnouncement.DiscordChannel
    if channel == channels.GUILD:
        return (guild.discord_webhook_url or "").strip() if guild is not None else ""
    if channel == channels.GENERAL:
        return (SiteConfiguration.load().discord_general_webhook_url or "").strip()
    if channel == channels.LEADERSHIP:
        return (SiteConfiguration.load().discord_leadership_webhook_url or "").strip()
    if channel == channels.OFFICERS:
        return (SiteConfiguration.load().discord_officers_webhook_url or "").strip()
    if channel == channels.NONE:
        return ""
    raise ValueError(f"Unknown Discord channel '{channel}'.")


def build_announcement_email_html(title: str, body: str) -> str:
    """Branded announcement email HTML — one builder for the preview and the real send.

    ``body`` is the rich-text editor's sanitized HTML; :func:`render_rich_email_body`
    inline-styles it for the dark card, the escaped title rides above it as an ``<h2>``,
    and the branded shell wraps the whole fragment. Shared by the compose wizard's live
    preview and the EMAIL override handed to the spine, so the two are byte-faithful.
    """
    from django.utils.html import escape

    from core.events.templates import wrap_email_html
    from core.html_sanitize import render_rich_email_body

    fragment = f"<h2>{escape(title)}</h2>{render_rich_email_body(body)}"
    return wrap_email_html(fragment)


class AlreadySentError(Exception):
    """Raised when :meth:`AnnouncementDraft.send` is called on an already-sent draft."""


class AnnouncementDraftManager(models.Manager["AnnouncementDraft"]):
    """Queries for the compose wizard's saved drafts."""

    def for_user(self, user: "User") -> "models.QuerySet[AnnouncementDraft]":
        """This user's resumable (unsent) drafts, newest first, guild pre-fetched."""
        return self.filter(author=user, sent_at__isnull=True).select_related("guild")


class AnnouncementDraft(models.Model):
    """A saved-or-sent announcement composed in the wizard (`/announcements/compose/`).

    One row backs the whole three-step compose: audience + rich message (Step 1), the
    "also email" choice (Step 2), and the Discord channel + opt-in @mention (Step 3). A
    draft can be saved (``sent_at`` NULL), resumed, and deleted; :meth:`send` stamps
    ``sent_at`` (mark-sent, not delete-on-send) so the resume list is a trivial
    ``sent_at IS NULL`` filter and the sent row survives as an audit record.

    A **site** send is ephemeral (emit only — site-wide announcements have no durable
    post today and gain none here). A **guild** send additionally materializes a published
    :class:`GuildAnnouncement` so the post shows on the guild page, the edit list, and the
    slideshow — see :meth:`send`.
    """

    class Audience(models.TextChoices):
        SITE = "site", "Everyone (site-wide)"
        GUILD = "guild", "A specific guild"

    class Mention(models.TextChoices):
        NONE = "none", "No ping"
        HERE = "here", "@here (online members)"
        EVERYONE = "everyone", "@everyone"

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="announcement_drafts",
        help_text="Whose draft this is — drives the resume list and the send actor.",
    )
    audience = models.CharField(
        max_length=10,
        choices=Audience.choices,
        default=Audience.SITE,
        help_text="Who hears it: everyone site-wide, or one guild's joined members.",
    )
    guild = models.ForeignKey(
        Guild,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="announcement_drafts",
        help_text="The target guild — set only when the audience is a specific guild.",
    )
    title = models.CharField(
        max_length=300,
        help_text="Subject / headline. Required even to save a draft (so the list row reads well).",
    )
    body = models.TextField(
        blank=True,
        default="",
        help_text="Sanitized rich HTML. May be blank while drafting; required to send.",
    )
    send_email = models.BooleanField(
        default=True,
        help_text="Also send this announcement as a branded email (in-app bell fires regardless).",
    )
    email_recipient_selection = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Which recipients get this announcement's email. Empty/absent = everyone (default). "
            'Shape: {"users": [pk, …], "custom": ["addr", …]}.'
        ),
    )
    discord_channel = models.CharField(
        max_length=20,
        choices=GuildAnnouncement.DiscordChannel.choices,
        default=GuildAnnouncement.DiscordChannel.NONE,
        help_text="Which Discord channel this echoes to (or 'none'). GUILD only for a guild audience.",
    )
    mention = models.CharField(
        max_length=10,
        choices=Mention.choices,
        default=Mention.NONE,
        help_text="Opt-in Discord ping — none, @here (online), or @everyone. Off by default.",
    )
    expires_at = models.DateField(
        null=True,
        blank=True,
        help_text="Guild only: last day the materialized post shows. Blank = never; ignored for a site send.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the draft was first created.")
    updated_at = models.DateTimeField(auto_now=True, help_text="Last edit — drives the resume-list ordering.")
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set on send. NULL = a resumable draft; non-null = an immutable sent record.",
    )

    objects = AnnouncementDraftManager()

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["author", "sent_at"], name="idx_%(class)s_author"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(audience="guild") | models.Q(guild__isnull=False),
                name="ck_%(class)s_guild_audience",
            ),
        ]

    def __str__(self) -> str:
        state = "sent" if self.sent_at else "draft"
        return f"{self.title} — {self.get_audience_display()} ({state})"

    def _mention_literal(self) -> str:
        """The Discord ping string for :attr:`mention` — ``""`` / ``"@here"`` / ``"@everyone"``."""
        return {self.Mention.NONE: "", self.Mention.HERE: "@here", self.Mention.EVERYONE: "@everyone"}[
            self.Mention(self.mention)
        ]

    def build_email_message(self, base_url: str) -> "Message":
        """The branded EMAIL :class:`Message` for this draft — the Step-2 preview *is* this.

        The one builder renders both the live preview and the override handed to the spine
        (``emit`` for a site send, ``notify_members(email_message=…)`` for a guild send), so
        the preview is always byte-faithful to what sends. ``base_url`` is the site root for a
        site send and the guild-detail URL for a guild send. The text part is the flattened
        rich body (matching the bell / Discord render).
        """
        from core.events.channels import Message
        from core.html_sanitize import rich_html_to_text

        body_text = rich_html_to_text(self.body)
        trigger = "guild_announcement" if self.audience == self.Audience.GUILD else "site_announcement"
        return Message(
            title=self.title,
            body=f"{self.title}\n\n{body_text}\n\n{base_url}",
            url=base_url,
            html_body=build_announcement_email_html(self.title, self.body),
            trigger_kind=trigger,
        )

    def recipient_count(self) -> int:
        """How many activated members this draft reaches right now (the confirm-dialog count).

        ``SITE`` → the all-active-members audience; ``GUILD`` → the guild's joined-member
        audience — the exact resolvers the send fans out to, so the count matches delivery.
        """
        from core.events import resolvers
        from core.events.registry import Recipients

        if self.audience == self.Audience.GUILD:
            return len(resolvers.resolve(Recipients.GUILD_MEMBERS, {"guild": self.guild}))
        return len(resolvers.resolve(Recipients.ALL_ACTIVE_MEMBERS, {}))

    @classmethod
    def save_from_form(
        cls, form: Any, author: "User", instance: "AnnouncementDraft | None" = None
    ) -> "AnnouncementDraft":
        """Upsert a draft from a validated :class:`~hub.forms.AnnouncementComposeForm`.

        Creates a new row or updates ``instance`` in place from the split audience/guild and
        the sanitized body. A ``GUILD`` audience without a guild fails loudly (also enforced
        by the form and the DB check constraint).
        """
        from django.core.exceptions import ValidationError

        cd = form.cleaned_data
        draft = instance or cls(author=author)
        draft.author = author
        draft.audience = cd["audience"]
        draft.guild = cd.get("guild")
        draft.title = cd["title"]
        draft.body = cd["body"]  # already sanitized by the form's clean_body
        draft.send_email = cd["send_email"]
        # Empty dict = "everyone" (the default); a present selection = exactly these recipients.
        draft.email_recipient_selection = cd.get("email_recipient_selection") or {}
        draft.discord_channel = cd["discord_channel"]
        draft.mention = cd["mention"]
        draft.expires_at = cd.get("expires_at")
        if draft.audience == cls.Audience.GUILD and draft.guild is None:
            raise ValidationError("Choose a guild for this announcement.")
        draft.save()
        return draft

    def send(self) -> tuple[int, int]:
        """Send the announcement and mark it sent — the wizard's one "fat" transition.

        Guards, then branches on audience. A **site** send is emit-only (ephemeral). A
        **guild** send first materializes a published :class:`GuildAnnouncement` (so the
        post lands on the guild page, the edit list, and the slideshow), then reuses the
        tested :meth:`GuildAnnouncement.notify_members` fan-out, mapping the saved
        ``email_recipient_selection`` (intersected with the *current* roster so stale ids
        can't resurrect) into the per-recipient subset + the narrowed custom addresses.

        Returns:
            An ``(emailed, total)`` pair for the post-send summary. ``total`` is the full
            addressable set (a site send: all active members; a guild send: the guild's
            members plus its custom mailing-list addresses — the checklist). ``emailed`` is
            how many of them this send actually emails: ``total`` when no selection was made,
            the chosen subset when one was, and ``0`` when "Also send email" is off. Everyone
            in ``total`` still gets the in-app bell regardless.

        Raises:
            AlreadySentError: If this draft was already sent.
            ValidationError: If the body sanitizes empty, or a guild audience has no guild.
        """
        from django.core.exceptions import ValidationError
        from django.urls import reverse

        from core.events.channels import Channel
        from core.events.emit import emit
        from core.html_sanitize import rich_html_to_text, sanitize_rich_html
        from membership.orientations import _absolute_url

        if self.sent_at is not None:
            raise AlreadySentError("This announcement was already sent.")
        body_html = sanitize_rich_html(self.body)
        if not body_html:
            raise ValidationError("Add a message before sending.")
        if self.audience == self.Audience.GUILD and self.guild is None:
            raise ValidationError("Choose a guild for this announcement.")

        mention_str = self._mention_literal()

        if self.audience == self.Audience.SITE:
            site_url = _absolute_url("/")
            webhook = resolve_channel_webhook(self.discord_channel, None)
            result = emit(
                "site_announcement",
                actor=self.author,
                context={
                    "member_name": "there",
                    "announcement_title": self.title,
                    "announcement_body": rich_html_to_text(body_html),
                    "site_url": site_url,
                    "discord_broadcast_webhook": webhook,
                },
                url=site_url,
                period=f"announce:{self.pk}:{timezone.now():%Y%m%d%H%M%S%f}",
                messages={Channel.EMAIL: self.build_email_message(site_url)},
                suppress_broadcast=(webhook == ""),
                suppress_email=not self.send_email,
                discord_mention=mention_str,
            )
            total = result.recipient_count
            counts = (total if self.send_email else 0, total)
        else:
            guild = cast(Guild, self.guild)  # the guard above guarantees a guild for a GUILD audience
            guild_url = _absolute_url(reverse("hub_guild_detail", args=[guild.slug]))
            selected_user_ids, selected_custom_emails, counts = self._guild_email_selection(guild)
            announcement = GuildAnnouncement.objects.create(
                guild=guild,
                author=self.author,
                title=self.title,
                # The guild page + slideshow render the body as plain text (|linebreaksbr),
                # so store the flattened rich body — the rich formatting still shows in the
                # branded email (the draft's own body keeps the rich HTML).
                body=rich_html_to_text(body_html),
                expires_at=self.expires_at,
                send_email=self.send_email,
                discord_channel=self.discord_channel,
            )
            announcement.notify_members(
                discord_mention=mention_str,
                email_message=self.build_email_message(guild_url),
                selected_user_ids=selected_user_ids,
                selected_custom_emails=selected_custom_emails,
            )

        self.sent_at = timezone.now()
        self.save(update_fields=["sent_at", "updated_at"])
        return counts

    def _guild_email_selection(self, guild: "Guild") -> "tuple[set[int] | None, list[str] | None, tuple[int, int]]":
        """Resolve the saved selection against the guild's *current* roster (guild-send helper).

        Returns ``(selected_user_ids, selected_custom_emails, (emailed, total))``:

        * An **absent/empty** ``email_recipient_selection`` means "everyone" → ``(None, None, …)``
          so :meth:`GuildAnnouncement.notify_members` emails the full roster (byte-identical to
          the pre-feature send). A **present** selection is intersected with the live roster so a
          member who left or a deleted custom row can't resurrect.
        * ``total`` counts the guild's members + its deduped custom addresses (the checklist);
          ``emailed`` is that total when no selection was made, the chosen subset when one was,
          and ``0`` when email is off (the selection is moot then).
        """
        member_recipients = guild.announcement_recipients()
        member_ids = {user.pk for user, _reason in member_recipients}
        member_emails = {(user.email or "").strip().lower() for user, _reason in member_recipients}
        all_custom = guild.mailing_list_emails_deduped(member_emails)
        total = len(member_recipients) + len(all_custom)

        selection = self.email_recipient_selection or {}
        if not selection:
            selected_user_ids: set[int] | None = None
            selected_custom_emails: list[str] | None = None
            chosen = total
        else:
            selected_user_ids = {int(pk) for pk in selection.get("users", [])} & member_ids
            custom_set = set(all_custom)
            selected_custom_emails = [
                addr.lower() for addr in selection.get("custom", []) if addr.lower() in custom_set
            ]
            chosen = len(selected_user_ids) + len(selected_custom_emails)

        emailed = chosen if self.send_email else 0
        return selected_user_ids, selected_custom_emails, (emailed, total)


class GuildMeetingNote(models.Model):
    """One meeting's notes / agenda on a guild page.

    A bundle per meeting: a date, a title, an optional Markdown body, and any
    number of child attachments (each a file OR a link). Members who can view the
    guild page can read these; staff/leads add, edit, and delete them. Meeting
    notes never expire, so there is no ``active()`` queryset — the default related
    manager plus ``Meta.ordering`` covers the page query.
    """

    guild = models.ForeignKey(
        Guild,
        on_delete=models.CASCADE,
        related_name="meeting_notes",
        help_text="Parent guild.",
    )
    meeting_date = models.DateField(help_text="The date this meeting took place (or is scheduled for).")
    title = models.CharField(max_length=300, help_text="Short headline, e.g. 'June general meeting'.")
    body = models.TextField(
        blank=True,
        default="",
        help_text="Optional written notes. Supports Markdown — bold, lists, links.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who posted these notes.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-meeting_date", "-created_at"]  # newest meeting first; tie-break newest post

    def __str__(self) -> str:
        return f"{self.title} — {self.guild.name} ({self.meeting_date:%Y-%m-%d})"

    @property
    def body_html(self) -> str:
        """Body Markdown rendered to sanitized HTML (safe to mark_safe in templates)."""
        from membership.markdown import render_markdown

        return render_markdown(self.body)


class GuildMeetingNoteAttachment(models.Model):
    """A file OR a link attached to a meeting note, repeated per note.

    Mirrors ``GuildImage`` for upload mechanics (``upload_to``, ``sort_order``,
    ``created_at``, ``save()`` orphan cleanup) but stores a document (not an image)
    and runs no image normalization. Exactly one of ``file`` / ``url`` is set —
    the friendly per-row guard lives on the form (``GuildMeetingNoteAttachmentForm``);
    the ``CheckConstraint`` here is the DB integrity backstop.
    """

    note = models.ForeignKey(
        GuildMeetingNote,
        on_delete=models.CASCADE,
        related_name="attachments",
        help_text="Parent meeting note.",
    )
    label = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="What to call this — e.g. 'Agenda PDF'. Defaults to the file name or link.",
    )
    file = models.FileField(
        upload_to="guilds/meeting_notes/",
        blank=True,
        validators=[validate_document],
        help_text="Upload a document (PDF, Word, slides, spreadsheet…). Leave blank if you're adding a link instead.",
    )
    url = models.URLField(
        blank=True,
        default="",
        help_text="Link to an external doc (e.g. a Google Doc). Leave blank if you uploaded a file instead.",
    )
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "created_at"]
        constraints = [
            models.CheckConstraint(
                condition=((Q(file="") & ~Q(url="")) | (~Q(file="") & Q(url=""))),
                name="ck_meetingnoteattachment_file_xor_url",
            ),
        ]

    def __str__(self) -> str:
        return f"Attachment #{self.pk} for {self.note.title}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        delete_orphan_on_replace(self, "file")  # mirror GuildImage; no image normalization
        super().save(*args, **kwargs)

    @property
    def is_file(self) -> bool:
        return bool(self.file)

    @property
    def is_link(self) -> bool:
        return bool(self.url)

    @property
    def display_name(self) -> str:
        """Label if set, else the file's base name, else the URL."""
        if self.label:
            return self.label
        if self.file and self.file.name:
            return self.file.name.rsplit("/", 1)[-1]
        return self.url


class GuildMembershipManager(models.Manager["GuildMembership"]):
    """Every writer of a :class:`GuildMembership` goes through here so ``source`` is
    set correctly — the anti-oscillation key for the two-way Discord sync (spec §4.5).

    Because ``unique(guild, member)`` means there is exactly one row per pair, the
    *ordering* of an in-app join and a Discord reaction matters. A bare
    ``get_or_create`` would leave a reaction-created row at ``source="discord"`` even
    after the member explicitly joined in-app, so a later un-react would delete a guild
    the member actually joined. These two methods make an explicit in-app join immune to
    inbound removal (it is promoted to — and never demoted from — ``source="app"``).
    """

    def record_app_join(self, guild: Guild, member: Member) -> tuple[GuildMembership, bool, bool]:
        """In-app join. Create as ``source="app"``; UPGRADE an existing ``source="discord"``
        row to ``source="app"`` (an explicit join outranks a standing reaction).

        Returns ``(membership, created, upgraded)`` — ``upgraded`` is True when an existing
        discord-sourced row was promoted, so the caller can fire the join side-effect (the
        guild lead never heard about the silent reaction; the real join is worth a notice).
        """
        membership, created = self.get_or_create(guild=guild, member=member, defaults={"source": self.model.Source.APP})
        upgraded = False
        if not created and membership.source != self.model.Source.APP:
            membership.source = self.model.Source.APP
            membership.save(update_fields=["source"])
            upgraded = True
        return membership, created, upgraded

    def record_discord_join(self, guild: Guild, member: Member) -> tuple[GuildMembership, bool]:
        """Inbound reaction mirror. Create as ``source="discord"``; NEVER downgrade an
        existing ``source="app"`` row (``get_or_create`` with ``defaults`` leaves it
        untouched). Returns ``(membership, created)``.
        """
        return self.get_or_create(guild=guild, member=member, defaults={"source": self.model.Source.DISCORD})


class GuildMembership(models.Model):
    """Explicit opt-in affiliation between a Member and a Guild."""

    class Source(models.TextChoices):
        APP = "app", "In-app join"
        DISCORD = "discord", "Discord reaction"

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="memberships", help_text="The guild.")
    member = models.ForeignKey(
        Member, on_delete=models.CASCADE, related_name="guild_memberships", help_text="The member."
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.APP,
        help_text=(
            "How this membership was created: an in-app join (app) or a Discord-reaction mirror "
            "(discord). Inbound reaction sync only ever adds/removes 'discord' rows and never touches "
            "'app' rows — this is what keeps the two directions from fighting."
        ),
    )

    objects = GuildMembershipManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["guild", "member"], name="uq_guildmembership_guild_member"),
        ]

    def __str__(self) -> str:
        return f"{self.member} in {self.guild.name}"


class DiscordGuildEmojiManager(models.Manager["DiscordGuildEmoji"]):
    """Query helper for the Discord reaction-emoji → guild map."""

    def mapping(self) -> dict[str, Guild]:
        """``{emoji: guild}`` for every configured row, in one query.

        Drives both directions of the reaction sync: a member who reacts with an emoji in
        this map joins the mapped guild. Unmapped emojis (no row) are skipped entirely.
        """
        return {row.emoji: row.guild for row in self.select_related("guild")}


class DiscordGuildEmoji(models.Model):
    """One reaction-emoji → guild mapping for the Discord role message (admin-editable).

    Many emojis may point at one guild (collapsed guilds: two Glass emojis → one Glass
    Guild). An emoji with no row is ignored in both directions.
    """

    emoji = models.CharField(
        max_length=64,
        help_text=(
            "The reaction emoji on the Discord role message: a unicode character (e.g. 🔥) or a "
            "custom emoji as name:id (e.g. PrisonOutreach:123456789). A member who reacts with this "
            "joins the guild below. Many emojis may point at one guild (collapsed guilds)."
        ),
    )
    guild = models.ForeignKey(
        Guild,
        on_delete=models.CASCADE,
        related_name="discord_emojis",
        help_text="The guild a reaction with this emoji joins the member to.",
    )

    objects = DiscordGuildEmojiManager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["emoji"], name="uq_discordguildemoji_emoji")]
        ordering = ["guild__name", "emoji"]

    def __str__(self) -> str:
        return f"{self.emoji} → {self.guild.name}"


class DiscordLinkNudge(models.Model):
    """The one-time "you're halfway there" DM guard for the reaction sync.

    A Discord user who reacts on the role message but has no linked member gets ONE DM
    telling them their guild pick won't count until they link their Past Lives account.
    A row here means that nudge was handled for that Discord user — sent, or skipped
    because their DMs are closed — and must never be sent again, even if they later
    link and unlink. Rows are written only by :func:`membership.discord_sync.reconcile_reactions`.
    """

    discord_user_id = models.CharField(
        max_length=32,
        help_text=(
            "The Discord account id (snowflake) of the reactor who was nudged to link their Past Lives "
            "account. One nudge ever per Discord user."
        ),
    )
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="When the nudge DM was sent (or skipped because the user's DMs are closed)."
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["discord_user_id"], name="uq_discordlinknudge_discord_user_id"),
        ]

    def __str__(self) -> str:
        return f"Link nudge → {self.discord_user_id}"


class SkillCategory(models.Model):
    """A grouping of related skills shown in the skills picker and directory filter."""

    name = models.CharField(max_length=100, unique=True, help_text="Display name of the category.")
    slug = models.SlugField(max_length=120, unique=True, help_text="URL-safe identifier.")
    sort_order = models.PositiveSmallIntegerField(default=0, help_text="Lower numbers sort first in pickers.")

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "skill category"
        verbose_name_plural = "skill categories"

    def __str__(self) -> str:
        return self.name


class Skill(models.Model):
    """A single skill members can list, drawn from a curated vocabulary."""

    class Status(models.TextChoices):
        APPROVED = "approved", "Approved"
        PENDING = "pending", "Pending review"

    name = models.CharField(max_length=80, unique=True, help_text="Canonical skill name shown everywhere.")
    slug = models.SlugField(max_length=100, unique=True, help_text="URL-safe identifier used for filtering.")
    category = models.ForeignKey(
        SkillCategory,
        on_delete=models.PROTECT,
        related_name="skills",
        help_text="The category this skill belongs to.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.APPROVED,
        help_text="Approved skills appear publicly; pending skills are member suggestions awaiting review.",
    )
    suggested_by = models.ForeignKey(
        "Member",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="suggested_skills",
        help_text="Member who proposed this skill, if it came from a suggestion.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the skill entered the vocabulary.")

    class Meta:
        ordering = ["category__sort_order", "name"]
        indexes = [
            models.Index(fields=["status", "name"], name="idx_skill_status_name"),
        ]

    def __str__(self) -> str:
        return self.name


class MemberSkill(models.Model):
    """A skill claimed by a member, with optional years of experience."""

    member = models.ForeignKey(
        "Member", on_delete=models.CASCADE, related_name="skills", help_text="The member who listed this skill."
    )
    skill = models.ForeignKey(
        Skill, on_delete=models.CASCADE, related_name="member_links", help_text="The skill being claimed."
    )
    years_experience = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Optional years of experience, shown beside the skill when set.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the member added this skill.")

    class Meta:
        ordering = ["skill__category__sort_order", "skill__name"]
        constraints = [
            models.UniqueConstraint(fields=["member", "skill"], name="uq_memberskill_member_skill"),
        ]

    def __str__(self) -> str:
        years = f" ({self.years_experience}y)" if self.years_experience is not None else ""
        return f"{self.member.display_name} — {self.skill.name}{years}"


class CommunityEventQuerySet(models.QuerySet):
    """Window + scope queries for FOG-authored community events."""

    def upcoming(self) -> CommunityEventQuerySet:
        """Events still worth showing: a non-recurring event that hasn't ended, or
        ANY monthly series (it keeps recurring — its concrete future occurrences are
        computed by :meth:`CommunityEvent.occurrences_in` at render time)."""
        return self.filter(Q(ends_at__gte=timezone.now()) | ~Q(recurrence=CommunityEvent.Recurrence.NONE))

    def candidates_for_window(self, frm: date_type, to: date_type) -> CommunityEventQuerySet:
        """Rows that *might* contribute an occurrence to ``[frm, to]``.

        A non-recurring event qualifies when its start-date is in-window; a monthly
        series qualifies whenever its anchor is on/before ``to`` (its later occurrences
        are expanded virtually, so a past anchor still counts). The adapter then asks
        each row's :meth:`CommunityEvent.occurrences_in` which concrete dates land in
        the window — a plain ``starts_at`` BETWEEN filter would wrongly drop a series.
        """
        return self.filter(
            (Q(recurrence=CommunityEvent.Recurrence.NONE) & Q(starts_at__date__gte=frm, starts_at__date__lte=to))
            | (~Q(recurrence=CommunityEvent.Recurrence.NONE) & Q(starts_at__date__lte=to))
        )

    def studio_hours(self) -> CommunityEventQuerySet:
        """Only the standing STUDIO_HOURS rows (drives the editor formset queryset)."""
        return self.filter(event_type=CommunityEvent.EventType.STUDIO_HOURS)

    def meetings(self) -> CommunityEventQuerySet:
        """Only the GUILD_MEETING rows (excludes studio hours + site-wide events)."""
        return self.filter(event_type=CommunityEvent.EventType.GUILD_MEETING)

    def for_guild(self, guild: Guild) -> CommunityEventQuerySet:
        return self.filter(guild=guild)

    def site_wide(self) -> CommunityEventQuerySet:
        return self.filter(guild__isnull=True)

    def for_member(self, member: "Member") -> CommunityEventQuerySet:
        """Site-wide events plus events from the guilds this member has joined.

        The personalized home feed: a member sees every makerspace-wide community/lead
        event and the meetings of guilds they belong to, but not other guilds' meetings.
        """
        return self.filter(Q(guild__isnull=True) | Q(guild__memberships__member=member))

    def published(self) -> CommunityEventQuerySet:
        """Only events live on the calendar (eligible to push to Google)."""
        return self.filter(moderation_state=CommunityEvent.ModerationState.PUBLISHED)

    def awaiting_review(self) -> CommunityEventQuerySet:
        """Member proposals waiting on a reviewer decision."""
        return self.filter(moderation_state=CommunityEvent.ModerationState.PENDING)

    def scheduled(self) -> CommunityEventQuerySet:
        """Parked-but-not-yet-announced events (the admin management list, §6)."""
        return self.filter(moderation_state=CommunityEvent.ModerationState.SCHEDULED)

    def due_to_publish(self, now: datetime_type) -> CommunityEventQuerySet:
        """SCHEDULED events whose publish_at has arrived (the deferred-publish set).

        A NULL ``publish_at`` never satisfies ``publish_at__lte``, so a schedule that was
        cleared can never sit here waiting — it's published immediately at edit time instead.
        """
        return self.filter(moderation_state=CommunityEvent.ModerationState.SCHEDULED, publish_at__lte=now)

    def pushed(self) -> CommunityEventQuerySet:
        """Rows FOG has pushed to Google (their iCal echo must be de-duped)."""
        return self.exclude(google_ical_uid="")

    def needs_push(self) -> CommunityEventQuerySet:
        """Published rows whose Google sync is pending or failed (the retry set)."""
        return self.published().filter(
            sync_state__in=[CommunityEvent.SyncState.PENDING, CommunityEvent.SyncState.FAILED]
        )

    def needs_discord_push(self) -> CommunityEventQuerySet:
        """Published, non-studio-hours rows whose Discord sync is pending or failed (the retry set).

        Excludes STUDIO_HOURS at the query level so the "ambient hours never become Scheduled
        Events" rule is enforced in one place, not just at the push call site.
        """
        return (
            self.published()
            .exclude(event_type=CommunityEvent.EventType.STUDIO_HOURS)
            .filter(discord_sync_state__in=[CommunityEvent.SyncState.PENDING, CommunityEvent.SyncState.FAILED])
        )

    def needs_discord_rollforward(self, now: datetime_type) -> CommunityEventQuerySet:
        """SYNCED single-occurrence events (unmappable cadence) whose pushed occurrence has passed.

        A natively-recurring event has a NULL ``discord_pushed_occurrence`` (Discord shows its
        next instance itself), so it never enters this roll-forward set.
        """
        return self.published().filter(
            discord_sync_state=CommunityEvent.SyncState.SYNCED,
            discord_pushed_occurrence__isnull=False,
            discord_pushed_occurrence__lt=now,
        )


class InvalidEventTransition(ValueError):
    """Raised when a :class:`CommunityEvent` lifecycle method is called from a state
    that does not permit it (e.g. approving an already-declined proposal)."""


class CommunityEvent(models.Model):
    """A FOG-native event on the Community Calendar (a guild meeting/event, a site-wide
    community event, or the cross-guild Guild Lead Meeting).

    Unlike :class:`CalendarEvent` (a read-only iCal cache), this is authored inside FOG
    by a guild lead/staffer (their guild's events) or an admin (site-wide events and the
    Guild Lead Meeting). Saving a *new* event posts a one-shot Discord/in-app announcement
    via :meth:`announce`; editing does not re-announce. A monthly event is a single row —
    its occurrences are expanded virtually in the calendar window and emitted as one
    ``RRULE`` VEVENT in the ``.ics`` (no materialised rows, no cron).
    """

    class EventType(models.TextChoices):
        GUILD_MEETING = "guild_meeting", "Guild meeting / event"
        LEAD_MEETING = "lead_meeting", "Guild Lead Meeting"
        COMMUNITY = "community", "Community event"
        STUDIO_HOURS = "studio_hours", "Studio hours"

    class Recurrence(models.TextChoices):
        NONE = "none", "Does not repeat"
        WEEKLY = "weekly", "Every week"
        SEMI_MONTHLY = "semi_monthly", "Twice a month"
        MONTHLY = "monthly", "Every month"
        EVERY_2_MONTHS = "every_2_months", "Every 2 months"
        EVERY_3_MONTHS = "every_3_months", "Every 3 months"
        EVERY_6_MONTHS = "every_6_months", "Every 6 months"
        YEARLY = "yearly", "Every year"

    class ModerationState(models.TextChoices):
        PUBLISHED = "published", "Published"  # live on the calendar; eligible to push
        SCHEDULED = (
            "scheduled",
            "Scheduled",
        )  # approved/authored; auto-publishes at publish_at (not yet announced/pushed)
        PENDING = "pending", "Pending review"  # member proposal awaiting a decision; FOG-only
        CHANGES_REQUESTED = "changes_requested", "Changes requested"  # sent back to the proposer to edit + resubmit
        DECLINED = "declined", "Declined"  # rejected; never pushes (removed from Google if it was)

    class SyncState(models.TextChoices):
        IDLE = "idle", "Not synced"  # nothing to push yet (unpublished, or a pre-existing/unmanaged row)
        PENDING = "pending", "Pending"  # published and awaiting its push / re-push
        SYNCED = "synced", "Synced"  # pushed to Google successfully
        FAILED = "failed", "Failed"  # last push errored (retry_calendar_pushes will re-try)

    class GoogleCalendarTarget(models.TextChoices):
        MEMBER = "member", "Member calendar"  # members-only makerspace calendar (the former "General")
        PUBLIC = "public", "Public calendar"  # the outward-facing calendar anyone can see

    # Months between occurrences for each recurring choice (semi-monthly walks
    # monthly but emits two dates per month — see ``occurrences_in``).
    _MONTH_INTERVALS: dict[str, int] = {
        Recurrence.SEMI_MONTHLY.value: 1,
        Recurrence.MONTHLY.value: 1,
        Recurrence.EVERY_2_MONTHS.value: 2,
        Recurrence.EVERY_3_MONTHS.value: 3,
        Recurrence.EVERY_6_MONTHS.value: 6,
        Recurrence.YEARLY.value: 12,
    }

    # Maps an event type to the registry event key its announcement fires.
    _ANNOUNCE_EVENT: dict[str, str] = {
        EventType.GUILD_MEETING: "event.guild_published",
        EventType.LEAD_MEETING: "event.lead_meeting_published",
        EventType.COMMUNITY: "event.community_published",
    }

    # (toggle field, days-before) pairs for the opt-in reminder pings. One place to add
    # an offset later; drives ``enabled_reminder_offsets`` and the reminder source.
    REMINDER_OFFSETS: list[tuple[str, int]] = [("remind_7d", 7), ("remind_3d", 3), ("remind_1d", 1)]

    title = models.CharField(max_length=200, help_text="Event name shown on the calendar — e.g. 'Monthly Potluck'.")
    event_type = models.CharField(
        max_length=20,
        choices=EventType.choices,
        default=EventType.GUILD_MEETING,
        help_text="What kind of event this is.",
    )
    guild = models.ForeignKey(
        Guild,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="events",
        help_text="The guild this belongs to. Leave blank for a site-wide community or leadership event.",
    )
    starts_at = models.DateTimeField(help_text="When the event starts.")
    ends_at = models.DateTimeField(help_text="When the event ends.")
    location = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Where it happens — a room name, address, or a video link. Optional.",
    )
    description = models.TextField(blank=True, default="", help_text="Optional details for members.")
    recurrence = models.CharField(
        max_length=20,
        choices=Recurrence.choices,
        default=Recurrence.NONE,
        help_text=(
            "Whether and how often this event repeats. 'Every week' repeats on the same weekday; "
            "the monthly options recur on the same weekday-of-month as the start (e.g. the 2nd "
            "Saturday); 'Twice a month' adds the same weekday two weeks later."
        ),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who created this event.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # --- Moderation (member-proposal review lifecycle) ------------------------
    moderation_state = models.CharField(
        max_length=20,
        choices=ModerationState.choices,
        default=ModerationState.PUBLISHED,
        help_text=(
            "Where this event is in the review flow. Leads/staff/admins create events already "
            "Published; member proposals start Pending."
        ),
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The member who proposed this event (for events that went through review).",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The lead or admin who approved, declined, or requested changes.",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, help_text="When the review decision was recorded.")
    review_notes = models.TextField(
        blank=True,
        default="",
        help_text="The reviewer's note to the proposer (shown on a decline or a changes-requested).",
    )

    # --- Google Calendar sync -------------------------------------------------
    google_calendar_target = models.CharField(
        max_length=10,
        choices=GoogleCalendarTarget.choices,
        default=GoogleCalendarTarget.MEMBER,
        verbose_name="Which calendar",
        help_text="Which Google calendar this event syncs to when Google sync is on — the members-only or the public one.",
    )
    google_event_id = models.CharField(
        max_length=1024,
        blank=True,
        default="",
        help_text="The Google Calendar event id returned when FOG pushed this event. Blank until pushed.",
    )
    google_calendar_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Which Google calendar this event was pushed to (kept so a later edit/delete targets the right one).",
    )
    google_ical_uid = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text=(
            "The pushed event's iCal UID (<id>@google.com), used to hide the echoed copy when the "
            "daily iCal read re-imports it."
        ),
    )
    sync_state = models.CharField(
        max_length=12,
        choices=SyncState.choices,
        default=SyncState.IDLE,
        help_text="Google Calendar sync status for this event.",
    )
    sync_error = models.TextField(
        blank=True,
        default="",
        help_text="Why the last Google push failed (or why it's still pending, e.g. no calendar linked). Blank when synced.",
    )
    synced_at = models.DateTimeField(null=True, blank=True, help_text="When this event last synced to Google.")

    # --- Discord Scheduled Events sync ----------------------------------------
    discord_event_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "The Discord Scheduled Event id returned when FOG pushed this event to the server's Events. "
            "Blank until pushed."
        ),
    )
    discord_sync_state = models.CharField(
        max_length=12,
        choices=SyncState.choices,
        default=SyncState.IDLE,
        help_text="Discord Events sync status for this event (independent of the Google sync state).",
    )
    discord_sync_error = models.TextField(
        blank=True,
        default="",
        help_text="Why the last Discord push failed (or why it's still pending, e.g. sync off). Blank when synced.",
    )
    discord_synced_at = models.DateTimeField(null=True, blank=True, help_text="When this event last synced to Discord.")
    discord_pushed_occurrence = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "For an unmappable-cadence event pushed to Discord as a single event, the start of the "
            "occurrence currently live there — so the nightly roll-forward knows when it has passed. "
            "Blank for one-off and natively-recurring events."
        ),
    )
    channel_announced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When this event was announced (or silently marked announced) in the Discord calendar "
            "channel. NULL = not yet announced; the 15-minute announcer picks it up once published."
        ),
    )

    # --- Seed import bookkeeping ----------------------------------------------
    import_source_uid = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text=(
            "The iCal UID of the Public-Calendar VEVENT this row was seeded from. Set only by the "
            "one-time seed importer; used to re-run it without duplicating. Blank for app-authored rows."
        ),
    )

    # --- Announcement scheduling + reminders ----------------------------------
    publish_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When to announce this event. Leave blank to announce as soon as it's saved.",
    )
    remind_7d = models.BooleanField(default=False, help_text="Send members a reminder 7 days before it starts.")
    remind_3d = models.BooleanField(default=False, help_text="Send members a reminder 3 days before it starts.")
    remind_1d = models.BooleanField(default=False, help_text="Send members a reminder 1 day before it starts.")
    notify_happening_now = models.BooleanField(default=False, help_text="Ping members when it starts.")

    objects = CommunityEventQuerySet.as_manager()

    class Meta:
        ordering = ["starts_at"]
        indexes = [models.Index(fields=["starts_at"], name="idx_communityevent_starts")]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=models.F("starts_at")),
                name="ck_communityevent_end_after_start",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(event_type__in=["guild_meeting", "studio_hours"]) & Q(guild__isnull=False))
                    | (~Q(event_type__in=["guild_meeting", "studio_hours"]) & Q(guild__isnull=True))
                ),
                name="ck_communityevent_guild_matches_type",
            ),
            models.UniqueConstraint(
                fields=["import_source_uid"],
                condition=~Q(import_source_uid=""),
                name="uq_communityevent_import_source_uid",
            ),
        ]

    def __str__(self) -> str:
        where = self.guild.name if self.guild is not None else "Site-wide"
        return f"{self.title} — {where} ({self.starts_at:%Y-%m-%d %H:%M})"

    # --- Recurrence (virtual expansion, reusing _nth_weekday) -----------------

    def _occurrence_ordinal(self) -> int:
        """Which weekday-of-month the start falls on: 1–4, or -1 for a 5th (treated as 'last')."""
        n = (timezone.localtime(self.starts_at).day - 1) // 7 + 1
        return -1 if n == 5 else n

    def occurrences_in(self, frm: date_type, to: date_type) -> list[datetime_type]:
        """Start datetimes of every occurrence whose start-date is within ``[frm, to]``.

        ``NONE`` yields ``[starts_at]`` when in window; ``MONTHLY`` walks months from the
        anchor, projecting the same nth weekday via :func:`_nth_weekday`, preserving the
        original time-of-day (and therefore the duration). Each occurrence's end is
        ``occurrence start + (ends_at - starts_at)`` (computed by the caller).
        """
        local_start = timezone.localtime(self.starts_at)
        if self.recurrence == self.Recurrence.NONE:
            return [self.starts_at] if frm <= local_start.date() <= to else []

        if self.recurrence == self.Recurrence.WEEKLY:
            # Weekly is not month-anchored, so it is expanded before the monthly machinery.
            # Walk from the first matching weekday on/after ``frm`` (never before the anchor),
            # advancing 7 days at a time and rebuilding each occurrence in local time so a
            # DST boundary keeps the wall-clock time (mirrors the monthly ``replace`` below).
            anchor_date = local_start.date()
            start_date = max(anchor_date, frm)
            days_ahead = (local_start.weekday() - start_date.weekday()) % 7
            cursor = start_date + timedelta(days=days_ahead)
            weekly_occurrences: list[datetime_type] = []
            while cursor <= to:
                weekly_occurrences.append(local_start.replace(year=cursor.year, month=cursor.month, day=cursor.day))
                cursor = cursor + timedelta(days=7)
            return weekly_occurrences

        from dateutil.relativedelta import FR, MO, SA, SU, TH, TU, WE, relativedelta

        weekdays = (MO, TU, WE, TH, FR, SA, SU)
        wd = weekdays[local_start.weekday()]
        ord_ical = self._occurrence_ordinal()
        ordinal = 5 if ord_ical == -1 else ord_ical  # _nth_weekday treats ordinal 5 as 'last'
        anchor_date = local_start.date()
        interval = self._MONTH_INTERVALS[self.recurrence]
        is_semi_monthly = self.recurrence == self.Recurrence.SEMI_MONTHLY

        # Align the first iterated month to the interval grid relative to the anchor, so a
        # far-future window doesn't walk month-by-month from a long-past anchor.
        months_to_window = (frm.year - anchor_date.year) * 12 + (frm.month - anchor_date.month)
        steps = max(0, -(-months_to_window // interval))  # ceil division, clamped at 0
        month_cursor = anchor_date.replace(day=1) + relativedelta(months=steps * interval)

        occurrences: list[datetime_type] = []
        while month_cursor <= to:
            occ_date = _nth_weekday(month_cursor, wd, ordinal)
            candidates = [occ_date, occ_date + relativedelta(weeks=2)] if is_semi_monthly else [occ_date]
            for occ in candidates:
                if occ >= anchor_date and frm <= occ <= to:
                    occurrences.append(local_start.replace(year=occ.year, month=occ.month, day=occ.day))
            month_cursor = month_cursor + relativedelta(months=interval)
        return occurrences

    def ical_rrule(self) -> str:
        """The iCal ``RRULE`` body (no ``RRULE:`` prefix) for this event, or ``""`` for none.

        Subscribed calendars expand this themselves, so the export emits one VEVENT per
        series instead of a row per occurrence. The monthly family recurs on the same nth
        weekday (with an ``INTERVAL`` for every-N-months); yearly pins the month too;
        twice-a-month lists the nth and nth+2 weekday when both fit in a month.
        """
        if self.recurrence == self.Recurrence.NONE:
            return ""
        local_start = timezone.localtime(self.starts_at)
        weekday = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")[local_start.weekday()]
        if self.recurrence == self.Recurrence.WEEKLY:
            # Weekly is not month-anchored — a plain BYDAY off the local start weekday.
            # This is what the Google push-out (Part C) emits for studio hours + weekly meetings.
            return f"FREQ=WEEKLY;BYDAY={weekday}"
        ordinal = self._occurrence_ordinal()
        if self.recurrence == self.Recurrence.YEARLY:
            return f"FREQ=YEARLY;BYMONTH={local_start.month};BYDAY={ordinal}{weekday}"
        if self.recurrence == self.Recurrence.SEMI_MONTHLY:
            second = ordinal + 2
            byday = f"{ordinal}{weekday},{second}{weekday}" if 1 <= ordinal <= 3 else f"{ordinal}{weekday}"
            return f"FREQ=MONTHLY;BYDAY={byday}"
        interval = self._MONTH_INTERVALS[self.recurrence]
        interval_part = "" if interval == 1 else f"INTERVAL={interval};"
        return f"FREQ=MONTHLY;{interval_part}BYDAY={ordinal}{weekday}"

    def ics_vevent_lines(self) -> list[str]:
        """The iCal ``VEVENT`` lines (``BEGIN:VEVENT`` … ``END:VEVENT``) for this event.

        Shared by the per-event :meth:`ics_document` and the combined
        ``hub.views.calendar_export_ics`` loop so the two never drift. A recurring
        series emits ONE ``RRULE`` (subscribers expand it themselves — no per-occurrence
        VEVENTs); ``DESCRIPTION``/``LOCATION`` are RFC-5545 escaped.
        """
        from membership.ical import ical_escape

        lines = [
            "BEGIN:VEVENT",
            f"UID:community-{self.pk}@pastlives",
            f"SUMMARY:{ical_escape(self.title)}",
            f"DTSTART:{self.starts_at.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{self.ends_at.strftime('%Y%m%dT%H%M%SZ')}",
        ]
        rrule = self.ical_rrule()
        if rrule:
            lines.append(f"RRULE:{rrule}")
        if self.description:
            lines.append(f"DESCRIPTION:{ical_escape(self.description[:250])}")
        if self.location:
            lines.append(f"LOCATION:{ical_escape(self.location)}")
        lines.append("END:VEVENT")
        return lines

    def ics_document(self) -> str:
        """A standalone single-``VEVENT`` ``VCALENDAR`` string for this event's public
        "Add to calendar" download. Reuses :meth:`ics_vevent_lines`, so the per-event
        add and the combined calendar export always agree."""
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Past Lives Makerspace//Community Calendar//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            *self.ics_vevent_lines(),
            "END:VCALENDAR",
        ]
        return "\r\n".join(lines) + "\r\n"

    # --- Properties -----------------------------------------------------------

    @property
    def is_site_wide(self) -> bool:
        return self.guild_id is None

    @property
    def when_display(self) -> str:
        """'Sat, Jul 12 · 6:00 PM – 8:00 PM' style, for notification copy and tooltips.

        Appends the recurrence label (e.g. ' · Every week') for a repeating series so the
        single launch announcement makes the cadence clear.
        """
        local_start = timezone.localtime(self.starts_at)
        local_end = timezone.localtime(self.ends_at)
        when = (
            f"{local_start.strftime('%a, %b %-d')} · "
            f"{local_start.strftime('%-I:%M %p')} – {local_end.strftime('%-I:%M %p')}"
        )
        if self.recurrence != self.Recurrence.NONE:
            when += f" · {self.get_recurrence_display()}"
        return when

    @property
    def publish_at_display(self) -> str:
        """Local-time 'Jul 12, 2026 · 6:00 PM' for the scheduled announcement time.

        Empty string when nothing is scheduled — safe to drop straight into a message or
        a "Scheduled for …" badge.
        """
        if self.publish_at is None:
            return ""
        local = timezone.localtime(self.publish_at)
        return f"{local.strftime('%b %-d, %Y')} · {local.strftime('%-I:%M %p')}"

    def enabled_reminder_offsets(self) -> list[int]:
        """Days-before values whose reminder toggle is on (e.g. ``[7, 1]``)."""
        return [days for attr, days in self.REMINDER_OFFSETS if getattr(self, attr)]

    @property
    def public_url(self) -> str:
        """Absolute URL of this event's public detail page.

        The page is reachable logged-out, so a QR scanned off a flyer or the wall
        signage resolves for anyone. Events have no slug (title only), so the
        pk-based URL is inherently stable.
        """
        from django.urls import reverse

        return f"{settings.MEMBER_BASE_URL}{reverse('hub_event_detail', args=[self.pk])}"

    @property
    def qr_url(self) -> str:
        """The URL the QR encodes — the public page directly.

        Events have no slug, so the pk URL is already stable and needs no slug-proof
        permalink redirect (unlike :class:`~classes.models.ClassOffering`). Kept as a
        property for API parity with the class QR helpers.
        """
        return self.public_url

    @property
    def absolute_url(self) -> str:
        """Absolute URL for notifications, signage, and calendar links — the event's own page."""
        return self.public_url

    def qr_svg(self) -> str:
        """Inline, CSS-scalable SVG QR of the event's public page (crisp at any print size)."""
        from membership.qr import qr_svg as render_qr

        return render_qr(self.qr_url)

    def qr_png_bytes(self) -> bytes:
        """PNG bytes of the same QR — a raster download for print/handout."""
        from membership.qr import qr_png_bytes as render_png

        return render_png(self.qr_url)

    # --- Publish --------------------------------------------------------------

    def _announce_context(self) -> dict[str, Any]:
        """The resolver/copy context shared by the launch announcement and any audience email.

        One source of truth for the emit context so :meth:`announce` and
        :meth:`email_announcement` render byte-identical copy and resolve the same audience.
        """
        return {
            "guild": self.guild,
            "guild_name": self.guild.name if self.guild is not None else "",
            "event_title": self.title,
            "when": self.when_display,
            "location": self.location,
            "event_url": self.absolute_url,
        }

    def announce(self, *, actor: User | None = None) -> None:
        """Post the launch announcement (in-app + Discord). Idempotent via ``period``.

        Called only on create. The ``guild`` rides in context for the guild-members
        resolver and the per-guild Discord fan-out (sibling routing spec); site-wide
        events carry ``guild=None`` and route centrally only.

        STUDIO_HOURS rows are ambient standing hours, not an event to ping members about,
        so they never announce (they are also absent from ``_ANNOUNCE_EVENT``) — this guard
        makes ``publish()`` a safe no-op for the announce step on a studio-hours row.
        """
        if self.event_type == self.EventType.STUDIO_HOURS:
            return
        from core.events.emit import emit

        emit(
            self._ANNOUNCE_EVENT[self.event_type],
            actor=actor,
            target=self,
            context=self._announce_context(),
            url=self.absolute_url,
            period=f"event:{self.pk}:published",
        )

    def email_announcement(self, audience: str, *, actor: User | None = None) -> int:
        """Email this published event's launch announcement to a chosen audience.

        Event emails are OFF by default (calendar + Discord + the in-app bell is enough), so
        the launch :meth:`announce` reaches only members who opted into event emails. This is
        the deliberate "also email everyone" escalation the ``/create-event`` Discord command
        offers: it resolves the chosen audience, then hands their addresses to
        ``emit(email_to=...)`` — which delivers the EMAIL channel to explicit addresses
        regardless of the per-member email preference. Discord is suppressed and the in-app /
        push fan-out is deduped against the launch (same ``period``), so only the extra emails
        actually go out — nothing double-posts.

        The address list is also deduped against the launch email itself: a member who opted
        into event emails already received the launch :meth:`announce` email through the normal
        per-member fan-out, and the explicit ``email_to`` send is keyed by address (not user), so
        without this exclusion they would receive two copies. Any recipient for whom the launch
        event already enables the EMAIL channel is therefore dropped from ``addresses``, leaving
        only the members this escalation genuinely adds.

        Args:
            audience: ``"guild_members"`` (this event's guild roster; empty for a site-wide
                event) or ``"all_active"`` (every active, signed-in member).
            actor: The member's user, recorded on the (idempotent) delivery ledger.

        Returns:
            The number of distinct addresses emailed (``0`` when the audience is empty).

        Raises:
            ValueError: If ``audience`` is not one of the two known values (fail loudly).
        """
        if self.event_type == self.EventType.STUDIO_HOURS:
            return 0
        from core.events import preferences, resolvers
        from core.events.emit import emit
        from core.events.registry import Channel, Recipients

        event_key = self._ANNOUNCE_EVENT[self.event_type]
        if audience == "guild_members":
            recipients = (
                resolvers.resolve(Recipients.GUILD_MEMBERS, {"guild": self.guild}) if self.guild is not None else []
            )
        elif audience == "all_active":
            recipients = resolvers.resolve(Recipients.ALL_ACTIVE_MEMBERS, {})
        else:
            raise ValueError(f"Unknown event email audience: {audience!r}")

        addresses = sorted(
            {
                email
                for user, _reason in recipients
                if (email := (user.email or "").strip())
                and Channel.EMAIL not in preferences.enabled_channels(user, event_key)
            }
        )
        if not addresses:
            return 0
        emit(
            event_key,
            actor=actor,
            target=self,
            context=self._announce_context(),
            url=self.absolute_url,
            period=f"event:{self.pk}:published",
            email_to=addresses,
            suppress_broadcast=True,
        )
        return len(addresses)

    # --- Review lifecycle (member proposals) ----------------------------------

    def publish(self, *, actor: User | None = None) -> None:
        """Make a PUBLISHED event live everywhere: the single "it's live now" choke point.

        Fires the one-shot announcement (idempotent via its ``period``), marks the event as
        needing a Google push (``IDLE`` → ``PENDING``), then pushes it to the linked Google
        Calendar, and likewise marks + pushes it to the Discord server's Scheduled Events
        (both best-effort — an outage records ``FAILED`` and never blocks this call; the
        Discord push self-gates to a no-op for studio hours and when Discord Events sync is
        off). Called by :meth:`approve` and by the direct-create views.
        """
        self.announce(actor=actor)
        if self.sync_state == self.SyncState.IDLE:
            self.sync_state = self.SyncState.PENDING
            self.save(update_fields=["sync_state", "updated_at"])
        self.push_to_google(actor=actor)
        if self.discord_sync_state == self.SyncState.IDLE:
            self.discord_sync_state = self.SyncState.PENDING
            self.save(update_fields=["discord_sync_state", "updated_at"])
        self.push_to_discord(actor=actor)

    def schedule_or_go_live(self, *, actor: User | None = None) -> None:
        """Publish now, or park until ``publish_at``. The single create/approve entry point.

        Future ``publish_at`` ⇒ ``moderation_state=SCHEDULED``, no announce/push (the cron
        promotes it via :meth:`publish_scheduled`). Blank/past ``publish_at`` ⇒
        ``moderation_state=PUBLISHED`` + :meth:`publish` (announce + Google push), as today.

        Idempotent for a still-parked event: re-saving a ``SCHEDULED`` row with an
        unchanged future ``publish_at`` re-sets ``SCHEDULED`` and does not announce.
        """
        if self.publish_at is not None and self.publish_at > timezone.now():
            self.moderation_state = self.ModerationState.SCHEDULED
            self.save(update_fields=["moderation_state", "updated_at"])
            return
        self.moderation_state = self.ModerationState.PUBLISHED
        self.save(update_fields=["moderation_state", "updated_at"])
        self.publish(actor=actor)

    def publish_scheduled(self, *, actor: User | None = None) -> None:
        """Promote a due SCHEDULED event to live (announce + Google push). Cron-facing.

        Raises:
            InvalidEventTransition: If not currently SCHEDULED.
        """
        if self.moderation_state != self.ModerationState.SCHEDULED:
            raise InvalidEventTransition(f"Cannot publish an event in state '{self.moderation_state}'.")
        self.moderation_state = self.ModerationState.PUBLISHED
        self.save(update_fields=["moderation_state", "updated_at"])
        self.publish(actor=actor)

    # --- Google Calendar sync (delegates to core.integrations) ----------------

    def push_to_google(self, *, actor: User | None = None) -> None:
        """Push this event to its linked Google Calendar and persist the sync fields.

        Best-effort: the service records ``PENDING``/``FAILED`` instead of raising, so a
        Google outage never rolls back the FOG save. Lazy-imports the service to keep the
        ``membership → core`` layering clean (like :meth:`announce`).
        """
        from core.integrations.google_calendar import push_community_event

        push_community_event(self, actor=actor)
        self.save(
            update_fields=[
                "google_event_id",
                "google_calendar_id",
                "google_ical_uid",
                "sync_state",
                "sync_error",
                "synced_at",
                "updated_at",
            ]
        )

    def remove_from_google(self) -> None:
        """Delete this event from Google (best-effort). Call BEFORE deleting the FOG row —
        it needs the stored ``google_event_id``/``google_calendar_id``. Never raises."""
        from core.integrations.google_calendar import remove_community_event

        remove_community_event(self)

    # --- Discord Scheduled Events sync (delegates to core.integrations) --------

    def push_to_discord(self, *, actor: User | None = None) -> None:
        """Push this event to the Discord server's Scheduled Events and persist the sync fields.

        Best-effort: the service records ``IDLE``/``PENDING``/``FAILED`` instead of raising, so
        a Discord outage never rolls back the FOG save. A self-gating no-op for studio hours
        and when Discord Events sync is off. Lazy-imports the service to keep the
        ``membership → core`` layering clean (like :meth:`push_to_google`).
        """
        from core.integrations.discord_events import push_community_event

        push_community_event(self, actor=actor)
        self.save(
            update_fields=[
                "discord_event_id",
                "discord_sync_state",
                "discord_sync_error",
                "discord_synced_at",
                "discord_pushed_occurrence",
                "updated_at",
            ]
        )

    def remove_from_discord(self) -> None:
        """Delete this event from the Discord server's Scheduled Events (best-effort). Call
        BEFORE deleting the FOG row — it needs the stored ``discord_event_id``. Never raises."""
        from core.integrations.discord_events import remove_community_event

        remove_community_event(self)

    def propose(self, *, by: User, guild: Guild | None, policy: str, editing: bool) -> bool:
        """Route a member-proposed event to publication or the review queue.

        Owns the member-facing create/resubmit logic that the "Propose an event" view used
        to carry inline: derive ``event_type`` from the guild, attribute a brand-new
        proposal to ``by``, then branch on the site's member-event ``policy`` — an OPEN
        policy publishes a new proposal immediately (announce + Google push), any other
        policy enters the review queue. An edit always re-submits for review (a
        changes-requested proposal returns to Pending). The instance must already carry the
        form's field values (title/time/etc.); the caller enforces the DISABLED policy gate.

        Args:
            by: The proposing member's user (the create actor + review submitter).
            guild: The target guild, or ``None`` for a site-wide community event.
            policy: The current ``SiteConfiguration.member_event_policy`` value.
            editing: True when resubmitting an owned Pending/changes-requested proposal.

        Returns:
            True if the event went live immediately, False if it was queued for review.
        """
        from core.models import SiteConfiguration

        self.guild = guild
        self.event_type = self.EventType.GUILD_MEETING if guild is not None else self.EventType.COMMUNITY
        if not editing:
            self.created_by = by
        if not editing and policy == SiteConfiguration.MemberEventPolicy.OPEN:
            self.moderation_state = self.ModerationState.PUBLISHED
            self.save()
            self.publish(actor=by)
            return True
        # On an edit, persist the form's field changes first — submit_for_review then saves
        # only the moderation fields (update_fields), so an edited title/time would otherwise
        # be dropped for an already-saved row.
        if editing:
            self.save()
        self.submit_for_review(submitted_by=by)
        return False

    def submit_for_review(self, *, submitted_by: User) -> None:
        """Enter (or re-enter) the review queue — the member proposal path.

        Used for BOTH the first submit and a resubmit after a changes-requested edit.
        Sets ``PENDING``, records the proposer, clears any prior review verdict, and
        notifies reviewers. Does NOT announce or push (a pending proposal is FOG-only).

        Raises:
            InvalidEventTransition: If the event is already published or declined.
        """
        if self.pk is not None and self.moderation_state not in (
            self.ModerationState.PENDING,
            self.ModerationState.CHANGES_REQUESTED,
        ):
            raise InvalidEventTransition(f"Cannot submit an event in state '{self.moderation_state}' for review.")
        self.moderation_state = self.ModerationState.PENDING
        self.submitted_by = submitted_by
        self.reviewed_by = None
        self.reviewed_at = None
        self.review_notes = ""
        if self.pk is None:
            self.save()
        else:
            self.save(
                update_fields=[
                    "moderation_state",
                    "submitted_by",
                    "reviewed_by",
                    "reviewed_at",
                    "review_notes",
                    "updated_at",
                ]
            )
        self._emit_submitted()

    def withdraw(self, *, by: User) -> None:
        """The proposer pulls back their own not-yet-published proposal.

        Deletes the row — it was never published, so there is no Google event or
        announcement to unwind.

        Raises:
            InvalidEventTransition: If the event is published/declined or already pushed.
        """
        if self.moderation_state not in (self.ModerationState.PENDING, self.ModerationState.CHANGES_REQUESTED) or (
            self.google_event_id
        ):
            raise InvalidEventTransition(f"Cannot withdraw an event in state '{self.moderation_state}'.")
        self.delete()

    def approve(self, *, reviewer: User) -> None:
        """Single reviewer decision → live. Records the reviewer, publishes, notifies.

        Raises:
            InvalidEventTransition: If the event is not awaiting a decision.
        """
        if self.moderation_state not in (self.ModerationState.PENDING, self.ModerationState.CHANGES_REQUESTED):
            raise InvalidEventTransition(f"Cannot approve an event in state '{self.moderation_state}'.")
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.save(update_fields=["reviewed_by", "reviewed_at", "updated_at"])
        # Approve-before-schedule: a future publish_at parks the proposal in SCHEDULED
        # (announced only when the cron promotes it); blank/past publishes it now. A member
        # can never self-publish at their chosen time — only the reviewer reaches this branch.
        self.schedule_or_go_live(actor=reviewer)
        self._emit_decision("event.approved", url=self.absolute_url, period=f"event:{self.pk}:approved")

    def request_changes(self, *, reviewer: User, notes: str) -> None:
        """Send a pending proposal back to the proposer with a note to fix + resubmit.

        Raises:
            InvalidEventTransition: If the event is not currently pending.
            ValueError: If ``notes`` is blank (a changes request must explain what to fix).
        """
        if self.moderation_state != self.ModerationState.PENDING:
            raise InvalidEventTransition(f"Cannot request changes on an event in state '{self.moderation_state}'.")
        if not (notes or "").strip():
            raise ValueError("A changes request needs a note so the proposer knows what to fix.")
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.review_notes = notes
        self.moderation_state = self.ModerationState.CHANGES_REQUESTED
        self.save(update_fields=["reviewed_by", "reviewed_at", "review_notes", "moderation_state", "updated_at"])
        from django.urls import reverse

        edit_url = f"{settings.MEMBER_BASE_URL}{reverse('hub_propose_event_edit', args=[self.pk])}"
        self._emit_decision(
            "event.changes_requested",
            url=edit_url,
            period=f"event:{self.pk}:changes:{self.reviewed_at.timestamp()}",
        )

    def decline(self, *, reviewer: User, notes: str) -> None:
        """Reject a proposal (it was never published — no Google/announce to unwind).

        Raises:
            InvalidEventTransition: If the event is not awaiting a decision.
            ValueError: If ``notes`` is blank (a decline must explain why).
        """
        if self.moderation_state not in (self.ModerationState.PENDING, self.ModerationState.CHANGES_REQUESTED):
            raise InvalidEventTransition(f"Cannot decline an event in state '{self.moderation_state}'.")
        if not (notes or "").strip():
            raise ValueError("A decline needs a note so the proposer knows why.")
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.review_notes = notes
        self.moderation_state = self.ModerationState.DECLINED
        self.save(update_fields=["reviewed_by", "reviewed_at", "review_notes", "moderation_state", "updated_at"])
        from django.urls import reverse

        propose_url = f"{settings.MEMBER_BASE_URL}{reverse('hub_propose_event')}"
        self._emit_decision("event.declined", url=propose_url, period=f"event:{self.pk}:declined")

    def _proposer_display_name(self) -> str:
        """A friendly name for the proposer, for notification copy."""
        user = self.submitted_by
        if user is None:
            return "A Past Lives member"
        full = (user.get_full_name() or "").strip()
        if full:
            return full
        member = getattr(user, "member", None)
        if member is not None and member.display_name:
            return member.display_name
        return user.get_username()

    def _emit_submitted(self) -> None:
        """Notify the guild's leadership (or admins) that a proposal awaits review.

        A fresh, timestamped ``period`` per submit round so a resubmit re-notifies
        reviewers instead of being deduped away by :func:`core.events.emit.emit`.
        """
        from django.urls import reverse

        from core.events.emit import emit

        review_url = f"{settings.MEMBER_BASE_URL}{reverse('hub_event_review_queue')}#event-{self.pk}"
        emit(
            "event.submitted",
            actor=self.submitted_by,
            target=self,
            context={
                "guild": self.guild,
                "guild_name": self.guild.name if self.guild is not None else "Site-wide",
                "event_title": self.title,
                "when": self.when_display,
                "proposer_name": self._proposer_display_name(),
                "review_url": review_url,
            },
            url=review_url,
            period=f"event:{self.pk}:submitted:{timezone.now().timestamp()}",
        )

    def _emit_decision(self, event_key: str, *, url: str, period: str) -> None:
        """Notify the proposer of a reviewer decision (approve / changes / decline).

        Builds the full superset context; each event's curated copy uses only its own
        documented placeholders (the extra keys are ignored by the safe renderer).
        """
        from django.urls import reverse

        from core.events.emit import emit

        base = settings.MEMBER_BASE_URL
        scheduled = self.moderation_state == self.ModerationState.SCHEDULED
        # The renderer does not branch (it only substitutes {{ placeholders }}), so the
        # schedule-aware line is composed here and dropped into event.approved's copy as
        # {{ outcome }} — an approved-but-SCHEDULED event must NOT read "now on the calendar".
        if scheduled:
            outcome = f"It'll be announced and added to the Community Calendar on {self.publish_at_display}."
        else:
            outcome = "It's now on the Community Calendar."
        emit(
            event_key,
            actor=self.reviewed_by,
            target=self,
            context={
                "user": self.submitted_by,
                "event_title": self.title,
                "when": self.when_display,
                "event_url": self.absolute_url,
                "edit_url": f"{base}{reverse('hub_propose_event_edit', args=[self.pk])}",
                "propose_url": f"{base}{reverse('hub_propose_event')}",
                "reviewer_notes": self.review_notes,
                "publish_at": self.publish_at_display,
                "scheduled": scheduled,
                "outcome": outcome,
            },
            url=url,
            period=period,
        )


class MeetingLockedError(Exception):
    """Raised when a mutating operation targets an approved (locked) meeting."""


class InvalidProposalTransition(ValueError):
    """Raised when a :class:`MeetingItemProposal` decision method is called from a state
    that does not permit it (e.g. approving an already-declined proposal)."""


class MeetingQuerySet(models.QuerySet):
    """Window + scope queries for the Meetings feature (upcoming / archive / attention)."""

    def upcoming(self) -> MeetingQuerySet:
        """Meetings dated today or later — drafts AND approved (an approved agenda for a
        future meeting still shows). Ordered ascending (soonest first, blank times last),
        overriding ``Meta.ordering`` so ``.first()`` is the *soonest* meeting."""
        return self.filter(scheduled_date__gte=timezone.localdate()).order_by(
            "scheduled_date", models.F("scheduled_time").asc(nulls_last=True), "pk"
        )

    def past(self) -> MeetingQuerySet:
        """Meetings dated before today (undated drafts are neither past nor upcoming)."""
        return self.filter(scheduled_date__lt=timezone.localdate())

    def archive(self) -> MeetingQuerySet:
        """Everything NOT upcoming: past meetings plus undated drafts, dated rows newest
        first, undated rows last — an interrupted create-empty-then-fill draft is always
        reachable here."""
        return self.filter(Q(scheduled_date__lt=timezone.localdate()) | Q(scheduled_date__isnull=True)).order_by(
            models.F("scheduled_date").desc(nulls_last=True), "-created_at"
        )

    def needs_attention(self) -> MeetingQuerySet:
        """Drafts an editor forgot: undated drafts + past-dated drafts still unapproved."""
        return self.filter(status=Meeting.Status.DRAFT).filter(
            Q(scheduled_date__isnull=True) | Q(scheduled_date__lt=timezone.localdate())
        )

    def for_scope(self, guild: Guild | None) -> MeetingQuerySet:
        """This guild's meetings, or (``None``) the cross-guild council meetings."""
        if guild is None:
            return self.filter(guild__isnull=True)
        return self.filter(guild=guild)

    def approved(self) -> MeetingQuerySet:
        return self.filter(status=Meeting.Status.APPROVED)

    def drafts(self) -> MeetingQuerySet:
        return self.filter(status=Meeting.Status.DRAFT)

    def visible_to(self, user: User) -> MeetingQuerySet:
        """Every meeting — all meetings are member-readable (transparency). Exists as the
        single seam should visibility ever narrow."""
        return self.all()


class Meeting(models.Model):
    """One meeting's workspace: the agenda that evolves into the minutes.

    A meeting belongs to a guild, or — with ``guild`` NULL — to the cross-guild
    council (guild leads) scope, the same convention as :class:`CommunityEvent`.
    It starts life as an editable DRAFT workspace (agenda items, attendance,
    action items, autosave) and locks into the permanent record when leadership
    approves the minutes (APPROVED). It optionally rides a :class:`CommunityEvent`
    for Google Calendar / Discord sync and reminder emails.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"

    # Queryset annotations + per-viewer flag set by the Meetings home view (§6.2).
    topic_count: int
    pending_count: int
    viewer_can_edit: bool

    guild = models.ForeignKey(
        Guild,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="meetings",
        help_text="The guild this meeting belongs to. Blank = the cross-guild council (guild leads) meeting.",
    )
    is_special = models.BooleanField(
        default=False,
        help_text="Off = the regular Monthly meeting. On = a named special meeting (Emergency, Planning…).",
    )
    special_title = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Name of the special meeting, e.g. 'Emergency'. Blank shows as 'Special'.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        help_text="Draft = editable workspace. Approved = locked minutes of record.",
    )
    scheduled_date = models.DateField(
        null=True,
        blank=True,
        help_text="The day of the meeting. Blank until scheduled — a meeting can be drafted first.",
    )
    scheduled_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Start time. Always a half-hour value from the shared dropdown; blank = time TBD.",
    )
    video_call_url = models.URLField(
        blank=True,
        default="",
        help_text="Video call link (Meet, Zoom…). Shown as a 'Join meeting' button wherever the meeting appears.",
    )
    special_notes = models.TextField(
        blank=True,
        default="",
        help_text="Sanitized HTML. The top-of-page notes box — context everyone should read before the meeting.",
    )
    other_notes = models.TextField(
        blank=True,
        default="",
        help_text="Sanitized HTML. Off-agenda discussion recorded at the bottom of the page.",
    )
    event = models.ForeignKey(
        CommunityEvent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="meetings",
        help_text="The calendar event this meeting rides for Google/Discord sync and reminders.",
    )
    event_occurrence = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "Which occurrence of a recurring event this meeting is — recurring events expand "
            "virtually, so the date pins it."
        ),
    )
    owns_event = models.BooleanField(
        default=False,
        help_text=(
            "On when the linked event was created from this workspace: it deletes with the meeting "
            "and follows date/time changes. Off for a pre-existing event that was merely linked."
        ),
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who approved (locked) the minutes.",
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the minutes were approved. Re-approving after an admin unlock re-stamps this.",
    )
    legacy_note_id = models.IntegerField(
        null=True,
        blank=True,
        editable=False,
        help_text="PK of the GuildMeetingNote this row was migrated from; migration bookkeeping only.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who created the meeting.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MeetingQuerySet.as_manager()

    class Meta:
        ordering = ["-scheduled_date", "-created_at"]
        indexes = [
            models.Index(fields=["guild", "-scheduled_date"], name="idx_%(class)s_guild_date"),
            models.Index(fields=["status"], name="idx_%(class)s_draft", condition=Q(status="draft")),
        ]
        constraints = [
            # The is_special autosave cleaner clears special_title in the same save when the
            # toggle goes off (§5.2 coupled fields) — this constraint is the backstop, never
            # the error path.
            models.CheckConstraint(
                condition=Q(is_special=True) | Q(special_title=""),
                name="ck_meeting_special_title_only_when_special",
            ),
            models.UniqueConstraint(
                fields=["legacy_note_id"],
                condition=Q(legacy_note_id__isnull=False),
                name="uq_meeting_legacy_note",
            ),
        ]

    def __str__(self) -> str:
        when = f"{self.scheduled_date:%Y-%m-%d}" if self.scheduled_date is not None else "no date"
        return f"{self.display_title} ({when})"

    @property
    def display_title(self) -> str:
        """The absorbed app's naming convention verbatim: '{scope} — {kind} Meeting'."""
        kind = ((self.special_title or "Special") if self.is_special else "Monthly") + " Meeting"
        return f"{self.scope_label} — {kind}"

    @property
    def is_locked(self) -> bool:
        return self.status == self.Status.APPROVED

    @property
    def scope_label(self) -> str:
        return self.guild.name if self.guild is not None else "Council"

    @property
    def starts_at(self) -> datetime_type | None:
        """Aware start datetime from date + time, or ``None`` while undated. A blank time
        means midnight — mirrors the ``NextMeeting.has_time`` handling."""
        if self.scheduled_date is None:
            return None
        return timezone.make_aware(datetime_type.combine(self.scheduled_date, self.scheduled_time or time_type()))

    @property
    def absolute_url(self) -> str:
        """Absolute workspace URL for notifications — the §6.0 ``hub_meeting`` route."""
        from django.urls import reverse

        return f"{settings.MEMBER_BASE_URL}{reverse('hub_meeting', args=[self.pk])}"

    @property
    def linked_occurrence_starts_at(self) -> datetime_type | None:
        """The linked occurrence's start: the event's local time-of-day on the pinned date.

        ``None`` while unlinked. Recurring events expand virtually, so the pinned
        ``event_occurrence`` date + the event's own start time IS the occurrence.
        """
        if self.event is None or self.event_occurrence is None:
            return None
        local = timezone.localtime(self.event.starts_at)
        return local.replace(
            year=self.event_occurrence.year, month=self.event_occurrence.month, day=self.event_occurrence.day
        )

    @property
    def calendar_mismatch(self) -> bool:
        """The §6.3 amber-warning predicate for the workspace's calendar line.

        A merely-linked event is never mutated by the meeting, so the warning fires
        when the meeting's start no longer matches the linked occurrence. An owned
        event auto-syncs — the only owned drift is a cleared meeting date, which
        ``sync_event`` deliberately refuses to guess about (§5.3).
        """
        if self.event is None:
            return False
        if self.owns_event:
            return self.scheduled_date is None
        return self.starts_at != self.linked_occurrence_starts_at

    def _event_location(self) -> str:
        """The linked event's location expression — the video link wins, then the guild's
        meeting location; a council meeting (no guild) must not raise."""
        return self.video_call_url or (self.guild.meeting_location if self.guild is not None else "")

    def assert_editable(self) -> None:
        """Raise :class:`MeetingLockedError` when the minutes are approved (locked).

        Called by every mutating path — approved minutes are the permanent record.
        """
        if self.is_locked:
            raise MeetingLockedError("Approved minutes are locked.")

    def approve(self, *, by: User) -> None:
        """Approve and lock the minutes: stamp, auto-decline pending proposals, notify.

        Still-pending proposals are declined first (each proposer notified — no proposal
        silently dies; withdrawn ones are skipped by the PENDING filter). The approval
        emit's period is timestamped so a re-approval after an admin unlock announces the
        corrected minutes again; the spine writes the ``meeting_approved`` activity row.

        Raises:
            MeetingLockedError: If already approved.
            ValueError: If no ``scheduled_date`` is set.
        """
        self.assert_editable()
        if self.scheduled_date is None:
            raise ValueError("Set the meeting date before approving.")
        for proposal in self.proposals.filter(state=MeetingItemProposal.State.PENDING):
            proposal.decline(reviewer=by, note="The meeting was closed before this was reviewed.")
        self.status = self.Status.APPROVED
        self.approved_by = by
        self.approved_at = timezone.now()
        self.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        from core.events.emit import emit

        key = "meeting.minutes_approved" if self.guild_id is not None else "meeting.council_minutes_approved"
        emit(
            key,
            actor=by,
            target=self,
            context={
                "guild": self.guild,
                "guild_name": self.scope_label,
                "meeting_title": self.display_title,
                "meeting_url": self.absolute_url,
            },
            url=self.absolute_url,
            period=f"meeting:{self.pk}:approved:{self.approved_at.timestamp()}",
        )

    def unlock(self, *, by: User) -> None:
        """Reopen approved minutes for editing (FOG admin only — the view enforces).

        The approval stamps are left in place as history until a re-approve overwrites
        them. No broadcast — unlocking is corrective plumbing (activity row only).

        Raises:
            ValueError: If the meeting is not currently approved.
        """
        if not self.is_locked:
            raise ValueError("Only approved minutes can be unlocked.")
        self.status = self.Status.DRAFT
        self.save(update_fields=["status", "updated_at"])
        from core.models import SiteActivity

        SiteActivity.log(SiteActivity.Kind.MEETING_UNLOCKED, actor=by, target=self)

    def create_calendar_event(self, *, by: User) -> CommunityEvent:
        """Create + publish a calendar event from the workspace and link it as owned.

        Builds the scope's event type (guild meeting / lead meeting), seeds the location
        from the video link or the guild's meeting location (a council meeting has no
        guild — the expression must not raise), and rides
        :meth:`CommunityEvent.schedule_or_go_live` — announce + Google + Discord +
        reminders all on the existing rails.

        Raises:
            MeetingLockedError: If the minutes are locked.
            ValueError: If already linked, or date/time are not both set.
        """
        self.assert_editable()
        if self.event_id is not None:
            raise ValueError("This meeting is already linked to a calendar event.")
        if self.scheduled_date is None or self.scheduled_time is None:
            raise ValueError("Set a date and time before adding the meeting to the calendar.")
        starts = timezone.make_aware(datetime_type.combine(self.scheduled_date, self.scheduled_time))
        event = CommunityEvent(
            title=self.display_title,
            event_type=(
                CommunityEvent.EventType.GUILD_MEETING
                if self.guild_id is not None
                else CommunityEvent.EventType.LEAD_MEETING
            ),
            guild=self.guild,
            starts_at=starts,
            ends_at=starts + timedelta(minutes=90),
            location=self._event_location(),
            recurrence=CommunityEvent.Recurrence.NONE,
            created_by=by,
        )
        event.save()
        event.schedule_or_go_live(actor=by)
        self.event = event
        self.event_occurrence = self.scheduled_date
        self.owns_event = True
        self.save(update_fields=["event", "event_occurrence", "owns_event", "updated_at"])
        return event

    def link_event(self, event: CommunityEvent, occurrence_date: date_type, *, by: User) -> None:
        """Link a pre-existing calendar event (never mutated by the meeting).

        ``owns_event`` stays False — a merely-linked (possibly recurring, possibly
        shared) event is never edited or deleted from the workspace.

        Raises:
            MeetingLockedError: If the minutes are locked.
            ValueError: If already linked, or ``occurrence_date`` is not one of the
                event's occurrences.
        """
        self.assert_editable()
        if self.event_id is not None:
            raise ValueError("This meeting is already linked to a calendar event.")
        if not event.occurrences_in(occurrence_date, occurrence_date):
            raise ValueError("That date is not an occurrence of the chosen event.")
        self.event = event
        self.event_occurrence = occurrence_date
        self.owns_event = False
        self.save(update_fields=["event", "event_occurrence", "owns_event", "updated_at"])

    def sync_event(self) -> None:
        """Push workspace changes through to an OWNED calendar event (else no-op).

        Recomputes title, location (a changed or cleared ``video_call_url`` propagates —
        no dead link lives on), start/end (duration preserved) and the pinned occurrence,
        then re-pushes via the event's own Google/Discord push methods (the retry cron
        covers transient failures). Never re-announces — a reschedule is a sync, not a
        new event. Clearing the meeting's date leaves the event where it was (the
        workspace shows the mismatch rather than guessing).
        """
        event = self.event
        if event is None or not self.owns_event:
            return
        event.title = self.display_title
        event.location = self._event_location()
        starts = self.starts_at
        if starts is not None:
            duration = event.ends_at - event.starts_at
            event.starts_at = starts
            event.ends_at = starts + duration
            if self.event_occurrence != self.scheduled_date:
                self.event_occurrence = self.scheduled_date
                self.save(update_fields=["event_occurrence", "updated_at"])
        event.save(update_fields=["title", "starts_at", "ends_at", "location", "updated_at"])
        event.push_to_google()
        event.push_to_discord()

    def unlink_event(self, *, by: User) -> None:
        """Drop the calendar link. Never deletes or edits the event — non-destructive by
        design (a formerly-owned event simply becomes a normal calendar event).

        Raises:
            MeetingLockedError: If the minutes are locked.
            ValueError: If no event is linked.
        """
        self.assert_editable()
        if self.event_id is None:
            raise ValueError("No calendar event is linked.")
        self.event = None
        self.event_occurrence = None
        self.owns_event = False
        self.save(update_fields=["event", "event_occurrence", "owns_event", "updated_at"])

    def remove(self, *, by: User) -> None:
        """Delete this draft meeting (the delete view calls this, not raw ``.delete()``).

        An OWNED calendar event is unwound through the existing rails first —
        ``remove_from_google`` + ``remove_from_discord`` — then its row is deleted, so no
        orphaned event keeps announcing a cancelled meeting. A merely-linked event is
        left completely alone.

        Raises:
            MeetingLockedError: If the minutes are locked (deletion requires unlock first).
        """
        self.assert_editable()
        event = self.event
        if event is not None and self.owns_event:
            event.remove_from_google()
            event.remove_from_discord()
            event.delete()
        self.delete()

    def add_item(self, *, by: User, name: str = "") -> MeetingAgendaItem:
        """Append an agenda item and return it.

        Raises:
            MeetingLockedError: If the minutes are locked.
        """
        self.assert_editable()
        return MeetingAgendaItem.objects.create(meeting=self, name=name)

    def add_attendee(self, *, member: Member | None = None, guest_name: str = "") -> MeetingAttendee:
        """Add a roster member OR a free-text guest to the attendance list.

        The member-XOR-guest shape is enforced by the check constraint; the friendly
        duplicate guard here surfaces as a 422 toast in the workspace.

        Raises:
            MeetingLockedError: If the minutes are locked.
            ValueError: If the member is already on the list.
        """
        self.assert_editable()
        if member is not None and self.attendees.filter(member=member).exists():
            raise ValueError("Already on the list.")
        return MeetingAttendee.objects.create(meeting=self, member=member, guest_name=guest_name)

    def propose_item(self, *, by: User, title: str, why: str) -> MeetingItemProposal:
        """A member proposes an agenda item for this (upcoming, editable) meeting.

        The view guards ``can_propose_to_meeting``; the model re-asserts the hard
        invariants (not locked, not past) and pings the reviewers via the spine.

        Raises:
            MeetingLockedError: If the minutes are locked.
            ValueError: If the meeting date has already passed.
        """
        self.assert_editable()
        if self.scheduled_date is not None and self.scheduled_date < timezone.localdate():
            raise ValueError("This meeting has already happened — proposals are closed.")
        proposal = MeetingItemProposal.objects.create(meeting=self, title=title, why=why, proposed_by=by)
        from core.events.emit import emit

        emit(
            "meeting.item_proposed",
            actor=by,
            target=proposal,
            context={
                "guild": self.guild,
                "meeting": self,
                "meeting_title": self.display_title,
                "proposal_title": title,
            },
            url=self.absolute_url,
            period=f"meeting-proposal:{proposal.pk}:submitted",
        )
        return proposal

    def carryover_actions(self) -> MeetingActionItemQuerySet:
        """Open action items from the scope's earlier meetings (empty while undated)."""
        return MeetingActionItem.objects.carryover_for(self)


class MeetingAttendee(models.Model):
    """One attendance row: a roster member OR a free-text guest, present or absent."""

    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="attendees",
        help_text="Parent meeting.",
    )
    member = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
        help_text="A roster member. Blank when this row is a free-text guest.",
    )
    guest_name = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Name of a non-member guest. Blank when a roster member is picked.",
    )
    present = models.BooleanField(default=True, help_text="Off = marked absent (shown struck through).")
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")

    class Meta:
        ordering = ["sort_order", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=((Q(member__isnull=False) & Q(guest_name="")) | (Q(member__isnull=True) & ~Q(guest_name=""))),
                name="ck_meetingattendee_member_xor_guest",
            ),
            models.UniqueConstraint(
                fields=["meeting", "member"],
                condition=Q(member__isnull=False),
                name="uq_meetingattendee_meeting_member",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.display_name} @ {self.meeting}"

    @property
    def display_name(self) -> str:
        """The roster member's name, or the guest's free-text name."""
        return self.member.display_name if self.member is not None else self.guest_name


class MeetingAgendaItem(models.Model):
    """One agenda topic: the plan (name + description) that grows minutes and actions."""

    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="items",
        help_text="Parent meeting.",
    )
    name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="The topic. Blank right after '+ Add item' — fill it in place.",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Plain text. What this item is about — the 'agenda' half.",
    )
    minutes = models.TextField(
        blank=True,
        default="",
        help_text="Sanitized HTML (Quill). What was discussed/decided — the 'minutes' half.",
    )
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Member whose approved proposal created this item; shown as a credit.",
    )
    sort_order = models.PositiveIntegerField(
        default=0, help_text="Position on the agenda; the up/down buttons swap these."
    )

    class Meta:
        ordering = ["sort_order", "pk"]

    def __str__(self) -> str:
        return f"{self.name or 'Untitled item'} ({self.meeting})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Auto-assign ``max(sibling sort_order) + 10`` on create so new items land at
        the bottom (an explicit non-zero ``sort_order`` is respected)."""
        if self._state.adding and self.sort_order == 0:
            max_order = self.meeting.items.aggregate(max_order=models.Max("sort_order"))["max_order"]
            self.sort_order = (max_order or 0) + 10
        super().save(*args, **kwargs)

    @property
    def open_action_count(self) -> int:
        """Open actions under this topic (the collapsed-item 'N★' badge). Counts in
        Python so a ``prefetch_related("items__actions")`` workspace query stays N+1-free."""
        return sum(1 for action in self.actions.all() if action.status == MeetingActionItem.Status.OPEN)

    def move(self, *, direction: str) -> None:
        """Swap this item one position up or down the agenda; a clamped no-op at the ends.

        Renumbers the whole sibling list (position × 10) so ties in ``sort_order``
        can never make a move invisible.

        Args:
            direction: ``"up"`` or ``"down"``.
        """
        siblings = list(self.meeting.items.all())
        index = siblings.index(self)
        target = index - 1 if direction == "up" else index + 1
        if target < 0 or target >= len(siblings):
            return
        siblings[index], siblings[target] = siblings[target], siblings[index]
        for position, item in enumerate(siblings):
            new_order = (position + 1) * 10
            if item.sort_order != new_order:
                item.sort_order = new_order
                item.save(update_fields=["sort_order"])


class MeetingActionItemQuerySet(models.QuerySet):
    """Carryover query for open action items."""

    def carryover_for(self, meeting: Meeting) -> MeetingActionItemQuerySet:
        """Open actions from OTHER meetings in the same scope (guild, or both NULL for
        council) dated before this meeting — oldest source meeting first, then row order.

        Approval status of the source meeting does not matter (an open task is open);
        undated source drafts are excluded by the date comparison, and an undated
        *target* meeting has no carryover at all.
        """
        if meeting.scheduled_date is None:
            return self.none()
        qs = self.filter(
            status=MeetingActionItem.Status.OPEN,
            item__meeting__scheduled_date__lt=meeting.scheduled_date,
        )
        if meeting.guild_id is None:
            qs = qs.filter(item__meeting__guild__isnull=True)
        else:
            qs = qs.filter(item__meeting__guild=meeting.guild_id)
        return qs.select_related("item__meeting").order_by("item__meeting__scheduled_date", "sort_order", "pk")


class MeetingActionItem(models.Model):
    """A task that came out of an agenda topic; open ones carry over until closed."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        DONE = "done", "Done"
        DISMISSED = "dismissed", "Dismissed"

    item = models.ForeignKey(
        MeetingAgendaItem,
        on_delete=models.CASCADE,
        related_name="actions",
        help_text="The agenda topic this action came out of.",
    )
    name = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text="The task, e.g. 'Order more 80-grit sandpaper'.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        help_text="Open items carry over to the next meeting until done or dismissed.",
    )
    is_flagged = models.BooleanField(default=False, help_text="Red priority flag.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")
    closed_at = models.DateTimeField(null=True, blank=True, help_text="When it was completed or dismissed.")
    closed_in = models.ForeignKey(
        Meeting,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The meeting whose carryover panel closed it (blank when closed in its own meeting).",
    )

    objects = MeetingActionItemQuerySet.as_manager()

    class Meta:
        ordering = ["sort_order", "pk"]
        indexes = [
            models.Index(fields=["status"], name="idx_%(class)s_open", condition=Q(status="open")),
        ]

    def __str__(self) -> str:
        return f"{self.name or 'Untitled action'} [{self.get_status_display()}]"

    def complete(self, *, by: User, in_meeting: Meeting | None = None) -> None:
        """Mark done — ``in_meeting`` when closed from another meeting's carryover panel
        (allowed even when the source meeting is locked; that's the point of carryover).

        Raises:
            ValueError: If not currently open.
        """
        if self.status != self.Status.OPEN:
            raise ValueError("Only an open action item can be completed.")
        self.status = self.Status.DONE
        self.closed_at = timezone.now()
        self.closed_in = in_meeting
        self.save(update_fields=["status", "closed_at", "closed_in"])

    def dismiss(self, *, by: User, in_meeting: Meeting) -> None:
        """Wave off a stale carryover item without lying that it was done.

        Raises:
            ValueError: If not currently open.
        """
        if self.status != self.Status.OPEN:
            raise ValueError("Only an open action item can be dismissed.")
        self.status = self.Status.DISMISSED
        self.closed_at = timezone.now()
        self.closed_in = in_meeting
        self.save(update_fields=["status", "closed_at", "closed_in"])

    def reopen(self) -> None:
        """Uncheck the done box (its own meeting only) — DONE back to OPEN, stamps
        cleared. A dismissal is not reopenable from the UI: it's a wave-off.

        Raises:
            ValueError: If not currently done.
        """
        if self.status != self.Status.DONE:
            raise ValueError("Only a completed action item can be reopened.")
        self.status = self.Status.OPEN
        self.closed_at = None
        self.closed_in = None
        self.save(update_fields=["status", "closed_at", "closed_in"])


class MeetingAttachment(models.Model):
    """A file OR a link attached to a meeting — migrated note attachments and the slide
    deck / budget sheet a locked record carries.

    A structural copy of :class:`GuildMeetingNoteAttachment` re-parented to
    :class:`Meeting`: same upload mechanics, XOR shape, and orphan cleanup.
    """

    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="attachments",
        help_text="Parent meeting.",
    )
    label = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="What to call this — e.g. 'Agenda PDF'. Defaults to the file name or link.",
    )
    file = models.FileField(
        upload_to="meetings/attachments/",
        blank=True,
        validators=[validate_document],
        help_text="Upload a document (PDF, Word, slides, spreadsheet…). Leave blank if you're adding a link instead.",
    )
    url = models.URLField(
        blank=True,
        default="",
        help_text="Link to an external doc (e.g. a Google Doc). Leave blank if you uploaded a file instead.",
    )
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "created_at"]
        constraints = [
            models.CheckConstraint(
                condition=((Q(file="") & ~Q(url="")) | (~Q(file="") & Q(url=""))),
                name="ck_meetingattachment_file_xor_url",
            ),
        ]

    def __str__(self) -> str:
        return f"Attachment #{self.pk} for {self.meeting}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        delete_orphan_on_replace(self, "file")  # mirror GuildMeetingNoteAttachment; no image normalization
        super().save(*args, **kwargs)

    @property
    def is_file(self) -> bool:
        return bool(self.file)

    @property
    def is_link(self) -> bool:
        return bool(self.url)

    @property
    def display_name(self) -> str:
        """Label if set, else the file's base name, else the URL."""
        if self.label:
            return self.label
        if self.file and self.file.name:
            return self.file.name.rsplit("/", 1)[-1]
        return self.url


class MeetingItemProposal(models.Model):
    """A member's proposed agenda item for an upcoming meeting, awaiting a decision."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined"
        WITHDRAWN = "withdrawn", "Withdrawn"

    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="proposals",
        help_text="The upcoming meeting this item is proposed for.",
    )
    title = models.CharField(max_length=200, help_text="The topic the member wants on the agenda.")
    why = models.TextField(
        blank=True,
        default="",
        help_text="Why it matters / what needs deciding. Becomes the item's description on approval.",
    )
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
        help_text="Who proposed it.",
    )
    state = models.CharField(
        max_length=20,
        choices=State.choices,
        default=State.PENDING,
        help_text="Pending until leadership decides.",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who decided.",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, help_text="When the decision was recorded.")
    review_note = models.TextField(
        blank=True,
        default="",
        help_text="Optional note back to the proposer (why declined, or what was tweaked).",
    )
    created_item = models.OneToOneField(
        MeetingAgendaItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_proposal",
        help_text="The agenda item an approval created.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["state"], name="idx_meetingproposal_pending", condition=Q(state="pending")),
        ]

    def __str__(self) -> str:
        return f"{self.title} → {self.meeting} ({self.get_state_display()})"

    def approve(self, *, reviewer: User, title: str | None = None, why: str | None = None) -> MeetingAgendaItem:
        """Put the proposal on the agenda; the proposer is credited on the item.

        The optional ``title``/``why`` overrides are the edit-then-approve path (the
        decision modal posts possibly-tweaked text). The new item lands at the agenda
        bottom via the sort_order auto-assign.

        Raises:
            InvalidProposalTransition: If not currently pending.
            MeetingLockedError: If the meeting's minutes are locked.
        """
        if self.state != self.State.PENDING:
            raise InvalidProposalTransition(f"Cannot approve a proposal in state '{self.state}'.")
        self.meeting.assert_editable()
        item = MeetingAgendaItem.objects.create(
            meeting=self.meeting,
            name=title or self.title,
            description=why or self.why,
            proposed_by=self.proposed_by,
        )
        self.state = self.State.APPROVED
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.created_item = item
        self.save(update_fields=["state", "reviewed_by", "reviewed_at", "created_item"])
        self._emit_decided(period=f"meeting-proposal:{self.pk}:approved")
        return item

    def decline(self, *, reviewer: User, note: str = "") -> None:
        """Turn the proposal down; the note back to the proposer is optional.

        Raises:
            InvalidProposalTransition: If not currently pending.
            MeetingLockedError: If the meeting's minutes are locked.
        """
        if self.state != self.State.PENDING:
            raise InvalidProposalTransition(f"Cannot decline a proposal in state '{self.state}'.")
        self.meeting.assert_editable()
        self.state = self.State.DECLINED
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.review_note = note
        self.save(update_fields=["state", "reviewed_by", "reviewed_at", "review_note"])
        self._emit_decided(period=f"meeting-proposal:{self.pk}:declined")

    def withdraw(self, *, by: User) -> None:
        """The proposer pulls back their own pending proposal. Silent — no emit; the
        reviewer queue simply loses the row, and ``Meeting.approve``'s auto-decline
        skips withdrawn proposals.

        Raises:
            InvalidProposalTransition: If not currently pending.
            ValueError: If ``by`` is not the proposer (the view 403s anyone else first).
        """
        if self.state != self.State.PENDING:
            raise InvalidProposalTransition(f"Cannot withdraw a proposal in state '{self.state}'.")
        if by.pk != self.proposed_by_id:
            raise ValueError("Only the proposer can withdraw their proposal.")
        self.state = self.State.WITHDRAWN
        self.save(update_fields=["state"])

    def _emit_decided(self, *, period: str) -> None:
        """Notify the proposer of the outcome (added to the agenda / declined + note)."""
        from core.events.emit import emit

        if self.state == self.State.APPROVED:
            outcome = f"'{self.title}' was added to the agenda for {self.meeting.display_title}."
        else:
            outcome = f"'{self.title}' wasn't added to the agenda for {self.meeting.display_title}."
        emit(
            "meeting.item_decided",
            actor=self.reviewed_by,
            target=self,
            context={
                "user": self.proposed_by,
                "proposal_title": self.title,
                "meeting_title": self.meeting.display_title,
                "meeting_url": self.meeting.absolute_url,
                "review_note": self.review_note,
                "outcome": outcome,
            },
            url=self.meeting.absolute_url,
            period=period,
        )


class VotePreferenceQuerySet(models.QuerySet):
    def from_signed_up_members(self) -> VotePreferenceQuerySet:
        """Votes cast by members who have a linked User account.

        Excludes VotePreferences created by Airtable backfill for members
        who were imported but never signed up to the Django app. Only
        signed-up members should influence live standings or snapshots.
        """
        return self.filter(member__user__isnull=False)

    def with_role_flags(self) -> VotePreferenceQuerySet:
        """Annotate each vote with the voter's guild-lead / guild-staff status.

        Mirrors ``Member.is_guild_lead`` / ``Member.is_guild_staff`` as ``Exists``
        subqueries, so a caller serializing many votes reads ``member_is_guild_lead`` /
        ``member_is_guild_staff`` straight off the row instead of firing one EXISTS
        query per voter (the N+1 the raw-votes builders used to hit). The same managers
        the properties use keep the booleans byte-for-byte identical.
        """
        return self.annotate(
            member_is_guild_lead=Exists(Guild.objects.filter(guild_lead_id=OuterRef("member_id"))),
            member_is_guild_staff=Exists(GuildStaffMembership.objects.filter(member_id=OuterRef("member_id"))),
        )

    def cast_ballot(
        self, member: Member, *, guild_1st: Guild, guild_2nd: Guild, guild_3rd: Guild
    ) -> tuple[VotePreference, bool]:
        """Create or update ``member``'s persistent ballot — the one shared save path.

        Both the hub voting page and the Discord ``/vote`` command land here, so every side
        effect of a ballot save fires identically for both: ``updated_at`` (auto_now), the
        Airtable push in :meth:`VotePreference.save`, and the vote-activity post-save signal.
        Callers validate first (``hub.forms.VotePreferenceForm``); this method only persists.

        Returns:
            ``(preference, created)`` — ``created`` is True for a first-ever ballot.
        """
        # cast: this queryset class is unparametrized (parametrizing it breaks the
        # ``with_role_flags`` annotation attributes under django-stubs), so
        # ``update_or_create`` types its row as the generic model.
        return cast(
            "tuple[VotePreference, bool]",
            self.update_or_create(
                member=member,
                defaults={"guild_1st": guild_1st, "guild_2nd": guild_2nd, "guild_3rd": guild_3rd},
            ),
        )


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


class ResultsAlreadySentError(Exception):
    """Raised when a snapshot's member results email is sent twice without an explicit resend."""


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
    is_auto = models.BooleanField(
        default=False,
        help_text="True when this snapshot was taken automatically at cycle end (vs. by an admin).",
    )
    results_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the member results email was sent for this snapshot. Null = pending the admin's review & send.",
    )
    results_send_count = models.PositiveIntegerField(
        default=0,
        help_text="How many times the results email has been sent (>=1 after the first send; supports resend).",
    )

    class Meta:
        ordering = ["-snapshot_at"]
        verbose_name = "Funding Snapshot"
        verbose_name_plural = "Funding Snapshots"

    def __str__(self) -> str:
        return f"{self.cycle_label} — ${self.funding_pool}"

    @property
    def source_label(self) -> str:
        """How this snapshot was created — "Automatic" for cycle-end auto-takes, else "Manual"."""
        return "Automatic" if self.is_auto else "Manual"

    @property
    def results_pending(self) -> bool:
        """Whether real per-guild results exist for this snapshot and haven't been emailed yet.

        A legacy or vote-less snapshot (no allocation) is never "pending" — there is
        nothing meaningful to send.
        """
        return self.results_sent_at is None and bool(self.allocation_summary())

    @classmethod
    def most_recent_pending(cls) -> FundingSnapshot | None:
        """The newest snapshot whose member results are still pending review & send.

        Drives the Overview "Results are in — review & send" banner. Walks unsent
        snapshots newest-first and returns the first with a real allocation.
        """
        for snapshot in cls.objects.filter(results_sent_at__isnull=True).order_by("-snapshot_at"):
            if snapshot.results_pending:
                return snapshot
        return None

    @classmethod
    def take(
        cls,
        *,
        title: str = "",
        minimum_pool: Decimal | int | None = None,
        is_auto: bool = False,
        actor: Any | None = None,
    ) -> FundingSnapshot | None:
        """Create a snapshot from current vote preferences (admin-confirmed results model).

        Taking a snapshot freezes the votes and runs the allocation, but does NOT
        email members — it logs the snapshot-taken activity once and pings admins via
        ``voting.results_ready`` so they can review the numbers and click Send results.

        Args:
            title: Custom label for the snapshot. Defaults to current month/year.
            minimum_pool: Dollar floor applied to the funding pool. Pool is
                ``max(paying_voters × $10, minimum_pool)``. ``None`` (the default)
                resolves to ``VotingSettings.load().minimum_pool_floor``; an explicit
                value overrides it.
            is_auto: True when taken automatically at cycle end (sets the badge flag).
            actor: The admin who took it, for the activity row. ``None`` for system.

        Returns:
            The created FundingSnapshot, or None if no votes exist.
        """
        from core.events.emit import emit
        from core.models import SiteActivity
        from membership.orientations import _absolute_url
        from membership.vote_calculator import calculate_results

        preferences = (
            VotePreference.objects.from_signed_up_members()
            .with_role_flags()
            .select_related(
                "member",
                "guild_1st",
                "guild_2nd",
                "guild_3rd",
            )
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
                "is_guild_lead": pref.member_is_guild_lead,
                "is_guild_staff": pref.member_is_guild_staff,
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

        if minimum_pool is None:
            minimum_pool_value = VotingSettings.load().minimum_pool_floor
        else:
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
            is_auto=is_auto,
        )

        # Log the snapshot-taken activity exactly once here (it no longer rides on the
        # per-member results emails, which would otherwise write N rows).
        SiteActivity.log(SiteActivity.Kind.FUNDING_SNAPSHOT_TAKEN, actor=actor, target=snapshot)

        # Ping admins to review & send — taking a snapshot never emails members.
        # The spine never absolutizes URLs, so the email/Discord link must already
        # carry the full host (a bare "/manage/..." path is a dead link in an inbox).
        review_url = _absolute_url(f"/manage/voting/history/{snapshot.pk}/")
        emit(
            "voting.results_ready",
            actor=actor,
            target=snapshot,
            context={
                "cycle_label": snapshot.cycle_label,
                "funding_pool": f"{snapshot.funding_pool}",
                "votes_cast": f"{calc['votes_cast']}",
                "review_url": review_url,
            },
            url=review_url,
            period=f"snapshot_ready:{snapshot.pk}",
        )
        return snapshot

    def allocation_summary(self) -> str:
        """A plain-text per-guild allocation breakdown for the results email.

        One line per guild, funding-descending (the order ``calculate_results``
        already sorts in): ``"Metal Guild — $600.00 (45.0%)"``. Empty string when the
        snapshot has no per-guild results (a legacy or vote-less snapshot).
        """
        results = (self.results or {}).get("results", [])
        lines = [f"{row['guild_name']} — ${row['funding']} ({row['share_pct']}%)" for row in results]
        return "\n".join(lines)

    def allocation_chart_html(self) -> SafeString:
        """A branded, email-safe bar chart of the per-guild allocation.

        The HTML twin of :meth:`allocation_summary`: one bar per guild (funding
        descending), each sized to that guild's funding relative to the leader — the top
        guild fills the bar — with the dollar amount, share of the pool, and a medal on
        the top three. Mirrors the live standings bars on the voting page so the results
        email reads the same as what members watched all month.

        Rendered through Django's autoescaping template engine (``_allocation_chart.html``),
        so guild names are HTML-escaped; the returned :class:`SafeString` is injected
        *unescaped* into the results email by the constrained copy renderer (see
        :func:`core.events.rendering.render_html`). Empty ``SafeString`` when the snapshot
        has no per-guild results (a legacy or vote-less snapshot), so the email simply
        omits the chart.
        """
        from decimal import ROUND_HALF_UP

        from django.template.loader import render_to_string
        from django.utils.safestring import mark_safe

        results = (self.results or {}).get("results", [])
        if not results:
            return mark_safe("")

        # funding is a Decimal fresh from calculate_results, or a string once the results
        # JSON has round-tripped the DB — coerce either way. The leader sets the scale.
        fundings = [Decimal(str(row["funding"])) for row in results]
        top = max(fundings)
        medals = {0: "🥇 ", 1: "🥈 ", 2: "🥉 "}
        rows = []
        for index, (row, funding) in enumerate(zip(results, fundings, strict=True)):
            if top > 0:
                width = int((funding / top * 100).to_integral_value(rounding=ROUND_HALF_UP))
                width = max(width, 2) if funding > 0 else 0
            else:
                width = 0  # every guild at $0 (degenerate) — flat empty bars
            rows.append(
                {
                    "name": row["guild_name"],
                    "funding_display": f"{funding:.2f}",
                    "share_display": row["share_pct"],
                    "width_pct": width,
                    "medal": medals.get(index, ""),
                }
            )
        return mark_safe(render_to_string("membership/emails/_allocation_chart.html", {"rows": rows}))

    def send_results(
        self, *, actor: Any | None = None, resend: bool = False, intro_note: str = "", discord: bool = True
    ) -> int:
        """Email every active member their results and post one @everyone Discord summary.

        Loops the snapshot's frozen ``raw_votes`` and emits ``voting.results_published``
        once per still-active voter with a personalized ballot recap, then emits the same
        event to every active member with a linked user account who did NOT appear in the
        voter list — giving non-voters the allocation without a ballot recap. Finally, one
        ``voting.results_discord`` broadcast fires so #general-member-chat hears the outcome.
        Stamps ``results_sent_at`` and bumps ``results_send_count`` for UI state + idempotency.

        Args:
            actor: The admin who triggered the send (unused in per-member emit, kept for
                symmetry/auditing of the calling view).
            resend: When True, allows re-sending already-sent results (a fresh ``period``
                re-delivers); when False a second send raises ``ResultsAlreadySentError``.
            intro_note: Optional organizer note rendered at the top of the results email
                (blank for a normal automated send). Carried into the ``intro_note`` merge field.
            discord: When False, suppresses the ``voting.results_discord`` @everyone broadcast
                while still emailing all members. Use ``--no-discord`` on the management command
                when back-filling a cycle that predates Discord notifications.

        Returns:
            The number of members who received a fresh delivery this send.

        Raises:
            ResultsAlreadySentError: If results were already sent and ``resend`` is False.
        """
        from core.events.emit import emit
        from membership.orientations import _absolute_url

        if self.results_sent_at is not None and not resend:
            raise ResultsAlreadySentError(f"Results for '{self.cycle_label}' were already sent.")

        self.results_send_count += 1
        n = self.results_send_count
        sent = 0
        allocation = self.allocation_summary()
        allocation_chart = self.allocation_chart_html()
        # The spine never absolutizes URLs — the results email's "See the full
        # breakdown" link must carry the full host or it's a dead link in an inbox.
        voting_url = _absolute_url("/guilds/voting/history/")
        member_ids = [vote["member_id"] for vote in self.raw_votes]
        active = {
            member.pk: member
            for member in Member.objects.filter(pk__in=member_ids, status=Member.Status.ACTIVE).select_related("user")
        }

        # --- 1. Voters: personalized ballot recap ---
        voter_ids: set[int] = set()
        for vote in self.raw_votes:
            member = active.get(vote["member_id"])
            if member is None:
                continue  # voter no longer active → skip (audience safety)
            voter_ids.add(vote["member_id"])
            ballot_recap = (
                f"You voted — 1st: {vote['guild_1st_name']}, "
                f"2nd: {vote['guild_2nd_name']}, "
                f"3rd: {vote['guild_3rd_name']}."
            )
            result = emit(
                "voting.results_published",
                target=self,
                context={
                    "member": member,  # → registrant resolver (drops no-account/no-email; respects opt-out)
                    "member_name": member.display_name,
                    "intro_note": intro_note,
                    "cycle_label": self.cycle_label,
                    "allocation_summary": allocation,
                    "allocation_chart": allocation_chart,
                    "ballot_recap": ballot_recap,
                    "vote_1st": vote["guild_1st_name"],
                    "vote_2nd": vote["guild_2nd_name"],
                    "vote_3rd": vote["guild_3rd_name"],
                    "voting_url": voting_url,
                },
                url=voting_url,
                period=f"snapshot:{self.pk}:send:{n}",  # fresh per send → resend re-delivers
            )
            if result.delivery_count:
                sent += 1

        # --- 2. Non-voters: allocation only, no ballot recap ---
        non_voters = Member.objects.active().filter(user__isnull=False).exclude(pk__in=voter_ids).select_related("user")
        for member in non_voters:
            result = emit(
                "voting.results_published",
                target=self,
                context={
                    "member": member,
                    "member_name": member.display_name,
                    "intro_note": intro_note,
                    "cycle_label": self.cycle_label,
                    "allocation_summary": allocation,
                    "allocation_chart": allocation_chart,
                    "ballot_recap": "",
                    "vote_1st": "",
                    "vote_2nd": "",
                    "vote_3rd": "",
                    "voting_url": voting_url,
                },
                url=voting_url,
                period=f"snapshot:{self.pk}:nonvoter:{member.pk}:send:{n}",
            )
            if result.delivery_count:
                sent += 1

        # --- 3. Discord: one @everyone broadcast to #general-member-chat ---
        if discord:
            emit(
                "voting.results_discord",
                target=self,
                context={
                    "cycle_label": self.cycle_label,
                    "allocation_summary": allocation,
                    "voting_url": voting_url,
                },
                discord_mention="@everyone",
                period=f"snapshot_discord:{self.pk}:send:{n}",
            )

        self.results_sent_at = timezone.now()
        self.save(update_fields=["results_sent_at", "results_send_count"])
        return sent

    def save(self, *args: Any, **kwargs: Any) -> None:
        super().save(*args, **kwargs)
        if not getattr(self, "_skip_airtable_sync", False):
            from airtable_sync.service import sync_snapshot_to_airtable

            sync_snapshot_to_airtable(self)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Hard-delete the snapshot and clean up its Airtable mirror row.

        Mirrors ``VotePreference.delete()`` — capture the Airtable record id
        before the row is gone, then remove the Airtable row (honoring the
        ``_skip_airtable_sync`` test flag) so deletions are honest end-to-end.
        """
        record_id = self.airtable_record_id
        result = super().delete(*args, **kwargs)
        if record_id and not getattr(self, "_skip_airtable_sync", False):
            from airtable_sync.service import delete_snapshot_from_airtable

            delete_snapshot_from_airtable(record_id)
        return result


class VotingSettings(models.Model):
    """Admin-configurable knobs for the monthly guild-funding vote (singleton, pk=1).

    Follows the ``SiteConfiguration`` / ``BillingSettings`` / ``ClassSettings`` pattern:
    one row, loaded via :meth:`load`, saved with ``pk`` forced to 1. Defaults reproduce
    today's behavior (a $1,000 pool floor, a 3-day reminder lead) with the automation
    switches on — and since nothing emails members without an admin's Send click,
    defaults-on is safe.
    """

    reminder_lead_days = models.PositiveIntegerField(
        default=3,
        validators=[MinValueValidator(1)],
        help_text="How many days before close to send the 'Polls closing soon!' reminder (minimum 1).",
    )
    minimum_pool_floor = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1000.00"),
        help_text="Dollar floor for the funding pool. The pool is the larger of (paying voters × $10) and this.",
    )
    reminders_enabled = models.BooleanField(
        default=True,
        help_text="Master switch for the 'Polls closing soon!' reminder to members who have voted.",
    )
    send_vote_soon_enabled = models.BooleanField(
        default=True,
        help_text="Master switch for the 'Vote soon!' nudge to members who've signed in but never voted.",
    )
    send_officer_reminder_enabled = models.BooleanField(
        default=True,
        help_text="Master switch for the 'Officer heads-up' turnout reminder to guild leadership before close.",
    )
    auto_snapshot_enabled = models.BooleanField(
        default=True,
        help_text=(
            "Master switch for the automated cycle-end snapshot, which also auto-emails the results "
            "(voters get their own ballot recap)."
        ),
    )

    class Meta:
        verbose_name = "Voting Settings"
        verbose_name_plural = "Voting Settings"

    def __str__(self) -> str:
        return "Voting settings"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Force singleton by always using pk=1."""
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> VotingSettings:
        """Load the singleton instance, creating it with defaults if needed."""
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj


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
    recurrence_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "Occurrence start (ISO) for one instance of a recurring feed event, so each repeat is "
            "its own row. Blank for a one-off event."
        ),
    )
    title = models.CharField(max_length=500, help_text="Event title from iCal SUMMARY field.")
    description = models.TextField(blank=True, help_text="Event description from iCal DESCRIPTION field.")
    location = models.CharField(max_length=500, blank=True, help_text="Event location from iCal LOCATION field.")
    url = models.URLField(blank=True, help_text="Event URL from iCal URL field.")
    start_dt = models.DateTimeField(help_text="Event start time, UTC-normalized.")
    end_dt = models.DateTimeField(help_text="Event end time, UTC-normalized.")
    all_day = models.BooleanField(default=False, help_text="True for all-day events (DATE not DATETIME in iCal).")
    fetched_at = models.DateTimeField(help_text="When this record was last synced from the iCal source.")
    discord_event_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Discord Scheduled Event id mirroring this feed event; blank when not pushed.",
    )
    channel_announced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When this event was announced (or silently marked announced) in the Discord calendar "
            "channel. NULL = not yet announced; the 15-minute announcer picks it up."
        ),
    )

    objects = CalendarEventQuerySet.as_manager()

    class Meta:
        ordering = ["start_dt"]
        indexes = [
            models.Index(fields=["start_dt", "end_dt"], name="idx_calendarevent_start_end"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["guild", "feed", "uid", "recurrence_id"], name="uq_calendarevent_guild_feed_uid"
            ),
        ]
        verbose_name = "Calendar Event"
        verbose_name_plural = "Calendar Events"

    def __str__(self) -> str:
        return self.title

    @property
    def source_key(self) -> str:
        """Key used to look up this event's display color in the source_colors dict.

        A guild event **or a guild-run class** keys by its guild, so a class inherits
        its guild's calendar color even though its ``source`` is ``classes``. A general
        event keys by its feed; anything else (a class with no guild) keys by its raw
        source. Safe because only GUILD and CLASSES rows ever carry a ``guild``.
        """
        if self.guild_id:
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


# ---------------------------------------------------------------------------
# Orientations
# ---------------------------------------------------------------------------


class OrientationError(Exception):
    """Raised when an orientation booking can't be made or transitioned."""


class GuildOrientationSettings(models.Model):
    """Per-guild orientation configuration plus two lead-editable follow-up emails.

    A guild offers orientation booking only when ``is_enabled`` is on; a lead can
    temporarily stop taking bookings with ``is_closed`` + a ``closed_message``
    (e.g. "On vacation till Sept 8") without losing their configuration.
    """

    guild = models.OneToOneField(
        Guild,
        on_delete=models.CASCADE,
        related_name="orientation_settings",
        help_text="The guild these orientation settings belong to.",
    )
    is_enabled = models.BooleanField(default=False, help_text="Offer orientation booking on this guild's page.")
    allow_custom_requests = models.BooleanField(
        default=True, help_text="Let members propose their own orientation time instead of only picking a posted slot."
    )
    info = models.TextField(
        blank=True, default="", help_text="Orientation info shown to members before they book (plain text)."
    )
    default_seats = models.PositiveSmallIntegerField(default=4, help_text="Default capacity for new orientation slots.")
    default_location = models.CharField(
        max_length=200, blank=True, default="", help_text="Default place orientations happen, e.g. 'Front desk'."
    )
    default_duration_minutes = models.PositiveSmallIntegerField(
        default=60, help_text="Length of a slot generated from a recurring rule, in minutes."
    )
    is_closed = models.BooleanField(default=False, help_text="Temporarily stop taking orientation bookings.")
    closed_message = models.CharField(
        max_length=300, blank=True, default="", help_text="Shown while closed, e.g. 'On vacation till Sept 8'."
    )
    thankyou_email_enabled = models.BooleanField(
        default=False, help_text="Send a thank-you / next-steps email once an orientation is completed."
    )
    thankyou_email_subject = models.CharField(
        max_length=200, blank=True, default="", help_text="Subject line of the thank-you email."
    )
    thankyou_email_body = models.TextField(
        blank=True, default="", help_text="Body of the thank-you email (plain text, line breaks preserved)."
    )
    thankyou_email_updated_at = models.DateTimeField(
        null=True, blank=True, help_text="When the thank-you email was last edited."
    )
    join_email_enabled = models.BooleanField(
        default=False, help_text="Send a welcome email when a member joins this guild."
    )
    join_email_subject = models.CharField(
        max_length=200, blank=True, default="", help_text="Subject line of the welcome email."
    )
    join_email_body = models.TextField(
        blank=True, default="", help_text="Body of the welcome email (plain text, line breaks preserved)."
    )
    join_email_updated_at = models.DateTimeField(
        null=True, blank=True, help_text="When the welcome email was last edited."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Guild orientation settings"
        verbose_name_plural = "Guild orientation settings"

    def __str__(self) -> str:
        return f"Orientation settings for {self.guild.name}"

    @property
    def thankyou_email_ready(self) -> bool:
        """True when the thank-you email is enabled and has both subject and body."""
        return self.thankyou_email_enabled and bool(self.thankyou_email_subject) and bool(self.thankyou_email_body)

    @property
    def join_email_ready(self) -> bool:
        """True when the welcome email is enabled and has both subject and body."""
        return self.join_email_enabled and bool(self.join_email_subject) and bool(self.join_email_body)

    @property
    def is_accepting(self) -> bool:
        """True when this guild is taking orientation bookings right now."""
        return self.is_enabled and not self.is_closed


class OrientationAvailability(models.Model):
    """A weekly recurring window during which a guild offers orientations.

    The slot-generation job materializes concrete ``OrientationSlot`` rows from
    each active rule across a rolling window.
    """

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    guild = models.ForeignKey(
        Guild, on_delete=models.CASCADE, related_name="orientation_rules", help_text="Parent guild."
    )
    weekday = models.PositiveSmallIntegerField(
        choices=Weekday.choices, help_text="Day of week this rule recurs on (0=Mon … 6=Sun)."
    )
    start_time = models.TimeField(help_text="When the orientation window starts.")
    end_time = models.TimeField(help_text="When the orientation window ends.")
    seats = models.PositiveSmallIntegerField(default=4, help_text="Capacity for slots generated from this rule.")
    location = models.CharField(
        max_length=200, blank=True, default="", help_text="Overrides the guild's default location for these slots."
    )
    is_active = models.BooleanField(default=True, help_text="Generate slots from this rule.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["weekday", "start_time"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_time__gt=models.F("start_time")),
                name="ck_orientationavailability_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.guild.name} orientation: {self.get_weekday_display()} {self.start_time:%H:%M}"


class OrientationSlotQuerySet(models.QuerySet):
    def for_guild(self, guild: Guild) -> OrientationSlotQuerySet:
        return self.filter(guild=guild)

    def upcoming(self) -> OrientationSlotQuerySet:
        """Future, uncancelled slots."""
        return self.filter(is_cancelled=False, starts_at__gte=timezone.now())

    def bookable(self) -> OrientationSlotQuerySet:
        """Upcoming slots at guilds currently accepting bookings (does not check seats)."""
        return self.upcoming().filter(
            guild__orientation_settings__is_enabled=True,
            guild__orientation_settings__is_closed=False,
        )


class OrientationSlot(models.Model):
    """A concrete, bookable orientation appointment with a seat cap."""

    class Source(models.TextChoices):
        MANUAL = "manual", "Added manually"
        GENERATED = "generated", "From a recurring rule"

    guild = models.ForeignKey(
        Guild, on_delete=models.CASCADE, related_name="orientation_slots", help_text="Parent guild."
    )
    availability = models.ForeignKey(
        OrientationAvailability,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="slots",
        help_text="The recurring rule that generated this slot, if any.",
    )
    source = models.CharField(
        max_length=10, choices=Source.choices, default=Source.MANUAL, help_text="How this slot was created."
    )
    starts_at = models.DateTimeField(help_text="When the orientation starts.")
    ends_at = models.DateTimeField(help_text="When the orientation ends.")
    seats = models.PositiveSmallIntegerField(default=4, help_text="Total capacity.")
    location = models.CharField(max_length=200, blank=True, default="", help_text="Where the orientation happens.")
    is_cancelled = models.BooleanField(default=False, help_text="Set when the slot is called off.")
    cancelled_reason = models.CharField(max_length=300, blank=True, default="", help_text="Why the slot was cancelled.")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = OrientationSlotQuerySet.as_manager()

    class Meta:
        ordering = ["starts_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["availability", "starts_at"],
                condition=Q(availability__isnull=False),
                name="uq_orientationslot_rule_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.guild.name} orientation @ {self.starts_at:%Y-%m-%d %H:%M}"

    @property
    def seats_taken(self) -> int:
        """Active (requested or confirmed) bookings — declined/cancelled free their seat."""
        return self.bookings.filter(
            status__in=[OrientationBooking.Status.REQUESTED, OrientationBooking.Status.CONFIRMED]
        ).count()

    @property
    def seats_remaining(self) -> int:
        return max(self.seats - self.seats_taken, 0)

    @property
    def is_full(self) -> bool:
        return self.seats_remaining <= 0

    @property
    def has_started(self) -> bool:
        return self.starts_at <= timezone.now()

    @property
    def is_past(self) -> bool:
        """The orientation window has fully elapsed (used by the auto-complete job)."""
        return self.ends_at <= timezone.now()

    @property
    def is_bookable(self) -> bool:
        """Future, uncancelled, has a free seat, and the guild is accepting bookings."""
        if self.is_cancelled or self.has_started or self.is_full:
            return False
        settings_obj = GuildOrientationSettings.objects.filter(guild=self.guild).first()
        return settings_obj is not None and settings_obj.is_accepting

    def book(self, member: Member, *, note: str = "") -> OrientationBooking:
        """Create a requested booking for ``member`` on this slot.

        Args:
            member: The member requesting the orientation.
            note: Optional free-text note the member adds.

        Returns:
            The newly created (REQUESTED) OrientationBooking.

        Raises:
            OrientationError: If the slot can't be booked, or the member is
                already oriented for or already has a live booking on this guild.
        """
        if not self.is_bookable:
            raise OrientationError("This orientation slot is not available to book.")
        if member.is_oriented_for(self.guild):
            raise OrientationError("You're already oriented for this guild.")
        if member.active_orientation_for(self.guild) is not None:
            raise OrientationError("You already have a pending orientation for this guild.")
        return OrientationBooking.objects.create(slot=self, guild=self.guild, member=member, member_note=note)

    def mark_cancelled(self, *, reason: str = "") -> None:
        """Flip the slot's own cancel state without touching its bookings.

        The slot-level state change lives here so both the silent model
        ``cancel()`` and the email-sending service ``cancel_slot`` set the same
        fields with the same ``update_fields``; only the per-booking fan-out
        differs between them.
        """
        self.is_cancelled = True
        self.cancelled_reason = reason
        self.save(update_fields=["is_cancelled", "cancelled_reason"])

    def cancel(self, *, reason: str = "") -> None:
        """Call off the slot and cancel each of its still-active bookings."""
        self.mark_cancelled(reason=reason)
        for booking in self.bookings.active():
            booking.cancel()


class OrientationBookingQuerySet(models.QuerySet):
    def for_guild(self, guild: Guild) -> OrientationBookingQuerySet:
        return self.filter(guild=guild)

    def active(self) -> OrientationBookingQuerySet:
        """Requested or confirmed — i.e. still occupying a seat."""
        return self.filter(status__in=[OrientationBooking.Status.REQUESTED, OrientationBooking.Status.CONFIRMED])

    def upcoming(self) -> OrientationBookingQuerySet:
        return self.active().filter(slot__starts_at__gte=timezone.now())

    def pending(self) -> OrientationBookingQuerySet:
        """Awaiting a lead's decision."""
        return self.filter(status=OrientationBooking.Status.REQUESTED)

    def completed(self) -> OrientationBookingQuerySet:
        return self.filter(is_completed=True)


class OrientationBooking(models.Model):
    """A member's orientation request and, once it happens, the orientation record.

    A booking starts as ``REQUESTED`` (not an official booking yet). The lead
    ``CONFIRMED``s, ``DECLINED``s, or it gets ``CANCELLED``. ``is_completed`` is
    set automatically after the slot ends (a lead can uncheck it) and is what
    marks the member oriented for the guild.
    """

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        CONFIRMED = "confirmed", "Confirmed"
        DECLINED = "declined", "Declined"
        CANCELLED = "cancelled", "Cancelled"

    slot = models.ForeignKey(
        OrientationSlot, on_delete=models.CASCADE, related_name="bookings", help_text="The slot booked."
    )
    guild = models.ForeignKey(
        Guild,
        on_delete=models.CASCADE,
        related_name="orientation_bookings",
        help_text="Denormalized from the slot for cheap filtering and scoping.",
    )
    member = models.ForeignKey(
        Member, on_delete=models.CASCADE, related_name="orientation_bookings", help_text="Who's getting oriented."
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.REQUESTED, help_text="Request lifecycle state."
    )
    is_completed = models.BooleanField(
        default=False,
        help_text="Whether the orientation actually happened. Auto-set after the slot ends; editable by leads.",
    )
    oriented_by = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orientations_given",
        help_text="Who gave the orientation.",
    )
    member_note = models.TextField(blank=True, default="", help_text="Optional note from the member when requesting.")
    lead_note = models.TextField(blank=True, default="", help_text="Note from the lead when declining or following up.")
    requested_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    objects = OrientationBookingQuerySet.as_manager()

    class Meta:
        ordering = ["-requested_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["guild", "member"],
                condition=Q(status__in=["requested", "confirmed"]),
                name="uq_orientationbooking_active_per_guild",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.member} — {self.guild.name} orientation ({self.get_status_display()})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.slot_id and not self.guild_id:
            self.guild = self.slot.guild
        super().save(*args, **kwargs)

    @property
    def is_upcoming(self) -> bool:
        """Still live (requested/confirmed) and the slot is in the future."""
        return self.status in (self.Status.REQUESTED, self.Status.CONFIRMED) and self.slot.starts_at >= timezone.now()

    def confirm(self, *, oriented_by: Member | None = None) -> None:
        """Accept the request; default the giver to the guild lead."""
        self.status = self.Status.CONFIRMED
        self.confirmed_at = timezone.now()
        self.oriented_by = oriented_by or self.guild.guild_lead
        self.save(update_fields=["status", "confirmed_at", "oriented_by"])

    def decline(self, *, note: str = "") -> None:
        """Turn down the request, optionally with a note for the member."""
        self.status = self.Status.DECLINED
        self.declined_at = timezone.now()
        self.lead_note = note
        self.save(update_fields=["status", "declined_at", "lead_note"])

    def cancel(self) -> None:
        """Cancel the booking, freeing its seat."""
        self.status = self.Status.CANCELLED
        self.cancelled_at = timezone.now()
        self.save(update_fields=["status", "cancelled_at"])

    def mark_completed(self, *, oriented_by: Member | None = None) -> None:
        """Record that the orientation happened — this is what marks the member oriented."""
        self.is_completed = True
        if oriented_by is not None:
            self.oriented_by = oriented_by
        elif self.oriented_by is None:
            self.oriented_by = self.guild.guild_lead
        self.save(update_fields=["is_completed", "oriented_by"])

    def uncomplete(self) -> None:
        """Undo a completion (a lead correcting an auto-completed no-show)."""
        self.is_completed = False
        self.save(update_fields=["is_completed"])


# ── Signage slideshow ─────────────────────────────────────────────────────────
# Wall-monitor digital signage (slideshow.pastlives.space). Lives here, not in
# core, because SlideshowSlide FKs GuildAnnouncement and the deck builder reuses
# CommunityEvent + the GuildImage image stack — all intra-app. The global/emergency
# settings sit on core.SiteConfiguration (plain config, no FK).


class SlideshowZone(models.Model):
    """One physical screen location the signage slideshow plays on (woodshop, lobby, …)."""

    name = models.CharField(max_length=100, help_text="Where this screen lives, e.g. 'Woodshop' or 'Lobby'.")
    slug = models.SlugField(
        max_length=100,
        unique=True,
        help_text="Used in the screen's URL: slideshow.pastlives.space/<slug>/. Point the monitor here once.",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Turn a screen's URL on or off. A disabled zone's URL returns 404.",
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text="Lower numbers sort first. The root URL redirects to the first enabled zone.",
    )

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name

    @property
    def player_url(self) -> str:
        """Absolute, set-and-forget URL to point a monitor at, e.g. https://slideshow.pastlives.space/woodshop/."""
        from django.urls import reverse

        return f"{settings.SIGNAGE_BASE_URL}{reverse('signage_player', args=[self.slug])}"

    def qr_svg(self) -> str:
        """Inline, CSS-scalable SVG QR of this zone's player_url — shown on the admin tab so staff can point a monitor at it."""
        from membership.qr import qr_svg as render_qr

        return render_qr(self.player_url)


class SlideshowSlideQuerySet(models.QuerySet):
    """Visibility rules for signage slides (the fat model — one source of truth)."""

    def visible(self, today: date_type | None = None) -> SlideshowSlideQuerySet:
        """Enabled slides inside their date window.

        Announcement-backed slides additionally require their linked announcement to be
        Published AND still active (not expired) — so a slide auto-hides the moment the
        announcement it mirrors is unpublished or expires. Custom slides skip that gate.
        Comparisons use ``timezone.localdate()`` against the ``DateField`` bounds.
        """
        if today is None:
            today = timezone.localdate()
        window = (
            Q(is_enabled=True)
            & (Q(starts_on__isnull=True) | Q(starts_on__lte=today))
            & (Q(ends_on__isnull=True) | Q(ends_on__gte=today))
        )
        live_announcement = ~Q(kind=SlideshowSlide.Kind.ANNOUNCEMENT) | (
            Q(announcement__moderation_state=GuildAnnouncement.ModerationState.PUBLISHED)
            & (Q(announcement__expires_at__isnull=True) | Q(announcement__expires_at__gte=today))
        )
        return self.filter(window & live_announcement)

    def for_zone(self, zone: SlideshowZone) -> SlideshowSlideQuerySet:
        """Slides pinned to this zone plus the all-zones (zone IS NULL) slides."""
        return self.filter(Q(zone__isnull=True) | Q(zone=zone))


class SlideshowSlide(models.Model):
    """A single slide in the signage rotation — a custom slide or a mirrored guild announcement."""

    class Kind(models.TextChoices):
        CUSTOM = "custom", "Custom slide"
        ANNOUNCEMENT = "announcement", "Guild announcement"

    kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        default=Kind.CUSTOM,
        help_text="Custom = your own title/body/image. Guild announcement = mirror a published announcement.",
    )
    zone = models.ForeignKey(
        SlideshowZone,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="slides",
        help_text="Show only on this screen. Leave blank to show on every screen.",
    )
    title = models.CharField(max_length=200, blank=True, default="", help_text="Headline for a custom slide.")
    body = models.TextField(
        blank=True,
        default="",
        help_text="Body text for a custom slide — a tip about the space, a reminder, etc.",
    )
    image = models.ImageField(
        upload_to="signage/slides/",
        blank=True,
        null=True,
        validators=[validate_image_size],
        help_text="Optional full-bleed image or flyer (JPG/PNG).",
    )
    link_url = models.URLField(
        blank=True,
        default="",
        help_text="Optional link — shown as a QR code when 'Show QR' is on.",
    )
    show_qr = models.BooleanField(default=False, help_text="Render a scannable QR of the link on this slide.")
    announcement = models.ForeignKey(
        GuildAnnouncement,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
        help_text="The published announcement to mirror. Only used for 'Guild announcement' slides.",
    )
    starts_on = models.DateField(null=True, blank=True, help_text="Optional: don't show before this date.")
    ends_on = models.DateField(null=True, blank=True, help_text="Optional: stop showing after this date.")
    is_enabled = models.BooleanField(default=True, help_text="Turn this slide on or off without deleting it.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Lower numbers show first in the rotation.")

    objects = SlideshowSlideQuerySet.as_manager()

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        if self.title:
            return self.title
        if self.announcement_id:
            return f"Announcement: {self.announcement}"
        return f"Slide {self.pk}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        delete_orphan_on_replace(self, "image")
        normalize_field_if_uploaded(self, "image", settings.IMAGE_MAX_LONG_EDGE_HERO)  # 2400px — wall-sized
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Interactive space map — Floorplan / MapHotspot / SpaceRequest
# ---------------------------------------------------------------------------


class FloorplanQuerySet(models.QuerySet):
    def published(self) -> FloorplanQuerySet:
        """Only the floors members should see on the public map."""
        return self.filter(is_published=True)


class Floorplan(models.Model):
    """One physical floor of the interactive space map.

    The map is **drawn by the app**: every room is a styled shape positioned from its
    hotspot's percentage coordinates, so it stays crisp at any zoom, follows the light/dark
    theme, and colours itself from live :class:`Space` status. :attr:`image` is an *optional*
    reference underlay for admins who want to trace against a scan — a floor with no image
    is the normal case.

    Ordered by ``sort_order`` and publish-gated: draft floors are visible only in the admin
    placement editor. Because coordinates are percentages of the drawn canvas (not of a
    picture), resizing or normalizing the underlay never moves a marker — but swapping in a
    differently-cropped underlay makes the tracing guide disagree with the shapes, which is
    what :attr:`image_changed` warns the editor about.
    """

    name = models.CharField(
        max_length=100,
        help_text="Floor label shown on the map's floor switcher — e.g. 'Floor 1' or '2nd Floor'.",
    )
    image = models.ImageField(
        upload_to="org/floorplans/",
        blank=True,
        validators=[validate_image_size],
        help_text=(
            "Optional reference underlay shown faintly behind the drawn rooms — handy when tracing a "
            "scanned plan. The map renders perfectly well without one."
        ),
    )
    aspect_ratio = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal("1.50"),
        validators=[MinValueValidator(Decimal("0.10")), MaxValueValidator(Decimal("10.00"))],
        help_text="Shape of the drawn floor, width divided by height — 1.50 is half again as wide as it is tall.",
    )
    sort_order = models.PositiveSmallIntegerField(
        default=0,
        help_text="Ascending — lower numbers show first (left-most on the floor switcher).",
    )
    is_published = models.BooleanField(
        default=False,
        help_text="When on, this floor appears on the public map. Draft floors are editor-only.",
    )
    caption = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text="Optional line shown under this floor's map.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When this floor was added.")
    updated_at = models.DateTimeField(auto_now=True, help_text="When this floor was last edited.")

    objects = FloorplanQuerySet.as_manager()

    #: Transient (never persisted) — True when :meth:`save` wrote a *different* underlay
    #: than the one already in the database. The placement editor reads it to warn that the
    #: new picture no longer lines up behind rooms traced against the old one.
    image_changed: bool = False

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Floor plan"
        verbose_name_plural = "Floor plans"

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.image_changed = self._image_differs_from_db()
        delete_orphan_on_replace(self, "image")
        normalize_field_if_uploaded(self, "image", settings.IMAGE_MAX_LONG_EDGE_HERO)
        super().save(*args, **kwargs)

    def _image_differs_from_db(self) -> bool:
        """True when this instance carries a different image file than the stored row.

        A brand-new row is not a "change" (there is nothing to misplace yet).
        """
        if self.pk is None:
            return False
        stored = Floorplan.objects.filter(pk=self.pk).values_list("image", flat=True).first()
        if stored is None:
            return False
        return stored != self.image.name

    #: Legend rows, in the order members read them: status slug → human label.
    LEGEND_ROWS: tuple[tuple[str, str], ...] = (
        ("available", "Available"),
        ("occupied", "Occupied"),
        ("maintenance", "Maintenance"),
        ("info", "Shops & facilities"),
    )

    @property
    def legend(self) -> list[tuple[str, str, int]]:
        """The map key for this floor — ``(status slug, label, live count)``, zeroes dropped.

        Counted in Python over the already-prefetched hotspots, so rendering the key costs
        no extra query, and the numbers are always the live :class:`Space` statuses.
        """
        tally: dict[str, int] = {}
        for hotspot in self.hotspots.all():
            if hotspot.is_decorative:
                continue
            tally[hotspot.availability_class or "info"] = tally.get(hotspot.availability_class or "info", 0) + 1
        return [(slug, label, tally[slug]) for slug, label in self.LEGEND_ROWS if tally.get(slug)]

    @property
    def list_filters(self) -> list[tuple[str, str, int]]:
        """The accessible list's status filters — ``All`` first, then this floor's own colours.

        Straight off :attr:`legend` (same prefetched hotspots, no extra query, same live
        counts), so the chips can never disagree with the map key sitting above them. A
        floor with nothing on it gets no chips at all — there is nothing to filter.
        """
        counts = self.legend
        if not counts:
            return []
        return [("all", "All", sum(count for _slug, _label, count in counts)), *counts]

    @property
    def list_hotspots(self) -> list[MapHotspot]:
        """This floor's markers ordered for the accessible list — actionable first.

        Grouped by status in the same order members just read in the legend (available,
        occupied, maintenance, then shops & facilities), so someone scanning for a space
        to rent meets the free ones first instead of paging through the leased studios.
        Within a status the editor's ``sort_order`` survives (the sort is stable), and the
        already-prefetched hotspots are sorted in Python — no extra query.
        """
        rank = {slug: index for index, (slug, _label) in enumerate(self.LEGEND_ROWS)}
        listed = (hotspot for hotspot in self.hotspots.all() if not hotspot.is_decorative)
        return sorted(listed, key=lambda hotspot: rank[hotspot.availability_class or "info"])


class MapHotspotQuerySet(models.QuerySet):
    def for_map(self) -> MapHotspotQuerySet:
        """Everything the map/list rendering touches, in one query (kills the marker N+1)."""
        return self.select_related("space", "space__sublet_guild", "guild", "floorplan")


class MapHotspot(models.Model):
    """A positioned marker on a floor plan — either a rectangular region or a labelled pin.

    Optionally bound to a :class:`Space` (studios, cubbies): status, price, size and
    occupants are always *derived* from that Space and never stored here, so the
    Airtable pull stays the single source of truth. Unbound hotspots are facility/info
    markers (wood shop, restroom, exit) that carry their own label and description.
    """

    class Shape(models.TextChoices):
        REGION = "region", "Region (rectangle)"
        PIN = "pin", "Pin (labelled dot)"

    class Kind(models.TextChoices):
        STUDIO = "studio", "Studio (leasable)"
        CUBBY = "cubby", "Shelf"
        FACILITY = "facility", "Facility / shop"
        INFO = "info", "Info marker"
        RESTROOM = "restroom", "Restroom"
        EXIT = "exit", "Emergency exit"
        MEETING_ROOM = "meeting_room", "Meeting room"
        EVENT_SPACE = "event_space", "Event space"
        WALL = "wall", "Wall"

    #: Kinds a member can request through the map (the rest are info-only or reservable).
    REQUESTABLE_KINDS: tuple[str, ...] = (Kind.STUDIO, Kind.CUBBY)
    #: Kinds whose CTA is a time-based reservation — owned by the reservations spec.
    RESERVABLE_KINDS: tuple[str, ...] = (Kind.MEETING_ROOM, Kind.EVENT_SPACE)
    #: Pure map decoration — drawn to shape the building, but never listed, counted in the
    #: legend, clickable for members, or given a status. A wall is the only one today.
    DECORATIVE_KINDS: tuple[str, ...] = (Kind.WALL,)

    floorplan = models.ForeignKey(
        Floorplan,
        on_delete=models.CASCADE,
        related_name="hotspots",
        help_text="The floor this marker sits on.",
    )
    shape = models.CharField(
        max_length=10,
        choices=Shape.choices,
        default=Shape.REGION,
        help_text="A region is a clickable rectangle; a pin is a small labelled dot.",
    )
    kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        default=Kind.STUDIO,
        help_text="What this marker is — drives its colour and which action members see.",
    )
    space = models.ForeignKey(
        "Space",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hotspots",
        help_text="The space this marker represents. Leave blank for facility/info markers.",
    )
    guild = models.ForeignKey(
        "Guild",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Optional: link this marker to a guild's page (e.g. a shop that is a guild's home).",
    )
    label = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Marker text for facility/info markers — e.g. 'Wood Shop'. Ignored when a space is linked.",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Optional blurb shown in this marker's detail panel.",
    )
    x = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("50.00"),
        help_text="Horizontal position as a percent of the image width (region: left edge; pin: centre).",
    )
    y = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("50.00"),
        help_text="Vertical position as a percent of the image height (region: top edge; pin: centre).",
    )
    w = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Region width as a percent of the image width. Blank for pins.",
    )
    h = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Region height as a percent of the image height. Blank for pins.",
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text="Ascending — orders the accessible list and breaks ties for stacking.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When this marker was added.")
    updated_at = models.DateTimeField(auto_now=True, help_text="When this marker was last edited or moved.")

    objects = MapHotspotQuerySet.as_manager()

    class Meta:
        ordering = ["floorplan__sort_order", "sort_order", "id"]
        verbose_name = "Map marker"
        verbose_name_plural = "Map markers"
        indexes = [
            models.Index(fields=["floorplan", "sort_order"], name="idx_maphotspot_floor_order"),
            models.Index(fields=["space"], name="idx_maphotspot_space"),
        ]
        constraints = [
            models.CheckConstraint(
                name="ck_maphotspot_region_has_dims",
                condition=(
                    (Q(shape="region") & Q(w__isnull=False) & Q(h__isnull=False))
                    | (Q(shape="pin") & Q(w__isnull=True) & Q(h__isnull=True))
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.display_label} ({self.floorplan.name})"

    @property
    def is_decorative(self) -> bool:
        """A wall or other pure-decoration marker: drawn on the map, never listed or clickable."""
        return self.kind in self.DECORATIVE_KINDS

    @property
    def display_label(self) -> str:
        """The marker's name — the linked space's code, else the free-text label."""
        if self.space is not None:
            return str(self.space)
        return self.label

    @property
    def code_label(self) -> str:
        """The short name written *inside* the drawn shape — a room is only so wide.

        The space's bare code ("A12"), never its full description; the fuller
        :attr:`display_label` belongs in the list, the detail panel and the accessible name,
        where there is room for it.
        """
        if self.space is not None:
            return self.space.space_id
        return self.label

    @property
    def linked_guild(self) -> Guild | None:
        """The guild this marker points at — an explicit link wins, else the space's sublet guild.

        Lets a facility shop (Ceramics, Wood Shop) link straight to its guild's page while a
        sublet studio still resolves through its :class:`Space`. The detail panel reads this
        single source so both routes render the same link.
        """
        if self.guild_id is not None:
            return self.guild
        if self.space is not None:
            return self.space.sublet_guild
        return None

    @property
    def status(self) -> str | None:
        """The linked space's status, or ``None`` for a facility/info marker."""
        return self.space.status if self.space is not None else None

    @property
    def status_display(self) -> str:
        """Human status used in the marker's aria-label and the detail header."""
        if self.space is None:
            return "Facility"
        return self.space.get_status_display()

    @property
    def availability_class(self) -> str | None:
        """``available`` / ``occupied`` / ``maintenance`` — ``None`` for info markers."""
        return self.space.status if self.space is not None else None

    @property
    def full_price(self) -> Decimal | None:
        """The linked space's monthly price, if it has one."""
        return self.space.full_price if self.space is not None else None

    @property
    def price_display(self) -> str:
        """The one place price copy is composed — never re-derive it ad hoc.

        ``"$420.00/mo"`` when a price is known, ``"Price on request"`` for a space with
        no derivable price, and ``""`` for a facility/info marker (which has no price).
        """
        if self.space is None:
            return ""
        price = self.full_price
        if price is None:
            return "Price on request"
        return f"${price:.2f}/mo"

    @property
    def size_display(self) -> str:
        """The linked space's size in plain words, or ``""`` when it has none recorded."""
        space = self.space
        if space is None:
            return ""
        if space.size_sqft is not None:
            return f"{space.size_sqft:.0f} sq ft"
        if space.width is not None and space.depth is not None:
            return f"{space.width:.0f} × {space.depth:.0f} ft"
        return ""

    @property
    def occupants(self) -> list[Member | Guild]:
        """Current tenants of the linked space (empty for info markers)."""
        return self.space.current_occupants if self.space is not None else []

    @property
    def occupant_names(self) -> list[str]:
        """Display names of the current tenants — members by preferred name, guilds by name."""
        return [getattr(tenant, "display_name", "") or str(tenant) for tenant in self.occupants]

    @property
    def cta_kind(self) -> str | None:
        """Which call-to-action this marker offers: ``lease`` / ``cubby`` / ``reserve`` / ``None``."""
        if self.kind == self.Kind.STUDIO:
            return "lease"
        if self.kind == self.Kind.CUBBY:
            return "cubby"
        if self.kind in self.RESERVABLE_KINDS:
            return "reserve"
        return None

    @property
    def cta_label(self) -> str:
        """The button text for this marker's action, or ``""`` when it has none."""
        return {
            "lease": "Request to lease",
            "cubby": "Request this space",
            "reserve": "Reserve",
        }.get(self.cta_kind or "", "")

    @property
    def is_requestable(self) -> bool:
        """True when a member could ask for this marker's space right now."""
        return (
            self.cta_kind in ("lease", "cubby")
            and self.space is not None
            and self.space.status == Space.Status.AVAILABLE
        )

    @property
    def search_text(self) -> str:
        """Lowercased haystack the accessible list's search box matches against.

        Everything a member would plausibly type to find a space: its code or name
        ("A12"), a facility's own label ("Wood Shop"), and what kind of thing it is
        ("cubby"). Composed here so the search agrees with what the row actually shows.
        """
        parts = [self.display_label, self.label, self.get_kind_display()]
        return " ".join(part for part in parts if part).lower()

    @property
    def aria_label(self) -> str:
        """The marker's accessible name — label, status, and price when there is one."""
        base = f"{self.display_label} — {self.status_display}"
        if self.price_display:
            return f"{base}, {self.price_display}"
        return base

    #: A drawn room needs at least this much of the canvas before its size/price will fit.
    FULL_TEXT_MIN_W = Decimal("5.0")
    FULL_TEXT_MIN_H = Decimal("6.0")
    #: Below this it is a colour block only — text would spill past the walls and read as noise.
    LABEL_MIN_W = Decimal("1.5")
    LABEL_MIN_H = Decimal("1.8")

    @property
    def detail_level(self) -> str:
        """How much text the drawn shape can carry without overflowing its walls.

        ``"full"`` — the room's name plus its size and price; ``"label"`` — the name alone;
        ``"minimal"`` — nothing, because the shape is smaller than a word. Nothing is ever
        lost: the accessible name (:attr:`aria_label`), the detail panel and the keyboard
        list always carry the whole story, and the shape grows legible as the map is zoomed.
        Pins are label-sized by definition — they size themselves to their text.
        """
        if self.shape == self.Shape.PIN or self.w is None or self.h is None:
            return "label"
        if self.w >= self.FULL_TEXT_MIN_W and self.h >= self.FULL_TEXT_MIN_H:
            return "full"
        if self.w >= self.LABEL_MIN_W and self.h >= self.LABEL_MIN_H:
            return "label"
        return "minimal"


class InvalidSpaceRequestTransition(ValueError):
    """Raised when a :class:`SpaceRequest` lifecycle method is called from a state that
    does not permit it (e.g. approving an already-withdrawn request)."""


class SpaceRequestQuerySet(models.QuerySet):
    def pending(self) -> SpaceRequestQuerySet:
        """Requests still awaiting a decision — also the reviewer queue's contents."""
        return self.filter(state=SpaceRequest.ModerationState.PENDING)

    def for_scope(self, scope: Any) -> SpaceRequestQuerySet:
        """Narrow to what a reviewer may act on.

        ``scope is True`` means an admin (every lease and cubby request); anything else
        is a ``Guild`` queryset/iterable — a lead sees only cubby requests for spaces
        their guild sublets.
        """
        if scope is True:
            return self
        return self.filter(kind=SpaceRequest.RequestKind.CUBBY, space__sublet_guild__in=scope)


class SpaceRequest(models.Model):
    """A member's ask for a studio lease or a cubby, routed to a human approver.

    Django-owned and purely advisory: approving one notifies the member and tells the
    approver to finalize the lease in Airtable — it never writes a :class:`Lease` or
    mutates the :class:`Space`. Mirrors :class:`CommunityEvent`'s proposal→decision
    machine, minus the changes-requested state (a request has no editable body, so
    "not this one" is a decline with a note).
    """

    class ModerationState(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined"
        WITHDRAWN = "withdrawn", "Withdrawn"

    class RequestKind(models.TextChoices):
        LEASE = "lease", "Studio lease"
        CUBBY = "cubby", "Cubby / shelf"

    requester = models.ForeignKey(
        "Member",
        on_delete=models.CASCADE,
        related_name="space_requests",
        help_text="The member who asked for this space.",
    )
    space = models.ForeignKey(
        "Space",
        on_delete=models.PROTECT,
        related_name="requests",
        help_text="The space being requested. Price and owning guild are read from it.",
    )
    hotspot = models.ForeignKey(
        MapHotspot,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requests",
        help_text="Which map marker the member clicked. Provenance only — the space is the real target.",
    )
    kind = models.CharField(
        max_length=10,
        choices=RequestKind.choices,
        default=RequestKind.LEASE,
        help_text="Whether this is a studio lease ask or a cubby/shelf ask. Sets who reviews it.",
    )
    state = models.CharField(
        max_length=12,
        choices=ModerationState.choices,
        default=ModerationState.PENDING,
        help_text="Where this request is in review.",
    )
    message = models.TextField(
        blank=True,
        default="",
        help_text="Optional note from the member — shown to the reviewer and carried into the notification.",
    )
    reviewed_by = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who approved or declined this request.",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, help_text="When the decision was recorded.")
    review_notes = models.TextField(
        blank=True,
        default="",
        help_text="The reviewer's reason. Required for a decline.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the member sent this request.")
    updated_at = models.DateTimeField(auto_now=True, help_text="When this request last changed.")

    objects = SpaceRequestQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Space request"
        verbose_name_plural = "Space requests"
        constraints = [
            models.UniqueConstraint(
                fields=["requester", "space"],
                condition=Q(state="pending"),
                name="uq_spacerequest_one_pending_per_member_space",
            ),
        ]
        indexes = [
            models.Index(fields=["state", "created_at"], name="idx_spacerequest_state_created"),
            models.Index(fields=["space", "state"], name="idx_spacerequest_space_state"),
        ]

    def __str__(self) -> str:
        return f"{self.requester.display_name} → {self.space.space_id} ({self.get_state_display()})"

    @property
    def is_open(self) -> bool:
        """True while the request is still awaiting a decision."""
        return self.state == self.ModerationState.PENDING

    @property
    def review_audience_label(self) -> str:
        """Plain-language description of who will read this request.

        Every request now routes to the makerspace admins (studio or shelf, owned or not),
        so the copy a member reads says exactly that. Reversible: restore the guild-lead
        branch here together with the registry recipient to send shelf asks back to leads.
        """
        return "the makerspace admins"

    def submit(self, *, requester: Member) -> None:
        """Create the request and notify its reviewers. One-shot — there is no resubmit.

        Raises:
            InvalidSpaceRequestTransition: If this request has already been saved.
        """
        if self.pk is not None:
            raise InvalidSpaceRequestTransition("A space request is submitted once — it cannot be re-submitted.")
        self.requester = requester
        self.state = self.ModerationState.PENDING
        self.save()
        self._notify_submitted()

    def approve(self, *, reviewer: User) -> None:
        """Approve the ask and tell the member. Does NOT create a Lease — a human
        finalizes that in Airtable.

        Raises:
            InvalidSpaceRequestTransition: If the request is not pending.
        """
        self._require_pending("approve")
        self.state = self.ModerationState.APPROVED
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.save(update_fields=["state", "reviewed_by", "reviewed_at", "updated_at"])
        self._notify_decision("space.request_approved", period=f"spacereq:{self.pk}:approved")

    def decline(self, *, reviewer: User, notes: str) -> None:
        """Turn the ask down with a reason the member will read.

        Raises:
            InvalidSpaceRequestTransition: If the request is not pending.
            ValueError: If ``notes`` is blank (a decline must explain why).
        """
        self._require_pending("decline")
        if not (notes or "").strip():
            raise ValueError("A decline needs a note so the member knows why.")
        self.state = self.ModerationState.DECLINED
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.review_notes = notes
        self.save(update_fields=["state", "reviewed_by", "reviewed_at", "review_notes", "updated_at"])
        self._notify_decision("space.request_declined", period=f"spacereq:{self.pk}:declined")

    def withdraw(self, *, by: User) -> None:
        """The requester pulls their own pending ask back.

        Keeps the row as an audit record (unlike a withdrawn event, which is deleted)
        and releases the one-pending-per-space constraint so they can ask again later.

        Raises:
            InvalidSpaceRequestTransition: If the request isn't pending, or ``by`` is
                not the requester.
        """
        self._require_pending("withdraw")
        if self.requester.user_id != by.pk:
            raise InvalidSpaceRequestTransition("Only the member who made a request can withdraw it.")
        self.state = self.ModerationState.WITHDRAWN
        self.save(update_fields=["state", "updated_at"])

    def _require_pending(self, action: str) -> None:
        if self.state != self.ModerationState.PENDING:
            raise InvalidSpaceRequestTransition(f"Cannot {action} a space request in state '{self.state}'.")

    def _review_url(self) -> str:
        from django.urls import reverse

        base = settings.MEMBER_BASE_URL.rstrip("/")
        return f"{base}{reverse('hub_space_request_review_queue')}#request-{self.pk}"

    def _map_url(self) -> str:
        from django.urls import reverse

        base = settings.MEMBER_BASE_URL.rstrip("/")
        anchor = f"#hotspot-{self.hotspot_id}" if self.hotspot_id else ""
        return f"{base}{reverse('hub_spaces')}{anchor}"

    def _notify_submitted(self) -> None:
        """Route a fresh request to the makerspace admins.

        Both event keys (``space.lease_requested`` and ``space.cubby_requested``) now resolve
        to the admins in the registry, so a studio lease and a shelf ask land in the same
        inbox. Reversible: point ``space.cubby_requested`` back at ``GUILD_LEADERSHIP_OR_ADMINS``
        to send shelf asks to the owning guild's leadership again. The ``guild`` context below
        is still passed so that switch needs no change here.
        """
        from core.events.emit import emit

        guild = self.space.sublet_guild
        is_cubby = self.kind == self.RequestKind.CUBBY
        event_key = "space.cubby_requested" if is_cubby else "space.lease_requested"
        review_url = self._review_url()
        emit(
            event_key,
            actor=self.requester.user,
            target=self,
            context={
                "guild": guild if is_cubby else None,
                "space_code": self.space.space_id,
                "space_label": str(self.space),
                "member_name": self.requester.display_name,
                "requester_message": self.message,
                "price_display": self._price_display(),
                "audience_label": self.review_audience_label,
                "review_url": review_url,
            },
            url=review_url,
            period=f"spacereq:{self.pk}:submitted",
        )

    def _notify_decision(self, event_key: str, *, period: str) -> None:
        """Tell the requester what a reviewer decided."""
        from core.events.emit import emit

        map_url = self._map_url()
        emit(
            event_key,
            actor=self.reviewed_by,
            target=self,
            context={
                "user": self.requester.user,
                "space_code": self.space.space_id,
                "space_label": str(self.space),
                "price_display": self._price_display(),
                "audience_label": self.review_audience_label,
                "reviewer_notes": self.review_notes,
                "space_url": map_url,
            },
            url=map_url,
            period=period,
        )

    def _price_display(self) -> str:
        """The space's monthly price for notification copy, or 'Price on request'."""
        price = self.space.full_price
        if price is None:
            return "Price on request"
        return f"${price:.2f}/mo"
