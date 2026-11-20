"""Admin for the classes app: the class records behind each permanent class page (#1260)."""

from __future__ import annotations

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from unfold.admin import ModelAdmin

from classes.models import ClassRecord


@admin.register(ClassRecord)
class ClassRecordAdmin(ModelAdmin):
    """One row per class. State is computed, so it is read only; No longer offered is an action."""

    list_display = ["title", "slug", "category", "state", "no_longer_offered", "state_changed_at"]
    list_filter = ["state", "no_longer_offered", "category"]
    search_fields = ["title", "slug"]
    readonly_fields = [
        "slug",
        "state",
        "state_changed_at",
        "no_longer_offered",
        "no_longer_offered_at",
        "no_longer_offered_by",
        "created_at",
        "updated_at",
    ]
    actions = ["mark_no_longer_offered", "offer_again"]

    @admin.action(description="Mark No longer offered (retires the class)")
    def mark_no_longer_offered(self, request: HttpRequest, queryset: QuerySet[ClassRecord]) -> None:
        """Retire each selected class by hand, even one with a date still to come (Felix, 2026-11-20)."""
        for record in queryset:
            record.mark_no_longer_offered(request.user)  # type: ignore[arg-type]  # the admin is signed in
            self.message_user(request, f"{record.title} is no longer offered.", messages.SUCCESS)

    @admin.action(description="Offer again (clear No longer offered)")
    def offer_again(self, request: HttpRequest, queryset: QuerySet[ClassRecord]) -> None:
        """Clear the mark; each class's dates then decide its state."""
        for record in queryset:
            record.offer_again(request.user)  # type: ignore[arg-type]  # the admin is signed in
            self.message_user(request, f"{record.title} is offered again ({record.get_state_display()}).")
