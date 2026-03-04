from __future__ import annotations

from django.contrib import admin
from django.db.models import Count, QuerySet
from django.http import HttpRequest
from django.urls import reverse
from django.utils.html import format_html
from unfold.admin import GenericTabularInline, ModelAdmin, TabularInline

from .models import (
    Buyable,
    Guild,
    GuildMembership,
    GuildVote,
    GuildWishlistItem,
    Lease,
    Member,
    MembershipPlan,
    Order,
    Space,
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


class GuildMembershipInline(TabularInline):
    model = GuildMembership
    fields = ["user", "is_lead", "joined_at"]
    readonly_fields = ["joined_at"]
    extra = 0


class GuildWishlistItemInline(TabularInline):
    model = GuildWishlistItem
    fields = ["name", "estimated_cost", "is_fulfilled", "created_by", "created_at"]
    readonly_fields = ["created_at"]
    extra = 0


class BuyableInline(TabularInline):
    model = Buyable
    fields = ["name", "slug", "unit_price", "is_active", "created_at"]
    readonly_fields = ["slug", "created_at"]
    extra = 0


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


@admin.register(Guild)
class GuildAdmin(ModelAdmin):
    list_display = ["name", "slug", "is_active", "guild_lead", "view_page_link", "notes_preview"]
    list_filter = ["is_active"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}
    inlines = [LeaseInlineGuild, GuildMembershipInline, GuildWishlistItemInline, BuyableInline]

    @admin.display(description="Page")
    def view_page_link(self, obj: Guild) -> str:
        url = reverse("guild_detail", kwargs={"slug": obj.slug})
        return format_html('<a href="{}">View &rarr;</a>', url)

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
    list_display = ["member", "guild", "priority"]
    list_filter = ["guild", "priority"]


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


# ---------------------------------------------------------------------------
# GuildMembershipAdmin
# ---------------------------------------------------------------------------


@admin.register(GuildMembership)
class GuildMembershipAdmin(ModelAdmin):
    list_display = ["guild", "user", "is_lead", "joined_at"]
    list_filter = ["is_lead", "guild"]
    search_fields = ["guild__name", "user__username"]


# ---------------------------------------------------------------------------
# GuildWishlistItemAdmin
# ---------------------------------------------------------------------------


@admin.register(GuildWishlistItem)
class GuildWishlistItemAdmin(ModelAdmin):
    list_display = ["name", "guild", "estimated_cost", "is_fulfilled", "created_at"]
    list_filter = ["is_fulfilled", "guild"]
    search_fields = ["name", "guild__name"]


# ---------------------------------------------------------------------------
# BuyableAdmin
# ---------------------------------------------------------------------------


@admin.register(Buyable)
class BuyableAdmin(ModelAdmin):
    list_display = ["name", "guild", "unit_price", "is_active", "created_at"]
    list_filter = ["is_active", "guild"]
    search_fields = ["name", "guild__name"]
    prepopulated_fields = {"slug": ("name",)}


# ---------------------------------------------------------------------------
# OrderAdmin
# ---------------------------------------------------------------------------


@admin.register(Order)
class OrderAdmin(ModelAdmin):
    list_display = ["__str__", "buyable", "user", "email", "quantity", "amount", "status", "is_fulfilled", "created_at"]
    list_filter = ["status", "is_fulfilled"]
    search_fields = ["buyable__name", "user__username", "email"]
    readonly_fields = ["stripe_checkout_session_id", "created_at", "paid_at"]
