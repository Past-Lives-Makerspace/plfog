from __future__ import annotations

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from unfold.admin import ModelAdmin

from .models import FundingSnapshot, Guild, Member, VotePreference


# ---------------------------------------------------------------------------
# MemberAdmin
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
    ]
    list_filter = ["status", "role", "membership_plan"]
    search_fields = ["full_legal_name", "preferred_name", "email"]
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
        return qs.select_related("membership_plan")

    @admin.display(description="Name")
    def display_name(self, obj: Member) -> str:
        return obj.display_name


# ---------------------------------------------------------------------------
# GuildAdmin
# ---------------------------------------------------------------------------


@admin.register(Guild)
class GuildAdmin(ModelAdmin):
    list_display = ["name", "guild_lead", "notes_preview"]
    search_fields = ["name"]

    @admin.display(description="Notes")
    def notes_preview(self, obj: Guild) -> str:
        if len(obj.notes) > 80:
            return obj.notes[:80] + "..."
        return obj.notes


# ---------------------------------------------------------------------------
# VotePreferenceAdmin
# ---------------------------------------------------------------------------


@admin.register(VotePreference)
class VotePreferenceAdmin(ModelAdmin):
    list_display = ["member", "guild_1st", "guild_2nd", "guild_3rd", "updated_at"]
    search_fields = ["member__full_legal_name", "member__preferred_name"]


# ---------------------------------------------------------------------------
# FundingSnapshotAdmin
# ---------------------------------------------------------------------------


@admin.register(FundingSnapshot)
class FundingSnapshotAdmin(ModelAdmin):
    list_display = ["cycle_label", "snapshot_at", "contributor_count", "funding_pool"]
    readonly_fields = ["snapshot_at", "results"]
