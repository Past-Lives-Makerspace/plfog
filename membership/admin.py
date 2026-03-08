from __future__ import annotations

from django.contrib import admin
from django.db.models import Count, QuerySet
from django.http import HttpRequest
from unfold.admin import GenericTabularInline, ModelAdmin, TabularInline

from .models import (
    Guild,
    GuildVote,
    Lease,
    Member,
    MembershipPlan,
    Space,
    VotingSession,
)

# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------


class LeaseInlineMember(GenericTabularInline):
    """Lease inline for MemberAdmin — shows space, hides tenant fields."""

    model = Lease
    ct_field = "content_type"
    ct_fk_field = "object_id"
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
    """Lease inline for SpaceAdmin — shows tenant, hides space."""

    model = Lease
    fk_name = "space"
    fields = [
        "tenant_display",
        "lease_type",
        "monthly_rent",
        "start_date",
        "end_date",
        "is_active_display",
    ]
    readonly_fields = ["tenant_display", "is_active_display"]
    extra = 0

    @admin.display(description="Tenant")
    def tenant_display(self, obj: Lease) -> str:
        return str(obj.tenant) if obj.tenant else "-"

    @admin.display(boolean=True, description="Active")
    def is_active_display(self, obj: Lease) -> bool:
        return obj.is_active


class LeaseInlineGuild(GenericTabularInline):
    """Lease inline for GuildAdmin."""

    model = Lease
    ct_field = "content_type"
    ct_fk_field = "object_id"
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
                    "billing_name",
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
# GuildAdmin
# ---------------------------------------------------------------------------


class SubletInline(TabularInline):
    """Read-only inline on GuildAdmin showing spaces sublet by the guild."""

    model = Space
    fk_name = "sublet_guild"
    fields = ["space_id", "name", "space_type", "full_price_display"]
    readonly_fields = ["space_id", "name", "space_type", "full_price_display"]
    extra = 0

    def has_add_permission(self, request: HttpRequest, obj: Guild | None = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Guild | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Guild | None = None) -> bool:
        return False

    @admin.display(description="Full Price")
    def full_price_display(self, obj: Space) -> str:
        price = obj.full_price
        if price is None:
            return "-"
        return f"${price:.2f}"


@admin.register(Guild)
class GuildAdmin(ModelAdmin):
    list_display = ["name", "guild_lead", "sublet_count", "notes_preview"]
    search_fields = ["name"]
    inlines = [SubletInline, LeaseInlineGuild]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Guild]:
        qs = super().get_queryset(request)
        return qs.annotate(sublet_count=Count("sublets"))

    @admin.display(description="Sublets", ordering="sublet_count")
    def sublet_count(self, obj: Guild) -> int:
        return obj.sublet_count

    @admin.display(description="Notes")
    def notes_preview(self, obj: Guild) -> str:
        if len(obj.notes) > 80:
            return obj.notes[:80] + "..."
        return obj.notes


# ---------------------------------------------------------------------------
# GuildVoteAdmin
# ---------------------------------------------------------------------------


@admin.register(GuildVote)
class GuildVoteAdmin(ModelAdmin):
    list_display = ["member", "guild", "priority", "session", "created_at"]
    list_filter = ["guild", "priority", "session"]
    search_fields = ["member__full_legal_name", "member__preferred_name"]


@admin.register(VotingSession)
class VotingSessionAdmin(ModelAdmin):
    list_display = ["name", "status", "open_date", "close_date", "eligible_member_count", "votes_cast"]
    list_filter = ["status"]
    search_fields = ["name"]
    readonly_fields = ["airtable_record_id", "created_at"]


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
        "is_rentable",
        "status",
        "sublet_guild",
    ]
    list_filter = ["space_type", "status", "is_rentable", "sublet_guild"]
    search_fields = ["space_id", "name"]
    inlines = [LeaseInlineSpace]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Space]:
        qs = super().get_queryset(request)
        return qs.select_related("sublet_guild").with_revenue()

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
        "tenant_display",
        "space",
        "lease_type",
        "monthly_rent",
        "start_date",
        "end_date",
        "is_active_display",
    ]
    list_filter = ["lease_type"]
    search_fields = ["space__space_id"]

    @admin.display(description="Tenant")
    def tenant_display(self, obj: Lease) -> str:
        return str(obj.tenant) if obj.tenant else "-"

    @admin.display(boolean=True, description="Active")
    def is_active_display(self, obj: Lease) -> bool:
        return obj.is_active
