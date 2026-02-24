from __future__ import annotations

from django.contrib import admin
from django.db.models import Count, QuerySet
from django.http import HttpRequest
from unfold.admin import ModelAdmin, TabularInline

from .models import Lease, Member, MembershipPlan, Space

# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------


class LeaseInlineMember(TabularInline):
    """Lease inline for MemberAdmin — shows space, hides member."""

    model = Lease
    fk_name = "member"
    fields = [
        "space",
        "lease_type",
        "monthly_rent",
        "start_date",
        "end_date",
        "is_active_display",
    ]
    readonly_fields = ["is_active_display"]
    extra = 0

    @admin.display(boolean=True, description="Active")
    def is_active_display(self, obj: Lease) -> bool:
        return obj.is_active


class LeaseInlineSpace(TabularInline):
    """Lease inline for SpaceAdmin — shows member, hides space."""

    model = Lease
    fk_name = "space"
    fields = [
        "member",
        "lease_type",
        "monthly_rent",
        "start_date",
        "end_date",
        "is_active_display",
    ]
    readonly_fields = ["is_active_display"]
    extra = 0

    @admin.display(boolean=True, description="Active")
    def is_active_display(self, obj: Lease) -> bool:
        return obj.is_active


# ---------------------------------------------------------------------------
# MembershipPlanAdmin
# ---------------------------------------------------------------------------


@admin.register(MembershipPlan)
class MembershipPlanAdmin(ModelAdmin):
    list_display = ["name", "monthly_price", "deposit_required", "member_count"]
    search_fields = ["name"]

    def get_queryset(self, request: HttpRequest) -> QuerySet[MembershipPlan]:
        qs = super().get_queryset(request)
        return qs.annotate(member_count=Count("member"))

    @admin.display(description="Members", ordering="member_count")
    def member_count(self, obj: MembershipPlan) -> int:
        return obj.member_count


# ---------------------------------------------------------------------------
# MemberAdmin (N+1 fix: use .with_lease_totals() annotation)
# ---------------------------------------------------------------------------


@admin.register(Member)
class MemberAdmin(ModelAdmin):
    list_display = [
        "display_name",
        "email",
        "membership_plan",
        "status",
        "role",
        "join_date",
        "total_monthly_spend_display",
    ]
    list_filter = ["status", "role", "membership_plan"]
    search_fields = ["full_legal_name", "preferred_name", "email"]
    inlines = [LeaseInlineMember]
    fieldsets = [
        (
            "Personal Info",
            {
                "fields": [
                    "user",
                    "full_legal_name",
                    "preferred_name",
                    "email",
                    "phone",
                ],
            },
        ),
        (
            "Membership",
            {
                "fields": [
                    "membership_plan",
                    "status",
                    "role",
                    "join_date",
                    "cancellation_date",
                    "committed_until",
                ],
            },
        ),
        (
            "Emergency Contact",
            {
                "fields": [
                    "emergency_contact_name",
                    "emergency_contact_phone",
                    "emergency_contact_relationship",
                ],
            },
        ),
        (
            "Notes",
            {
                "fields": ["notes"],
            },
        ),
    ]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Member]:
        qs = super().get_queryset(request)
        return qs.select_related("membership_plan").with_lease_totals()

    @admin.display(description="Name")
    def display_name(self, obj: Member) -> str:
        return obj.display_name

    @admin.display(description="Monthly Spend")
    def total_monthly_spend_display(self, obj: Member) -> str:
        spend = obj.membership_plan.monthly_price + obj.total_monthly_rent
        return f"${spend:.2f}"


# ---------------------------------------------------------------------------
# SpaceAdmin (N+1 fix: use .with_revenue() annotation)
# ---------------------------------------------------------------------------


@admin.register(Space)
class SpaceAdmin(ModelAdmin):
    list_display = [
        "space_id",
        "name",
        "space_type",
        "size_sqft",
        "full_price_display",
        "actual_revenue_display",
        "vacancy_value_display",
        "status",
    ]
    list_filter = ["space_type", "status"]
    search_fields = ["space_id", "name"]
    inlines = [LeaseInlineSpace]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Space]:
        qs = super().get_queryset(request)
        return qs.with_revenue()

    @admin.display(description="Full Price")
    def full_price_display(self, obj: Space) -> str:
        price = obj.full_price
        if price is None:
            return "-"
        return f"${price:.2f}"

    @admin.display(description="Actual Revenue")
    def actual_revenue_display(self, obj: Space) -> str:
        return f"${obj.active_lease_rent_total:.2f}"

    @admin.display(description="Vacancy Value")
    def vacancy_value_display(self, obj: Space) -> str:
        if obj.status == Space.Status.AVAILABLE:
            price = obj.full_price or 0
            return f"${price - obj.active_lease_rent_total:.2f}"
        return "$0.00"


# ---------------------------------------------------------------------------
# LeaseAdmin
# ---------------------------------------------------------------------------


@admin.register(Lease)
class LeaseAdmin(ModelAdmin):
    list_display = [
        "member",
        "space",
        "lease_type",
        "monthly_rent",
        "start_date",
        "end_date",
        "is_active_display",
    ]
    list_filter = ["lease_type"]
    search_fields = ["member__full_legal_name", "space__space_id"]

    @admin.display(boolean=True, description="Active")
    def is_active_display(self, obj: Lease) -> bool:
        return obj.is_active
