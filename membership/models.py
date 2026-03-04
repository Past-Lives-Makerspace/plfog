from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import DecimalField, Q, Sum, UniqueConstraint, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.text import slugify

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
        ACTIVE = "active", "Active"
        FORMER = "former", "Former"
        SUSPENDED = "suspended", "Suspended"

    class Role(models.TextChoices):
        STANDARD = "standard", "Standard"
        GUILD_LEAD = "guild_lead", "Guild Lead"
        WORK_TRADE = "work_trade", "Work-Trade"
        EMPLOYEE = "employee", "Employee"
        CONTRACTOR = "contractor", "Contractor"
        VOLUNTEER = "volunteer", "Volunteer"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    full_legal_name = models.CharField(max_length=255)
    preferred_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=20, blank=True)
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
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.STANDARD,
    )
    join_date = models.DateField(null=True, blank=True)
    cancellation_date = models.DateField(null=True, blank=True)
    committed_until = models.DateField(null=True, blank=True)
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

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        return self.preferred_name if self.preferred_name else self.full_legal_name

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


# ---------------------------------------------------------------------------
# Guild
# ---------------------------------------------------------------------------


class Guild(models.Model):
    # Queryset annotation (set by GuildAdmin.get_queryset)
    sublet_count: int

    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    intro = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)
    cover_image = models.ImageField(upload_to="guilds/", blank=True)
    icon = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    links = models.JSONField(default=list, blank=True, help_text="List of {name, url} dicts")
    guild_lead = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="led_guilds",
    )
    notes = models.TextField(blank=True)
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
        if not self.slug:
            self.slug = slugify(self.name)
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


class GuildVote(models.Model):
    """Members vote for 3 guilds in priority order."""

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="guild_votes")
    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="votes")
    priority = models.PositiveSmallIntegerField(choices=[(1, "First"), (2, "Second"), (3, "Third")])

    class Meta:
        ordering = ["member", "priority"]
        verbose_name = "Guild Vote"
        verbose_name_plural = "Guild Votes"
        constraints = [
            models.UniqueConstraint(fields=["member", "priority"], name="unique_member_priority"),
            models.UniqueConstraint(fields=["member", "guild"], name="unique_member_guild"),
        ]

    def __str__(self) -> str:
        return f"{self.member} → {self.guild} (#{self.priority})"


# ---------------------------------------------------------------------------
# GuildMembership
# ---------------------------------------------------------------------------


class GuildMembership(models.Model):
    """M2M through table for Guild <-> User membership."""

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="guild_memberships",
    )
    is_lead = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["guild", "user"]
        ordering = ["guild", "user"]
        verbose_name = "Guild Membership"
        verbose_name_plural = "Guild Memberships"

    def __str__(self) -> str:
        role = "Lead" if self.is_lead else "Member"
        return f"{self.user} - {self.guild} ({role})"


# ---------------------------------------------------------------------------
# GuildWishlistItem
# ---------------------------------------------------------------------------


class GuildWishlistItem(models.Model):
    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="wishlist_items")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="wishlist/", blank=True)
    link = models.URLField(blank=True)
    estimated_cost = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    is_fulfilled = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["guild", "-created_at"]
        verbose_name = "Guild Wishlist Item"
        verbose_name_plural = "Guild Wishlist Items"

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# Buyable
# ---------------------------------------------------------------------------


class Buyable(models.Model):
    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="buyables")
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="buyables/", blank=True)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Buyable"
        verbose_name_plural = "Buyables"
        constraints = [UniqueConstraint(fields=["guild", "slug"], name="unique_guild_buyable_slug")]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        PAID = "paid"
        FAILED = "failed"

    buyable = models.ForeignKey(Buyable, on_delete=models.CASCADE, related_name="orders")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    email = models.EmailField(blank=True)
    quantity = models.PositiveIntegerField(default=1)
    amount = models.IntegerField(help_text="Total in cents")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True)
    is_fulfilled = models.BooleanField(default=False)
    fulfilled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="fulfilled_orders",
    )
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Order"
        verbose_name_plural = "Orders"

    def __str__(self) -> str:
        return f"Order #{self.pk} - {self.buyable.name}"


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
    photo = models.ImageField(upload_to="spaces/", blank=True)
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
        return [lease.tenant for lease in active]

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


# ---------------------------------------------------------------------------
# Lease
# ---------------------------------------------------------------------------


class LeaseQuerySet(models.QuerySet):
    def active(self, as_of: date_type | None = None) -> LeaseQuerySet:
        return self.filter(_active_lease_q(today=as_of))


class Lease(models.Model):
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
