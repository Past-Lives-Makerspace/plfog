"""Outreach app admin."""

from __future__ import annotations

from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Buyable, BuyablePurchase, Event, Lead, Tour


class TourInline(TabularInline):
    model = Tour
    fields = ["scheduled_at", "claimed_by", "status"]
    extra = 0


class BuyablePurchaseInline(TabularInline):
    model = BuyablePurchase
    fields = ["user", "quantity", "purchased_at"]
    readonly_fields = ["purchased_at"]
    extra = 0


@admin.register(Lead)
class LeadAdmin(ModelAdmin):
    list_display = ["name", "email", "status", "greenlighted_for_membership", "source", "created_at"]
    list_filter = ["status", "greenlighted_for_membership"]
    search_fields = ["name", "email"]
    inlines = [TourInline]


@admin.register(Tour)
class TourAdmin(ModelAdmin):
    list_display = ["lead", "scheduled_at", "claimed_by", "status"]
    list_filter = ["status"]
    search_fields = ["lead__name"]


@admin.register(Event)
class EventAdmin(ModelAdmin):
    list_display = ["name", "guild", "starts_at", "location", "is_published", "is_recurring"]
    list_filter = ["is_published", "is_recurring", "guild"]
    search_fields = ["name"]


@admin.register(Buyable)
class BuyableAdmin(ModelAdmin):
    list_display = ["name", "guild", "formatted_price", "total_quantity_sold", "is_active"]
    list_filter = ["is_active", "guild"]
    search_fields = ["name"]
    inlines = [BuyablePurchaseInline]

    @admin.display(description="Price")
    def formatted_price(self, obj: Buyable) -> str:
        return obj.formatted_price


@admin.register(BuyablePurchase)
class BuyablePurchaseAdmin(ModelAdmin):
    list_display = ["buyable", "user", "quantity", "purchased_at"]
    search_fields = ["buyable__name", "user__username"]
