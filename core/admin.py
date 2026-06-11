"""Admin configuration for core app — site settings."""

from __future__ import annotations

from typing import Any

from django import forms as dj_forms
from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from .models import CalendarFeed, SiteConfiguration


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


@admin.register(CalendarFeed)
class CalendarFeedAdmin(ModelAdmin):
    """Direct edit access to CalendarFeed rows.

    Admins normally manage these through Site Settings → Calendar tab; this admin
    is for convenience and read-only auditing of last-fetched timestamps.
    """

    list_display = ["name", "ical_url", "color", "last_fetched_at", "sort_order"]
    readonly_fields = ["last_fetched_at"]
    search_fields = ["name"]


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(ModelAdmin):
    """Admin for the singleton SiteConfiguration model."""

    form = _SiteConfigurationAdminForm
    list_display = ["__str__", "registration_mode"]
    readonly_fields = [
        "general_calendar_last_fetched_at",
        "classes_last_synced_at",
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
            "Classes on the Community Calendar",
            {
                "fields": [
                    "sync_classes_enabled",
                    ("classes_calendar_color", "classes_last_synced_at"),
                ],
                "description": "When enabled, upcoming classes from our catalog appear on the Community Calendar, each linking to its class page. Events refresh automatically every morning — there's nothing to sync by hand.",
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
        """Trigger an immediate sync of every configured CalendarFeed when the URL is set.

        The legacy ``general_calendar_url`` field is kept on the model for one release
        so existing Django-admin workflows continue to function during the migration
        to the new multi-feed list. Either path triggers ``sync_general_calendar``,
        which now iterates every ``CalendarFeed`` row.
        """
        super().save_model(request, obj, form, change)
        from hub.calendar_service import sync_general_calendar

        if obj.general_calendar_url or CalendarFeed.objects.exists():
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
