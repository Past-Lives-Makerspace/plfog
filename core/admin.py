"""Admin configuration for core app — site settings."""

from __future__ import annotations

from typing import Any

from django import forms as dj_forms
from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse
from django.urls import path
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from .models import SiteConfiguration


_ICAL_TOOLTIP = (
    "In Google Calendar \u2192 Settings \u2192 your calendar \u2192 "
    "\u2018Secret address in iCal format\u2019. Leave blank if not using Google Calendar."
)


class _SiteConfigurationAdminForm(dj_forms.ModelForm):
    """Custom admin form: color pickers for calendar colors, ? tooltip on the iCal URL label."""

    class Meta:
        model = SiteConfiguration
        fields = "__all__"
        widgets = {
            "general_calendar_color": dj_forms.TextInput(
                attrs={"type": "color", "style": "width:56px;height:36px;padding:2px;cursor:pointer;"},
            ),
            "classes_calendar_color": dj_forms.TextInput(
                attrs={"type": "color", "style": "width:56px;height:36px;padding:2px;cursor:pointer;"},
            ),
            "general_calendar_url": dj_forms.URLInput(
                attrs={"placeholder": "https://calendar.google.com/calendar/ical/..."},
            ),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["general_calendar_url"].help_text = ""
        self.fields["general_calendar_url"].label = format_html(
            "General Calendar iCal URL"
            '<span x-data="{{ open: false }}"'
            ' style="position:relative;display:inline-flex;vertical-align:middle;margin-left:5px;">'
            '<button type="button" @mouseenter="open=true" @mouseleave="open=false"'
            ' style="cursor:help;color:#96ACBB;border:1px solid currentColor;border-radius:50%;'
            "width:15px;height:15px;display:inline-flex;align-items:center;justify-content:center;"
            'font-size:10px;font-weight:700;background:none;padding:0;line-height:1;">?</button>'
            '<div x-show="open" x-cloak'
            ' style="position:absolute;bottom:calc(100% + 6px);left:0;'
            "background:#1e2530;color:#e8eaed;font-size:0.8rem;line-height:1.5;"
            "padding:8px 12px;border-radius:6px;min-width:240px;max-width:320px;"
            'box-shadow:0 4px 16px rgba(0,0,0,0.4);z-index:999;white-space:normal;">'
            "{}</div></span>",
            _ICAL_TOOLTIP,
        )


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(ModelAdmin):
    """Admin for the singleton SiteConfiguration model."""

    form = _SiteConfigurationAdminForm
    list_display = ["__str__", "registration_mode"]
    readonly_fields = [
        "general_calendar_last_fetched_at",
        "classes_last_synced_at",
        "sync_classes_button",
    ]
    fieldsets = [
        (
            None,
            {
                "fields": ["registration_mode"],
                "description": "Global settings that control how the site behaves. Changes take effect immediately.",
            },
        ),
        (
            "General Calendar",
            {
                "fields": [
                    "general_calendar_url",
                    ("general_calendar_color", "general_calendar_last_fetched_at"),
                ],
                "description": "Paste the 'Secret address in iCal format' from Google Calendar to sync general makerspace events. Syncs automatically when you save.",
            },
        ),
        (
            "Classes (classes.pastlives.space)",
            {
                "fields": [
                    "sync_classes_enabled",
                    ("classes_calendar_color", "classes_last_synced_at"),
                    "sync_classes_button",
                ],
                "description": "When enabled, upcoming classes are imported as Calendar Events with links to register. Use the sync button to fetch classes manually — this is not done automatically on save because it fetches hundreds of records.",
            },
        ),
        (
            "Integrations",
            {
                "fields": [
                    "mailchimp_api_key",
                    "mailchimp_list_id",
                    "google_analytics_measurement_id",
                ],
                "description": (
                    "Third-party integrations that apply site-wide. MailChimp auto-subscribes new class "
                    "registrants; Google Analytics is injected on every member-facing and public page "
                    "(the Django admin is excluded)."
                ),
            },
        ),
    ]

    def sync_classes_button(self, obj: SiteConfiguration | None) -> str:
        from django.urls import reverse

        url = reverse("admin:core_siteconfiguration_sync_classes")
        return format_html(
            '<a href="{}" style="display:inline-flex;align-items:center;gap:6px;'
            "padding:5px 14px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);"
            'background:rgba(255,255,255,0.06);color:inherit;text-decoration:none;font-size:0.85rem;">'
            "Sync Classes Now</a>",
            url,
        )

    sync_classes_button.short_description = "Manual Sync"  # type: ignore[attr-defined]

    def get_urls(self) -> list:
        urls = super().get_urls()
        custom_urls = [
            path(
                "sync-classes/",
                self.admin_site.admin_view(self._sync_classes_view),
                name="core_siteconfiguration_sync_classes",
            ),
        ]
        return custom_urls + urls

    def _sync_classes_view(self, request: HttpRequest) -> HttpResponse:
        from django.shortcuts import redirect

        from hub.calendar_service import sync_classes_calendar

        config = SiteConfiguration.load()
        try:
            count = sync_classes_calendar()
        except Exception as exc:  # noqa: BLE001
            self.message_user(request, f"Classes sync failed: {type(exc).__name__}: {exc}", messages.WARNING)
        else:
            self.message_user(request, f"Classes synced: {count} session(s) imported.", messages.SUCCESS)
        return redirect(f"/admin/core/siteconfiguration/{config.pk}/change/")

    def has_module_permission(self, request: HttpRequest) -> bool:
        """Only FOG admins (superusers) can see site settings."""
        return request.user.is_superuser

    def has_view_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return request.user.is_superuser

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return request.user.is_superuser

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Prevent adding if the singleton already exists."""
        return request.user.is_superuser and not SiteConfiguration.objects.exists()

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        """Never allow deleting the singleton."""
        return False

    def save_model(self, request: HttpRequest, obj: SiteConfiguration, form: Any, change: bool) -> None:
        """Trigger an immediate general calendar sync when the URL is set."""
        super().save_model(request, obj, form, change)
        from hub.calendar_service import sync_general_calendar

        if obj.general_calendar_url:
            try:
                count = sync_general_calendar()
            except Exception as exc:  # noqa: BLE001
                import urllib.error

                if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                    msg = (
                        "Calendar URL saved, but got a 404 — the calendar isn't publicly accessible. "
                        "In Google Calendar settings, either enable 'Make available to public' "
                        "or use the 'Secret address in iCal format' URL instead."
                    )
                else:
                    msg = f"General calendar sync failed: {type(exc).__name__}: {exc}"
                self.message_user(request, msg, messages.WARNING)
            else:
                self.message_user(request, f"General calendar synced: {count} event(s) imported.", messages.SUCCESS)

    def changelist_view(self, request: HttpRequest, extra_context: dict | None = None) -> HttpResponse:
        """Redirect the changelist straight to the singleton edit form."""
        from django.shortcuts import redirect

        config = SiteConfiguration.load()
        return redirect(f"/admin/core/siteconfiguration/{config.pk}/change/")
