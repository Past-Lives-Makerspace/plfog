from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

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
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)

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
    join_date = models.DateField()
    cancellation_date = models.DateField(null=True, blank=True)
    committed_until = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

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
    manual_price = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.AVAILABLE,
    )
    photo = models.ImageField(upload_to="spaces/", blank=True)
    floorplan_ref = models.CharField(max_length=100, blank=True)
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
            return self.size_sqft * DEFAULT_PRICE_PER_SQFT
        return None

    @property
    def current_occupants(self) -> models.QuerySet[Member]:
        return Member.objects.filter(pk__in=self.leases.filter(_active_lease_q()).values("member"))

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

    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="leases")
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

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = LeaseQuerySet.as_manager()

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "Lease"
        verbose_name_plural = "Leases"

    def __str__(self) -> str:
        return f"{self.member} @ {self.space} ({self.start_date})"

    @property
    def is_active(self) -> bool:
        today = timezone.now().date()
        if self.start_date is None or self.start_date > today:
            return False
        if self.end_date is not None and self.end_date < today:
            return False
        return True
