"""Models for the Classes app."""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Callable, Sequence
from datetime import date as date_type, datetime
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.db.models import CheckConstraint, Exists, F, OuterRef, Q
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import format_html, strip_tags
from django.utils.safestring import SafeString, mark_safe
from django.utils.timezone import localtime

from core.files import delete_orphan_on_replace
from core.images import normalize_field_if_uploaded
from core.models import HeroCropMixin
from core.validators import validate_image_size

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser, AnonymousUser, User
    from django.core.files.uploadedfile import UploadedFile

    from billing.models import PaymentRefund
    from membership.models import Member

logger = logging.getLogger(__name__)

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
        verbose_name = "Guild Type"
        verbose_name_plural = "Guild Types"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        from django.conf import settings

        delete_orphan_on_replace(self, "hero_image")
        normalize_field_if_uploaded(self, "hero_image", settings.IMAGE_MAX_LONG_EDGE_HERO)
        super().save(*args, **kwargs)


# Storage folder + ceiling constants for class hero images. Keys under this prefix are
# content-addressed (see core.images.store_content_addressed) so the same picture used by
# many offerings is stored exactly once.
CLASS_IMAGE_PREFIX = "classes/images/"

# Ceiling on how much of the live legacy catalog a single sync run may archive. Above
# this, ``ClassOfferingQuerySet.archive_missing_from_legacy_feed`` refuses to act and logs
# instead — a feed that suddenly lists almost nothing is far likelier to be broken than to
# be telling the truth.
LEGACY_ARCHIVE_GUARD_FRACTION = 0.5

# Human-readable pointer used in the guard's log line.
LEGACY_CMS_FEED_LABEL = "https://classes.pastlives.space/jsonapi/node/class"


class ClassOfferingQuerySet(models.QuerySet["ClassOffering"]):
    def public(self) -> "ClassOfferingQuerySet":
        """Published classes visible in the public portal (excludes private).

        Demo classes (a ``demo-`` slug, seeded by the demo_data command) are hidden
        here unless the ``display_demo_classes`` site setting is on, so seeded demo
        content can sit on production without members seeing or booking it. Admin and
        teaching querysets do not route through ``public()`` (they use ``editable_by``
        / ``for_instructor`` / ``hosted_by`` / ``.all()``), so staff always see and
        manage demo classes. ``bookable()`` calls this, so the gate covers the
        catalog, calendar, Discord posts, and every other public class surface at once.
        """
        from core.models import SiteConfiguration

        qs = self.filter(status="published", is_private=False)
        if not SiteConfiguration.load().display_demo_classes:
            qs = qs.exclude(slug__startswith="demo-")
        return qs

    def refile_into_guild_categories(self, assignments: dict[int, int]) -> int:
        """Re-file offerings into guild-linked categories; returns how many changed.

        ``assignments`` maps offering pk → target Category pk. Only offerings
        currently in a guild-less category and only guild-linked targets apply;
        a stale or invalid pair is skipped silently (another admin may have
        re-filed that row mid-edit). Both sides load in one query each, but rows
        save one-by-one on purpose: ``ClassOffering.save`` recomputes
        ``grouping_key`` when the category changes, which ``bulk_update`` would
        skip and quietly break catalog card grouping.
        """
        targets = Category.objects.filter(guild__isnull=False).in_bulk(assignments.values())
        offerings = self.filter(category__guild__isnull=True).in_bulk(assignments.keys())
        applied = 0
        for offering_pk, target_pk in assignments.items():
            offering = offerings.get(offering_pk)
            target = targets.get(target_pk)
            if offering is None or target is None:
                continue
            offering.category = target
            offering.save(update_fields=["category"])
            applied += 1
        return applied

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

    def awaiting_admin_validation(self, member: "Member") -> "ClassOfferingQuerySet":
        """Pending classes whose guild-lead gate this member's guilds already approved.

        The counterpart to :meth:`awaiting_guild_lead` for the lead's dashboard —
        after a lead approves stage one, the class stays visible here (read only)
        until an admin publishes or bounces it, instead of silently vanishing.
        Each ``.filter()`` call joins ``approvals`` separately on purpose: one
        approved ``guild_lead`` row AND one undecided ``admin`` row must exist.
        """
        return (
            self.filter(status="pending", category__guild__in=member.staffed_guilds)
            .filter(approvals__role="guild_lead", approvals__decision="approved")
            .filter(approvals__role="admin", approvals__decision="")
            .distinct()
        )

    def for_instructor(self, instructor: "Member") -> "ClassOfferingQuerySet":
        return self.filter(instructor=instructor)

    def hosted_by(self, member: "Member") -> "ClassOfferingQuerySet":
        """Classes this member teaches or authored (instructor OR created_by).

        Both are direct single-valued FK comparisons on the row, so no join can
        multiply rows — no ``.distinct()`` needed. ``member`` must be a real
        Member: callers guard ``None`` (passing ``None`` would match every class
        with a NULL instructor/author, the opposite of intended).
        """
        return self.filter(Q(instructor=member) | Q(created_by=member))

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

    def archive_missing_from_legacy_feed(self, seen_ids: Sequence[str]) -> int:
        """Archive legacy-CMS offerings absent from the feed, with a blast-radius guard.

        The legacy Drupal feed is the authority for which imported classes still exist,
        so anything it stops listing gets archived. That single statement is enormously
        destructive at our scale — practically every offering carries a ``legacy_cms_id``
        — and an *unsuccessful-but-HTTP-200* fetch is entirely plausible while the legacy
        CMS is being decommissioned: content unpublished upstream, an auth wall answering
        200 with an empty ``data`` array, a truncated first page. Any of those would
        archive the whole catalog in one write.

        So the sweep is skipped when the feed listed nothing at all, or when it would
        archive more than :data:`LEGACY_ARCHIVE_GUARD_FRACTION` of the live legacy
        offerings. A tripped guard is logged at ERROR — the sync has no alerting, so that
        log line is the only signal anyone gets.

        Args:
            seen_ids: Legacy node UUIDs present in the feed that was just fetched.

        Returns:
            Number of offerings archived — 0 when nothing was stale or the guard tripped.
        """
        live = self.filter(legacy_cms_id__gt="").exclude(status=ClassOffering.Status.ARCHIVED)
        total = live.count()
        if not total:
            return 0
        stale = live.exclude(legacy_cms_id__in=list(seen_ids))
        stale_count = stale.count()
        if not stale_count:
            return 0
        if not seen_ids or stale_count > total * LEGACY_ARCHIVE_GUARD_FRACTION:
            logger.error(
                "!!! LEGACY CMS SYNC ARCHIVE GUARD TRIPPED — NOTHING WAS ARCHIVED !!! "
                "The feed listed %d class(es), which would have archived %d of %d live "
                "legacy offerings (over the %.0f%% ceiling). This almost always means the "
                "legacy CMS answered with an empty or truncated payload, not that the "
                "classes really went away. Check %s before assuming the catalog shrank.",
                len(seen_ids),
                stale_count,
                total,
                LEGACY_ARCHIVE_GUARD_FRACTION * 100,
                LEGACY_CMS_FEED_LABEL,
            )
            return 0
        return stale.update(status=ClassOffering.Status.ARCHIVED)


_SLUG_RETRY_LIMIT = 5


def _unique_slug(base: str, exclude_pk: int | None) -> str:
    """A slug derived from ``base`` that no other ClassOffering currently holds.

    Returns ``base`` when it is free, otherwise ``base-2``, ``base-3``, … Pass the
    offering's own ``pk`` as ``exclude_pk`` so re-checking a row against itself never
    reads as a collision; ``None`` (a not-yet-saved offering) excludes nothing. This is
    a check-then-set probe that closes the common case but not a genuine race — the save
    paths pair it with :func:`_save_with_unique_slug` to retry on the unique constraint.
    """
    candidate = base
    n = 1
    while True:
        taken = ClassOffering.objects.filter(slug=candidate)
        if exclude_pk is not None:
            taken = taken.exclude(pk=exclude_pk)
        if not taken.exists():
            return candidate
        n += 1
        candidate = f"{base}-{n}"


def _is_slug_unique_violation(error: IntegrityError) -> bool:
    """True when ``error`` is the ClassOffering ``slug`` unique constraint failing.

    Portable across SQLite ("UNIQUE constraint failed: classes_classoffering.slug") and
    PostgreSQL ("duplicate key value ... Key (slug)=…") — both name the ``slug`` column.
    A non-slug integrity error is left to propagate rather than pointlessly retried.
    """
    return "slug" in str(error).lower()


def _save_with_unique_slug(
    offering: "ClassOffering",
    base: str,
    *,
    exclude_pk: int | None,
    save: Callable[[], None],
) -> None:
    """Stamp ``offering`` with a unique slug from ``base`` and persist, retrying on a race.

    Probes with :func:`_unique_slug`, then runs ``save`` inside ``transaction.atomic()`` so
    a losing write never poisons the outer connection. If a concurrent writer claimed the
    same slug between the probe and the save, the unique constraint raises ``IntegrityError``;
    we re-derive the next free suffix (the winning row is now visible) and retry, bounded at
    ``_SLUG_RETRY_LIMIT`` attempts before re-raising. A non-slug integrity error propagates
    immediately.
    """
    for attempt in range(_SLUG_RETRY_LIMIT):
        offering.slug = _unique_slug(base, exclude_pk)
        try:
            with transaction.atomic():
                save()
            return
        except IntegrityError as error:
            if not _is_slug_unique_violation(error) or attempt == _SLUG_RETRY_LIMIT - 1:
                raise


# Keyword → guild-category-name rules for the bulk guild-tagging suggester. Ordered
# most→least specific so a compound term wins for the guild that actually owns it
# (e.g. "Stained Glass" hits Glass before Woodworking's "\bstained" would ever apply).
# The first entry whose pattern matches the title+description AND whose named category
# exists wins; see ``ClassOffering.suggest_guild_category``.
GUILD_CATEGORY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("Art Framing", r"\bfram(?:e|ing)\b|\bshadowbox\b|\bmat\s*cut"),
    ("Glass", r"\bglass|lampwork|borosilicate|\bboro\b|\bfrit\b|\bfus(?:ed|ing)\b|\bstained\b"),
    (
        "Ceramics",
        r"ceramic|\bclay\b|potter|wheel[- ]throw|\bglaz(?:e|ing)|\bteapot\b|hand[- ]?build|\bkiln\b|porcelain",
    ),
    ("Metalworking", r"blacksmith|\bforg(?:e|ing|ed)\b|\bweld|\bknife|\bmetal\b|\banvil\b|\bplasma\b|\bsteel\b"),
    ("Leatherworking", r"leather"),
    ("Textiles", r"\bsew|\bknit|crochet|\bquilt|\bweav|\bdye|\bfiber\b|embroider|\bfelt\b|macram|textile|\byarn\b"),
    ("Jewelry", r"jewel|lapidary|silversmith|\bearring|\bring[- ]making"),
    ("Technology", r"\blaser\b|3[dD][- ]print|\bcnc\b|arduino|electronic|microcontroller|\brobot"),
    ("Woodworking", r"\bwood|\blathe\b|\bcarv|joinery|\bspoon\b|sawstop"),
    ("Gardeners", r"\bgarden|\bcompost|\bseed[- ]start|\bplant\b"),
    ("Food Independence", r"\bferment|\bcanning\b|sourdough|\bcheese|\bpickl|\bpreserv(?:e|ing)\b|\bcook"),
    ("Visual Arts", r"\bpaint|\bdraw|printmak|screen[- ]?print|collage|\bsketch|\bacrylic|watercolor"),
    ("Writers", r"\bwrit(?:e|ing|er)|poetry|\bzine\b"),
)

# Patterns compiled once at import; paired 1:1 with GUILD_CATEGORY_KEYWORDS by order.
_COMPILED_GUILD_CATEGORY_KEYWORDS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in GUILD_CATEGORY_KEYWORDS
)


DEFAULT_SALE_BANNER_TEXT = "🔥 Limited-time sale — save on this class while it lasts!"


class ClassOffering(HeroCropMixin, models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending Review"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    class SaleKind(models.TextChoices):
        PERCENT = "percent", "Percent off"
        FIXED = "fixed", "Dollar amount off"

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
    sale_enabled = models.BooleanField(
        default=False, help_text="When on, this class shows a sale banner and charges the sale price."
    )
    sale_kind = models.CharField(
        max_length=10,
        choices=SaleKind.choices,
        default=SaleKind.PERCENT,
        help_text="Percent off the full price, or a flat dollar amount off.",
    )
    sale_percent = models.PositiveIntegerField(
        null=True, blank=True, help_text="Percent off (1–99). Used for a percent-off sale."
    )
    sale_amount_cents = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Flat amount off, in cents (edited in dollars). Must be less than the price. "
        "Used for a dollar-amount sale.",
    )
    sale_banner_text = models.CharField(
        max_length=200,
        blank=True,
        default=DEFAULT_SALE_BANNER_TEXT,
        help_text="The headline shown on the sale banner. Leave blank to use the default.",
    )
    sale_allow_discount_codes = models.BooleanField(
        default=False,
        help_text="Off by default — the sale price can't be combined with other offers. "
        "Turn on to let registrants add a discount code on top of the sale.",
    )
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
        default=False, help_text="When on, registrants also sign photo release."
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
    channel_announced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When this class was announced (or silently marked announced) in the Discord #classes "
            "channel. NULL = not yet announced; the 15-minute announcer picks it up once it is "
            "publicly bookable. The stamp survives unpublish/republish so a class never re-announces."
        ),
    )
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
        help_text="The welcome message sent to each new registrant. Supports rich text formatting.",
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
    def public_url(self) -> str:
        """Absolute URL of this class's readable public page (on the book surface)."""
        from django.urls import reverse

        from classes.emails import _absolute_url

        return _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": self.slug}))

    @property
    def legacy_public_url(self) -> str:
        """This class's page on the legacy Drupal site, or ``""`` for locally-authored offerings.

        The import derives ``slug`` from the Drupal path alias (``/class/<alias>``), appending a
        ``-legacy`` suffix only when that alias collides with an existing local slug — so stripping
        the suffix recovers the alias.
        """
        from classes.import_service import LEGACY_CMS_BASE

        if not self.legacy_cms_id:
            return ""
        return f"{LEGACY_CMS_BASE}/class/{self.slug.removesuffix('-legacy')}"

    @property
    def qr_url(self) -> str:
        """Stable, slug-independent permalink the QR encodes — it redirects to the current
        public page, so a printed QR keeps working even after the class's slug changes."""
        from django.urls import reverse

        from classes.emails import _absolute_url

        return _absolute_url(reverse("classes:class_permalink", kwargs={"pk": self.pk}))

    def qr_svg(self) -> str:
        """Inline, CSS-scalable SVG QR of the class's stable permalink (crisp at any print size)."""
        from membership.qr import qr_svg as render_qr

        return render_qr(self.qr_url)

    def qr_png_bytes(self) -> bytes:
        """PNG bytes of the same QR — a raster download for print/handout."""
        from membership.qr import qr_png_bytes as render_png

        return render_png(self.qr_url)

    @property
    def welcome_email_ready(self) -> bool:
        """True when the instructor welcome email is enabled and has subject + body.

        The send path checks this so an enabled-but-empty welcome email never goes out.
        """
        return bool(
            self.welcome_email_enabled and self.welcome_email_subject.strip() and self.welcome_email_body.strip()
        )

    def announcement_recipients(self, *, include_waitlist: bool = False) -> list["Registration"]:
        """Registrations to notify for a class announcement — the roster behind the composer.

        Every CONFIRMED registrant (and, when ``include_waitlist`` is set, every WAITLISTED
        one too). A registrant with a linked user account gets the in-app bell + push + email;
        an email-only registrant (guest checkout, no account) can only be reached by email.
        Both kinds are returned here — the caller splits them by ``member`` + ``member.user``.
        Ordered confirmed-before-waitlisted, then by name, for a stable checklist.

        Args:
            include_waitlist: When True, waitlisted registrants join the confirmed ones.

        Returns:
            The matching :class:`Registration` rows, each a person to notify.
        """
        statuses = [Registration.Status.CONFIRMED]
        if include_waitlist:
            statuses.append(Registration.Status.WAITLISTED)
        return list(
            self.registrations.filter(status__in=statuses)
            .select_related("member__user")
            .order_by("status", "first_name", "last_name")
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

    def suggest_guild_category(self, categories_by_name: dict[str, "Category"] | None = None) -> "Category | None":
        """Suggest a guild-linked category from keywords in the title + description.

        First matching entry in GUILD_CATEGORY_KEYWORDS wins (ordered most→least
        specific so e.g. "Stained Glass" hits Glass before anything else). Returns
        None when nothing matches or the named category doesn't exist. Pass
        categories_by_name (name → guild-linked Category) to avoid a query per call
        when scanning many offerings.
        """
        if categories_by_name is None:
            categories_by_name = {c.name: c for c in Category.objects.filter(guild__isnull=False)}
        haystack = f"{self.title} {self.description}"
        for name, pattern in _COMPILED_GUILD_CATEGORY_KEYWORDS:
            if pattern.search(haystack):
                category = categories_by_name.get(name)
                if category is not None:
                    return category
        return None

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
        ``on_review_decision_recorded``). An admin can still step in early —
        admin approval is final and publishes immediately, closing any open
        guild-lead gate.

        Notifies the first-stage reviewer (in-app + email) directly from the
        model so every submit path — quick-submit, create, and edit — fans out
        the same way. Returns the freshly created approval row(s) so callers
        can introspect the result.
        """
        if self.status != self.Status.DRAFT:
            raise ValueError(f"Only draft classes can be submitted; got {self.status}.")
        if not self.has_submittable_image:
            from django.core.exceptions import ValidationError

            raise ValidationError(
                "Add photos before submitting — a class needs its own hero image and at least one gallery photo."
            )
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
        from core.events.emit import emit

        activity.log(CmsActivity.Kind.CLASS_ARCHIVED, class_offering=self)
        # In-app broadcast to all active members (the ``class_cancelled`` event resolves
        # ALL_ACTIVE_MEMBERS), but the EMAIL goes ONLY to the people who actually booked
        # the class — including guests with no account, who are unreachable by the
        # resolver. ``suppress_email=True`` is unconditional (not merely implied by a
        # non-empty ``email_to``) so archiving a class nobody booked can never turn into a
        # site-wide email blast to every active member.
        emit(
            "class_cancelled",
            target=self,
            context={
                "member_name": "there",
                "class_title": self.title,
                "class_starts_at": self.cancellation_date_label,
                "classes_url": f"{settings.BOOK_BASE_URL}/classes/",
            },
            url="/classes/",
            email_to=self.registrant_notice_emails,
            suppress_email=True,
            period=f"offering:{self.pk}:archived",
        )

    @property
    def cancellation_date_label(self) -> str:
        """A human date for the cancellation copy ("Saturday, July 12").

        Falls back to a neutral phrase for a flexibly-scheduled class with no session
        rows, so the copy never renders a bare placeholder.
        """
        starts_at = self.earliest_session_at
        if starts_at is None:
            return "its scheduled date"
        return date_format(localtime(starts_at), "l, F j")

    @property
    def registrant_notice_emails(self) -> list[str]:
        """Every registrant address that should hear about a change to this class.

        Drawn from ``Registration.email`` rather than the linked member, so a **guest**
        registrant (no account, ``member`` is ``NULL``) is reached too. Rows that have
        already left the class (cancelled / refunded) are excluded.
        """
        return list(
            self.registrations.filter(
                status__in=[
                    Registration.Status.PENDING,
                    Registration.Status.CONFIRMED,
                    Registration.Status.WAITLISTED,
                ]
            )
            .exclude(email="")
            .values_list("email", flat=True)
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

        APPROVED by an admin: publish immediately — admin approval is final
        (owner decision), even when another gate is still open. Any remaining
        undecided approval rows are closed as approved with a system note so
        nothing lingers in reviewer queues; the rows stay as history.
        APPROVED by a guild lead: escalate to the admin gate without
        publishing — publication always waits for the admin.
        CHANGES_REQUESTED / DENIED: bounce back to DRAFT so the instructor
        can edit and resubmit. Per the locked decision in PLAN.md §14,
        a guild-lead denial is recoverable (returns to DRAFT) rather than
        archival; admin-level archival is a separate explicit action.
        """
        from classes import activity

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
            if row.role == ClassApproval.Role.ADMIN:
                # Admin approval is final: close any still-open gates (e.g. an
                # undecided guild-lead row) as approved with a system note so the
                # class drops out of every reviewer queue. The rows are kept as
                # history — decided_by stays NULL because no human decided them.
                self.approvals.filter(decision="").exclude(pk=row.pk).update(
                    decision=ClassApproval.Decision.APPROVED,
                    notes="Approved automatically when an admin gave final approval.",
                    decided_at=timezone.now(),
                )
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
                # view right after the publishing decision). This separate broadcast is the
                # "a new class is live" in-app fan-out to ALL active members (resolved by the
                # ``class_published`` event); its EMAIL channel defaults off, matching the old
                # in-app-only dispatch.
                from django.urls import reverse

                from classes.emails import _absolute_url
                from core.events.emit import emit

                class_url = _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": self.slug}))
                emit(
                    "class_published",
                    actor=row.decided_by,
                    target=self,
                    context={
                        "class_title": self.title,
                        "class_url": class_url,
                        "class_image_html": self.email_hero_image_html,
                    },
                    url=class_url,
                    period=f"offering:{self.pk}:published",
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
    def sale_is_active(self) -> bool:
        """A sale counts only when switched on, the class is paid, and the matching
        amount is set. The form guarantees consistency, but a stray admin/import edit
        must not crash the catalog — so we re-check the amount defensively."""
        if not self.sale_enabled or self.price_cents <= 0:
            return False
        if self.sale_kind == self.SaleKind.PERCENT:
            return bool(self.sale_percent)
        return bool(self.sale_amount_cents)

    @property
    def sale_price_cents(self) -> int:
        """Public (non-member) price after the sale. Equals price_cents when no sale is
        active, so callers can use it unconditionally in place of price_cents."""
        if not self.sale_is_active:
            return self.price_cents
        if self.sale_kind == self.SaleKind.PERCENT:
            return int(self.price_cents * (100 - self.sale_percent) / 100)  # type: ignore[operator]
        return max(0, self.price_cents - self.sale_amount_cents)  # type: ignore[operator]

    @property
    def sale_savings_display(self) -> str:
        """Short 'what you save' string for the badge/banner pill — '20% off' or '$15 off'.

        Empty string when no sale is active.
        """
        if not self.sale_is_active:
            return ""
        if self.sale_kind == self.SaleKind.PERCENT:
            return f"{self.sale_percent}% off"
        # Whole dollars drop decimals, matching cents_as_price. sale_amount_cents is
        # always > 0 and < price here (guaranteed active + validated).
        dollars, rem = divmod(self.sale_amount_cents, 100)  # type: ignore[operator]
        money = f"${dollars}" if rem == 0 else f"${dollars}.{rem:02d}"
        return f"{money} off"

    @property
    def sale_banner_display(self) -> str:
        """The banner headline to render, always non-empty. The form fills the default
        on blank, but a non-form edit (admin bulk action, shell, CMS import) can leave
        the text empty — so the render path falls back to the default too, and a
        blank-text sale row never shows a headless banner."""
        return self.sale_banner_text.strip() or DEFAULT_SALE_BANNER_TEXT

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
        items.extend(self.gallery_display_images)
        if not items and self.category and self.category.hero_image:
            items.append({"url": self.category.hero_image.url, "alt": self.category.name})
        return items

    @property
    def gallery_display_images(self) -> list[dict]:
        """Gallery rows only — no hero and no category fallback.

        Feeds the gallery block under the public detail page's booking rail, which
        should render nothing at all when the class has no gallery shots of its own
        (the hero already leads the page).
        """
        return [{"url": gi.image.url, "alt": gi.alt_text or self.title} for gi in self.gallery_images.all()]

    @property
    def email_hero_image_html(self) -> SafeString:
        """A styled ``<img>`` of the class's hero image for the class-published email.

        Empty string when the class has no image. Injected as a trusted, app-built
        SafeString into the ``class_published`` copy fragment (the same pattern as the
        voting-results chart) so the constrained copy renderer inserts it verbatim;
        ``format_html`` escapes the URL and alt text. The URL comes from
        :attr:`display_images` (the class's own hero, else a gallery shot, else the
        category hero) and is absolute in production (R2 storage).
        """
        images = self.display_images
        if not images:
            return mark_safe("")
        hero = images[0]
        return format_html(
            '<img src="{}" alt="{}" width="520" '
            'style="width:100%;max-width:520px;height:auto;border-radius:8px;margin:0 0 20px;display:block;">',
            hero["url"],
            hero["alt"],
        )

    @property
    def display_faqs(self) -> list[dict]:
        """Question/answer pairs for the public detail page's Questions section.

        A class's own ``ClassFaq`` rows when it has any; otherwise the site-wide
        ``DEFAULT_CLASS_FAQS``. The arrival FAQ (``ARRIVAL_CLASS_FAQ``) is site
        policy — the building is locked — so it is always appended, even for
        classes with their own FAQ list, unless a custom row already asks the
        same question. Each entry is ``{"question": str, "answer": str}`` with
        plain-text answers (the template runs them through urlize/linebreaks).
        """
        custom = [{"question": faq.question, "answer": faq.answer} for faq in self.faqs.all()]
        faqs = custom or [dict(faq) for faq in DEFAULT_CLASS_FAQS]
        if not any(faq["question"] == ARRIVAL_CLASS_FAQ["question"] for faq in faqs):
            faqs.append(dict(ARRIVAL_CLASS_FAQ))
        return faqs

    @property
    def has_submittable_image(self) -> bool:
        """Whether this class carries the photos required to submit for review.

        True when the offering has BOTH its own hero (``image``) AND at least
        one gallery photo. The Category/Guild-Type hero fallback that
        ``display_images`` leans on is deliberately excluded: a class must
        supply its own photos before it can go to a reviewer.
        """
        return bool(self.image) and self.gallery_images.exists()

    @property
    def needs_photo_nudge(self) -> bool:
        """Whether to gently suggest adding more gallery photos.

        Classes with three or more gallery photos tend to draw more sign-ups,
        so below that we surface a soft suggestion. This is advisory only — it
        never blocks submission (that gate is ``has_submittable_image``).
        """
        return self.gallery_images.count() < 3

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

    def finalize_recurring_slug(self) -> None:
        """Overwrite the provisional slug with the canonical date-stamped one.

        Called once during creation, right after the offering's sessions are
        attached, so the public URL reads ``slugify(title)-YYYY-MM-DD`` where the
        date is the offering's first session date (:attr:`earliest_session_at`,
        resolved in local time). When the offering has no sessions yet, the
        creation date is used instead. A same-day collision with an existing
        slug falls back to a ``-2``, ``-3``, … tiebreak. Full date — not just
        month + year — because the same class can recur several times in one
        month.

        Local time matters: an aware session datetime stored in UTC can fall on
        a different calendar day than the class's local date, so we convert with
        ``localtime`` / ``localdate`` before formatting.

        This is a create-only finalizer — it is never called from an edit flow,
        so a published offering's slug (and any already-indexed URL) never
        changes.
        """
        from django.utils.text import slugify

        when = self.earliest_session_at
        when_date = timezone.localtime(when).date() if when is not None else timezone.localdate()
        base = f"{slugify(self.title) or 'class'}-{when_date:%Y-%m-%d}"
        _save_with_unique_slug(self, base, exclude_pk=self.pk, save=lambda: self.save(update_fields=["slug"]))

    def duplicate(self) -> "ClassOffering":
        """Clone this offering as a fresh draft with a unique slug and title."""
        base_slug = f"{self.slug}-copy"
        self.pk = None
        self.title = f"{self.title} (copy)"
        self.status = self.Status.DRAFT
        self.published_at = None
        self.approved_by = None
        _save_with_unique_slug(self, base_slug, exclude_pk=None, save=self.save)
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
        self.pk = None
        self.status = self.Status.DRAFT
        self.published_at = None
        self.approved_by = None
        self.legacy_cms_id = ""
        _save_with_unique_slug(self, base_slug, exclude_pk=None, save=self.save)
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
    offering (publish when the ADMIN row is APPROVED — admin approval is
    final; back to DRAFT when any reviewer requests changes or denies).
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


# Site-wide starting-point FAQs shown on every class page until the class saves its own
# ClassFaq rows. The class edit form seeds these as editable rows, so instructors can
# reword them or add more; answers are plain text (urlize turns the email into a link).
# Shown on every class page (see ClassOffering.display_faqs) — site policy, not
# per-class copy: the building is locked, so every student needs the arrival drill.
ARRIVAL_CLASS_FAQ: dict = {
    "question": "What do I do once I arrive at Past Lives?",
    "answer": (
        "Our building is secure, and our doors are locked. The instructor will meet you at the "
        "front door to let you in 10 min. before your scheduled class time. If you don't see "
        "anyone, please knock!"
    ),
}

DEFAULT_CLASS_FAQS: list[dict] = [
    {
        "question": "What's your cancellation policy?",
        "answer": (
            "We know plans change, but late cancellations and no-shows leave empty seats that could've "
            "gone to someone on the waitlist, and instructors still prep materials and hold space for "
            "every registered student. Here's how we handle it:\n\n"
            "Canceling with 48+ hours' notice: No fee. Please cancel by emailing classes@pastlives.space "
            "as early as possible so we can offer your spot to someone on the waitlist.\n\n"
            "Canceling with less than 48 hours' notice, or no-shows: We do not offer refunds for late "
            "cancellations and no-shows.\n\n"
            "Emergencies: We understand things come up. Emergency exceptions are handled case-by-case. "
            "Please reach out to us directly, and we'll work with you.\n\n"
            "How to cancel: Email classes@pastlives.space"
        ),
    },
    {
        "question": "Is the space accessible?",
        "answer": (
            "We have a ramp to reach our first floor, but please note that Past Lives Makerspace is not "
            "currently ADA accessible, and we do not have ADA accessible restrooms at this time. If you "
            "have questions about accessing a specific class or space, please reach out to us at "
            "studios@pastlives.space and we'll do our best to help."
        ),
    },
    {
        "question": "Do I need any prior experience or skill level?",
        "answer": (
            "Generally, no! Our classes are designed for all skill levels, from complete beginners to "
            "those looking to refine existing techniques. Each class description will note if any prior "
            "experience is recommended."
        ),
    },
]


class ClassFaq(models.Model):
    """A question/answer pair shown in the public class page's Questions section.

    While a class has no rows, the page falls back to ``DEFAULT_CLASS_FAQS``; the first
    save from the class edit form materializes those defaults as rows, so from then on
    the class fully owns its own list. Deleting every row returns it to the defaults.
    """

    class_offering = models.ForeignKey(
        ClassOffering,
        on_delete=models.CASCADE,
        related_name="faqs",
        help_text="Parent class offering.",
    )
    question = models.CharField(max_length=500, help_text="The question.")
    answer = models.TextField(help_text="The answer (plain text; URLs and emails become links).")
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending; lower shows first.")

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return self.question


class ClassSessionQuerySet(models.QuerySet["ClassSession"]):
    def upcoming_public(self) -> "ClassSessionQuerySet":
        """Future sessions whose offering is publicly visible (published + non-private).

        Mirrors ``ClassOfferingQuerySet.public()`` across the FK (``status="published"``,
        ``is_private=False``) rather than ``bookable()``: a part-started series is no
        longer *bookable* as a whole, but its still-future sessions remain real,
        dated, purchasable inventory and should be counted.

        The ``display_demo_classes`` gate is mirrored here too — this is a second
        member-facing choke-point (the Discord ``/whats-on`` digest reads it), so demo
        (``demo-`` slug) sessions stay hidden unless that site setting is on, exactly
        like ``public()``.
        """
        from core.models import SiteConfiguration

        qs = self.filter(
            starts_at__gte=timezone.now(),
            class_offering__status="published",
            class_offering__is_private=False,
        )
        if not SiteConfiguration.load().display_demo_classes:
            qs = qs.exclude(class_offering__slug__startswith="demo-")
        return qs

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


class DiscountApprover(NamedTuple):
    """A user's discount-approval capability, resolved once so a whole list of codes
    can be checked without a per-row Member query.

    ``approves_any`` is True for an admin/superuser (may approve every code);
    ``self_approves`` is True for a member holding ``can_self_approve_discounts`` (may
    approve only the codes they created). ``user_pk`` is the acting user's pk, used for
    the cheap in-Python ``created_by`` comparison.
    """

    user_pk: int | str | None
    approves_any: bool
    self_approves: bool

    def can_approve(self, code: "DiscountCode") -> bool:
        """Whether this approver may approve ``code`` — no DB query."""
        if self.approves_any:
            return True
        return self.self_approves and code.created_by_id == self.user_pk


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
        default=False,
        help_text=(
            "Codes are usable only once approved. Every new code starts unapproved until an admin — or a "
            "member with the self-approve permission, for their own codes — approves it."
        ),
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
            from core.events.emit import emit

            emit(
                "discount_code.requested",
                actor=self.created_by,
                target=self,
                context={},
                title="A discount code needs approval",
                body=f"The discount code {self.code} was created and needs approval before it can be used.",
                url="/classes/admin/discount-codes/",
                period=f"discount:{self.pk}:requested",
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

    @classmethod
    def approver_for(cls, user: "AbstractBaseUser | AnonymousUser | None") -> DiscountApprover:
        """Resolve ``user``'s approval capability once (a single Member query).

        Callers rendering a list of codes should resolve this once and reuse the
        returned :class:`DiscountApprover` for every row (via ``approver.can_approve(code)``)
        instead of calling :meth:`can_be_approved_by` per row, which repeats the Member
        lookup for the same user N times.

        Admins and superusers may approve any code; every other member may approve only
        the codes they created, and only when they hold the ``can_self_approve_discounts``
        permission. Anonymous or unlinked users can never approve.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return DiscountApprover(user_pk=None, approves_any=False, self_approves=False)
        from membership.models import AdminCapability, Member

        # Fold the DISCOUNT_APPROVER check into the Member lookup as an EXISTS subquery so
        # the whole resolution stays a single query (the docstring's contract) instead of a
        # second round-trip via ``has_admin_capability``.
        member = (
            Member.objects.filter(user_id=cast("int | str", user.pk), status=Member.Status.ACTIVE)
            .annotate(
                _holds_discount_cap=Exists(
                    AdminCapability.objects.filter(
                        member=OuterRef("pk"), capability=AdminCapability.Capability.DISCOUNT_APPROVER
                    )
                )
            )
            .first()
        )
        approves_any = bool(
            getattr(user, "is_superuser", False)
            or (member is not None and (member.is_fog_admin or getattr(member, "_holds_discount_cap")))
        )
        self_approves = member is not None and member.can_self_approve_discounts
        return DiscountApprover(user_pk=user.pk, approves_any=approves_any, self_approves=self_approves)

    def can_be_approved_by(self, user: "AbstractBaseUser | AnonymousUser | None") -> bool:
        """Whether ``user`` may approve (activate) this discount code.

        Convenience for a single-code check (e.g. the approve action guard). When
        checking many codes for one user, resolve :meth:`approver_for` once and call
        ``approver.can_approve(code)`` per row to avoid an N+1 on the Member lookup.

        Args:
            user: The acting user (may be anonymous or ``None``).

        Returns:
            ``True`` when the user is authorized to flip ``is_approved`` on this code.
        """
        return self.approver_for(user).can_approve(self)

    def approve(self, user: "AbstractBaseUser | AnonymousUser | None" = None) -> None:
        """Mark this discount code approved so it becomes usable.

        Approval is a deliberate forward action — an admin, or a member with the
        ``can_self_approve_discounts`` permission approving one of their own
        pending codes. Idempotent: approving an already-approved code is a no-op
        beyond the write.

        Args:
            user: The acting user. Accepted so every call site passes the
                approver, but not recorded — the model has no approver column
                today. Authorization is the caller's responsibility (see
                :meth:`can_be_approved_by`).
        """
        self.is_approved = True
        self.save(update_fields=["is_approved"])

    def unapprove(self) -> None:
        """Revoke approval, returning the code to pending so it can't be used.

        The admin-side counterpart to :meth:`approve` — lets an admin turn an
        approved code back off without deleting it. Idempotent.
        """
        self.is_approved = False
        self.save(update_fields=["is_approved"])


class Waiver(models.Model):
    class Kind(models.TextChoices):
        LIABILITY = "liability", "Liability"
        MODEL_RELEASE = "model_release", "Photo Release"

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


class Registration(models.Model):
    # Transient (non-persisted) attribute a view sets before a status-changing
    # save() to attribute a confirm/refund action in the audit feed. Unset on a
    # fresh instance — read via getattr(..., None).
    _acting_user: "User | None"
    # Transient flag ``promote_from_waitlist`` sets around its save() so the
    # CONFIRMED-transition dispatch logs WAITLIST_PROMOTED instead of the
    # payment-flavored REGISTRATION_CONFIRMED. Unset elsewhere — read via getattr.
    _promoting: bool

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
    payment_due_cents = models.PositiveIntegerField(
        default=0,
        help_text=(
            "What this registration owes, stamped at promote time. 0 = nothing owed "
            "(normal flow, free class, or fully settled at registration)."
        ),
    )
    payment_link_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Last time a payment-link email was sent for this registration. Display-only "
            "('Link sent Aug 26'); dedupe lives in the emit period."
        ),
    )
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
        help_text=(
            "Is this registration a newsletter opt-in? True when the registrant ticked the box, and also "
            "when the box was hidden because they had already opted in elsewhere."
        ),
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
                if getattr(self, "_promoting", False):
                    # A staff promote is not a "Payment confirmed" event — log the
                    # dedicated WAITLIST_PROMOTED row (with what the seat now owes)
                    # instead, so the feed never double-rows the transition.
                    activity.log(
                        CmsActivity.Kind.WAITLIST_PROMOTED,
                        class_offering=self.class_offering,
                        registration=self,
                        actor=acting,
                        payload={"due_cents": self.payment_due_cents},
                    )
                else:
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
                # The refund RECEIPT no longer emits here: it lives with the
                # PaymentRefund row's succeeded transition (billing.refunds), which
                # knows the ACTUAL refunded amount and gives each refund a unique
                # dedupe period so a second partial's receipt still delivers. This
                # save-transition keeps only the audit log above.

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

    # --- Roster management (staff promote / mark-paid / remove) -------------

    @property
    def balance_due_cents(self) -> int:
        """Cents still owed — the stamped promote-time price minus what has been paid."""
        return max(0, self.payment_due_cents - self.amount_paid_cents)

    @property
    def is_unpaid(self) -> bool:
        """True for a CONFIRMED seat-holder who still owes money (promoted, not yet settled)."""
        return self.status == self.Status.CONFIRMED and self.balance_due_cents > 0

    def compute_promote_price_cents(self) -> int:
        """What this registrant owes if promoted now — mirrors the register form's price engine.

        Uses STORED state (the offering's sale price, this registration's linked
        member, and any discount code stored at waitlist join): sale price first,
        then the member percentage, then the code — unless an active sale blocks
        codes (``sale_allow_discount_codes`` off), in which case the stored code is
        ignored exactly as the form would have refused it. The code is applied as
        stored, with no re-validation — the person entered it in good faith.
        """
        offering = self.class_offering
        price = offering.sale_price_cents
        if self.member is not None and offering.member_discount_pct:
            price = int(price * (100 - offering.member_discount_pct) / 100)
        sale_blocks_codes = offering.sale_is_active and not offering.sale_allow_discount_codes
        if self.discount_code is not None and not sale_blocks_codes:
            price = self.discount_code.apply_to(price)
        return max(0, price)

    def promote_from_waitlist(self, actor: "User | None") -> None:
        """Staff-pick this waitlisted person straight into the class — instantly CONFIRMED.

        Stamps ``payment_due_cents`` from :meth:`compute_promote_price_cents` so the
        deal is frozen at promote time, and logs WAITLIST_PROMOTED (via the
        ``_promoting`` dispatch branch) instead of the payment-flavored confirm row.
        Sends NO email — the caller chooses which promoted email goes out (pay-link
        vs plain), keeping "the registrant hears exactly once" honest. Never fires
        claim links (confirming consumes a seat; only cancel/refund paths promote).
        Over-capacity is allowed — the UI warns, staff know the room.

        Raises:
            RegistrationStateError: If this registration is not WAITLISTED (a
                double-click, stale row, or concurrent promote — first one wins).
        """
        from classes.exceptions import RegistrationStateError

        with transaction.atomic():
            # Guard on a locked refetch, not the in-memory copy — two concurrent
            # promotes serialize here and the second sees the flipped status.
            current = type(self)._default_manager.select_for_update().get(pk=self.pk)
            if current.status != self.Status.WAITLISTED:
                raise RegistrationStateError("Only waitlisted registrations can be added to the class.")
            self.payment_due_cents = self.compute_promote_price_cents()
            self.status = self.Status.CONFIRMED
            self.confirmed_at = timezone.now()
            self._acting_user = actor
            self._promoting = True
            try:
                self.save(update_fields=["payment_due_cents", "status", "confirmed_at"])
            finally:
                self._promoting = False

    def mark_paid(self, actor: "User | None", note: str = "") -> None:
        """Settle an unpaid promoted registration by hand (cash, comped, check).

        Sets ``amount_paid_cents`` to the stamped ``payment_due_cents``, logs
        REGISTRATION_MARKED_PAID (actor + optional method note — who/when live on
        the activity row), and bumps the stored discount code's use count exactly
        once, matching the online-payment path. No email — the staff member is
        standing next to the cash box; the activity feed is the record.

        Raises:
            RegistrationStateError: If nothing is owed (unpaid → paid is one-way) —
                including when an in-flight online payment settled the row between
                the caller's fetch and this call (the webhook holds the same lock,
                so the two settlements serialize and the loser hears about it).
        """
        from classes import activity
        from classes.exceptions import RegistrationStateError

        with transaction.atomic():
            # Guard on a locked refetch, not the in-memory copy — the balance
            # webhook runs under the same select_for_update, so a cash mark-paid
            # racing an online payment can never record both silently.
            current = type(self)._default_manager.select_for_update().get(pk=self.pk)
            if not current.is_unpaid:
                raise RegistrationStateError("This registration has no outstanding balance.")
            code_not_yet_counted = current.amount_paid_cents == 0
            self.payment_due_cents = current.payment_due_cents
            self.amount_paid_cents = current.payment_due_cents
            self.save(update_fields=["amount_paid_cents"])
            activity.log(
                CmsActivity.Kind.REGISTRATION_MARKED_PAID,
                class_offering=self.class_offering,
                registration=self,
                actor=actor,
                payload={"note": note},
            )
            if self.discount_code_id and code_not_yet_counted:
                DiscountCode.objects.filter(pk=self.discount_code_id).update(use_count=F("use_count") + 1)
                activity.log(
                    CmsActivity.Kind.DISCOUNT_CODE_REDEEMED,
                    class_offering=self.class_offering,
                    registration=self,
                    payload={"code": self.discount_code.code},  # type: ignore[union-attr]  # discount_code_id guard ensures non-None
                )

    def remove_by_staff(self, actor: "User | None", reason: str = "") -> None:
        """Staff-remove this registrant: wraps :meth:`cancel`, then sends the removal notice.

        The removal email lives at THIS layer only, so self-serve cancels and
        refund flows keep their current email behavior untouched. ``cancel`` frees
        the seat, logs the cancel/waitlist-left activity, and fires the auto
        claim-link email to the next un-notified waitlister when a seat opens.

        Raises:
            RegistrationStateError: If this registration is already cancelled/refunded.
        """
        from classes.exceptions import RegistrationStateError

        if self.status not in (self.Status.CONFIRMED, self.Status.PENDING, self.Status.WAITLISTED):
            raise RegistrationStateError(f"This registration is already {self.get_status_display().lower()}.")
        was_waitlisted = self.status == self.Status.WAITLISTED
        self.cancel(reason=reason, actor=actor)
        from classes.emails import send_removal_notice

        send_removal_notice(self, was_waitlisted=was_waitlisted)

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

        try:
            from classes.services.mailchimp_subscribe import unsubscribe_registration

            unsubscribe_registration(self)
        except Exception:
            # Mailchimp must never block a cancellation, but a failure here (a bug or an
            # unexpected error) must not vanish — log it with a traceback instead.
            logger.exception("Mailchimp unsubscribe failed for registration %s", self.pk)

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

    # --- Refund engine surface (billing.refunds.RefundableSource) -----------

    @property
    def amount_refunded_cents(self) -> int:
        """Sum of succeeded refunds against this registration's payment.

        Iterates ``refunds.all()`` (not an aggregate) so a ``prefetch_related``
        caller pays no extra query per row.
        """
        from billing.models import PaymentRefund

        return sum(r.amount_cents for r in self.refunds.all() if r.status == PaymentRefund.Status.SUCCEEDED)

    @property
    def refundable_cents(self) -> int:
        """Cents still available to refund — the paid amount minus succeeded refunds."""
        return self.amount_paid_cents - self.amount_refunded_cents

    @property
    def refund_state(self) -> str:
        """``"none" | "partial" | "full" | "failed"`` — the panel/badge vocabulary.

        ``"failed"``: the latest refund attempt is FAILED and no succeeded refund
        has since covered that amount (a later succeeded refund would be the
        latest row). Deliberately NOT a new ``Status`` value — a partially
        refunded registration is still CONFIRMED (the person is still attending),
        and a REFUNDED registration whose covering refund later failed is exactly
        what the Retry action exists for.
        """
        from billing.models import PaymentRefund

        refunds = list(self.refunds.all())  # newest first per PaymentRefund.Meta.ordering
        latest = refunds[0] if refunds else None
        if latest is not None and latest.status == PaymentRefund.Status.FAILED and self.refundable_cents > 0:
            return "failed"
        if self.amount_refunded_cents == 0:
            return "none"
        if self.refundable_cents == 0:
            return "full"
        return "partial"

    @property
    def refund_payment_intent_id(self) -> str:
        """The Stripe PaymentIntent id refunds are issued against (blank when unpaid)."""
        return self.stripe_payment_id

    def refund_receipt_context(self) -> dict[str, Any]:
        """The documented context keys the shared refund service reads (see the protocol)."""
        from django.urls import reverse

        from classes.emails import _absolute_url

        guest_name = f"{self.first_name} {self.last_name}".strip()
        return {
            "item_title": self.class_offering.title,
            "recipient_email": self.email,
            "recipient_name": self.first_name or "there",
            "payer_name": self.member.display_name if self.member is not None else (guest_name or self.email),
            "member": self.member,
            "manage_url": _absolute_url(reverse("classes:my_registration", kwargs={"token": self.self_serve_token})),
            "in_app_url": "/classes/account/",
        }

    def on_fully_refunded(self, reason: str, actor: "User | None") -> None:
        """Full-refund bookkeeping: status to REFUNDED, seat freed, waitlist promoted."""
        self.mark_refunded(reason=reason, actor=actor)

    def issue_refund(
        self, *, amount_cents: int | None = None, reason: str = "", actor: "User | None" = None
    ) -> "PaymentRefund":
        """Send a real Stripe refund for this registration — full when ``amount_cents`` is ``None``.

        Thin delegate: the shared billing-side service owns locking, the Stripe
        call, ledger-row lifecycle, the receipt email, and full-refund
        bookkeeping. See :func:`billing.refunds.issue_refund` for the exceptions.
        """
        from billing.refunds import issue_refund

        return issue_refund(self, amount_cents=amount_cents, reason=reason, actor=actor)

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
        REGISTRATION_PARTIAL_REFUND = "registration_partial_refund", "Partially refunded"
        REGISTRATION_REFUND_FAILED = "registration_refund_failed", "Refund failed"
        REGISTRATION_MOVED = "registration_moved", "Moved"
        REGISTRATION_MARKED_PAID = "registration_marked_paid", "Marked paid"
        PAYMENT_LINK_SENT = "payment_link_sent", "Payment link sent"
        DUPLICATE_PAYMENT = "duplicate_payment", "Duplicate payment received"
        WAITLIST_JOINED = "waitlist_joined", "Joined waitlist"
        WAITLIST_NOTIFIED = "waitlist_notified", "Notified of open spot"
        WAITLIST_LEFT = "waitlist_left", "Left waitlist"
        WAITLIST_PROMOTED = "waitlist_promoted", "Promoted from waitlist"
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
