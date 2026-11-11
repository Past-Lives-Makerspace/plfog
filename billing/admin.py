"""Admin configuration for billing app."""

from __future__ import annotations

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from unfold.admin import ModelAdmin, TabularInline

from .models import BillingSettings, Product, Tab, TabCharge, TabEntry


# ---------------------------------------------------------------------------
# BillingSettings (singleton)
# ---------------------------------------------------------------------------


@admin.register(BillingSettings)
class BillingSettingsAdmin(ModelAdmin):
    """Admin for the singleton BillingSettings model."""

    list_display = ["__str__", "charge_frequency", "default_tab_limit"]
    fieldsets = [
        (
            "Schedule",
            {
                "fields": ["charge_frequency", "charge_time", "charge_day_of_week", "charge_day_of_month"],
                "description": "Controls when the billing cycle runs. Changes take effect on the next cycle.",
            },
        ),
        (
            "Limits & Retries",
            {
                "fields": ["default_tab_limit", "max_retry_attempts", "retry_interval_hours"],
            },
        ),
    ]

    def has_module_permission(self, request: HttpRequest) -> bool:
        return request.user.is_superuser

    def has_view_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return request.user.is_superuser

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return request.user.is_superuser

    def has_add_permission(self, request: HttpRequest) -> bool:
        return request.user.is_superuser and not BillingSettings.objects.exists()

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def changelist_view(self, request: HttpRequest, extra_context: dict | None = None) -> HttpResponse:
        """Redirect the changelist straight to the singleton edit form."""
        from django.shortcuts import redirect

        config = BillingSettings.load()
        return redirect(f"/admin/billing/billingsettings/{config.pk}/change/")


# ---------------------------------------------------------------------------
# Tab
# ---------------------------------------------------------------------------


class TabEntryInline(TabularInline):
    model = TabEntry
    extra = 0
    fields = ["description", "amount", "entry_type", "created_at", "voided_at"]
    readonly_fields = ["description", "amount", "entry_type", "created_at", "voided_at"]
    show_change_link = True
    max_num = 0  # No adding via inline — use the dedicated form

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return super().get_queryset(request).order_by("-created_at")[:20]

    def has_add_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False


@admin.register(Tab)
class TabAdmin(ModelAdmin):
    list_display = ["member", "current_balance_display", "is_locked", "has_payment_method_display", "created_at"]
    list_filter = ["is_locked"]
    search_fields = ["member__full_legal_name", "member__preferred_name", "member___pre_signup_email"]
    readonly_fields = ["stripe_customer_id", "stripe_payment_method_id", "payment_method_last4", "payment_method_brand"]
    inlines = [TabEntryInline]

    fieldsets = [
        (
            None,
            {
                "fields": ["member", "tab_limit", "is_locked", "locked_reason"],
            },
        ),
        (
            "Stripe",
            {
                "fields": [
                    "stripe_customer_id",
                    "stripe_payment_method_id",
                    "payment_method_last4",
                    "payment_method_brand",
                ],
                "classes": ["collapse"],
            },
        ),
    ]

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    @admin.display(description="Balance")
    def current_balance_display(self, obj: Tab) -> str:
        return f"${obj.current_balance}"

    @admin.display(description="Payment Method", boolean=True)
    def has_payment_method_display(self, obj: Tab) -> bool:
        return obj.has_payment_method


# ---------------------------------------------------------------------------
# TabEntry
# ---------------------------------------------------------------------------


@admin.register(TabEntry)
class TabEntryAdmin(ModelAdmin):
    list_display = ["description", "amount", "tab", "entry_type", "created_at", "voided_at"]
    list_filter = ["entry_type"]
    search_fields = ["description", "tab__member__full_legal_name", "tab__member__preferred_name"]
    readonly_fields = ["created_at"]

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False


# ---------------------------------------------------------------------------
# TabCharge
# ---------------------------------------------------------------------------


@admin.register(TabCharge)
class TabChargeAdmin(ModelAdmin):
    list_display = ["tab", "amount", "status", "retry_count", "created_at", "charged_at"]
    list_filter = ["status"]
    search_fields = ["tab__member__full_legal_name", "tab__member__preferred_name"]
    readonly_fields = [
        "stripe_payment_intent_id",
        "stripe_charge_id",
        "stripe_receipt_url",
        "created_at",
        "charged_at",
        "receipt_sent_at",
    ]

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------


@admin.register(Product)
class ProductAdmin(ModelAdmin):
    # Splits are managed via the public guild detail page
    # (``templates/hub/_modal_add_product.html``) for admins /
    # guild leads and staff; this admin change-form is read-mostly for quick lookup/deletion.
    list_display = ["name", "guild_name", "price"]
    list_filter = ["guild"]
    search_fields = ["name", "guild__name"]
    fields = ["name", "guild", "price"]

    @admin.display(description="Guild", ordering="guild__name")
    def guild_name(self, obj: Product) -> str:
        return obj.guild.name
