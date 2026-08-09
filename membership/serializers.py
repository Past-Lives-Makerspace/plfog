from __future__ import annotations

from typing import ClassVar

from rest_framework import serializers

from membership.models import (
    CommunityEvent,
    Guild,
    GuildAnnouncement,
    Member,
    MembershipPlan,
)


class MembershipPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = MembershipPlan
        fields: ClassVar = ["id", "name", "monthly_price", "deposit_required", "notes", "created_at"]
        read_only_fields: ClassVar = ["id", "created_at"]


class MemberSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    primary_email = serializers.CharField(read_only=True)
    membership_plan_name = serializers.CharField(source="membership_plan.name", read_only=True)

    class Meta:
        model = Member
        fields: ClassVar = [
            "id",
            "display_name",
            "primary_email",
            "preferred_name",
            "full_legal_name",
            "status",
            "fog_role",
            "member_type",
            "membership_plan",
            "membership_plan_name",
            "join_date",
            "show_in_directory",
            "notes",
            "created_at",
        ]
        read_only_fields: ClassVar = [
            "id",
            "display_name",
            "primary_email",
            "full_legal_name",
            "member_type",
            "fog_role",
            "join_date",
            "created_at",
        ]


class MemberWriteSerializer(serializers.ModelSerializer):
    """Writable subset for admin patches. Changes to status/plan may be overwritten by airtable_pull."""

    class Meta:
        model = Member
        fields: ClassVar = ["status", "membership_plan", "notes", "show_in_directory"]


class GuildLeadSummarySerializer(serializers.Serializer):
    """Minimal read-only representation of a guild lead."""

    id = serializers.IntegerField(read_only=True)
    display_name = serializers.CharField(read_only=True)
    primary_email = serializers.CharField(read_only=True)


class GuildSerializer(serializers.ModelSerializer):
    guild_lead = GuildLeadSummarySerializer(read_only=True)

    class Meta:
        model = Guild
        fields: ClassVar = [
            "id",
            "name",
            "slug",
            "is_active",
            "guild_lead",
            "about",
            "contact_email",
            "website_url",
            "created_at",
        ]
        read_only_fields: ClassVar = ["id", "slug", "created_at"]


class CommunityEventSerializer(serializers.ModelSerializer):
    guild_name = serializers.CharField(source="guild.name", read_only=True, default=None)

    class Meta:
        model = CommunityEvent
        fields: ClassVar = [
            "id",
            "title",
            "event_type",
            "guild",
            "guild_name",
            "starts_at",
            "ends_at",
            "location",
            "description",
            "recurrence",
            "moderation_state",
            "google_calendar_target",
            "publish_at",
            "remind_7d",
            "remind_3d",
            "remind_1d",
            "notify_happening_now",
            "sync_state",
            "discord_sync_state",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields: ClassVar = [
            "id",
            "moderation_state",
            "sync_state",
            "discord_sync_state",
            "created_by",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs: dict) -> dict:
        instance = self.instance
        event_type = attrs.get("event_type", instance.event_type if instance else CommunityEvent.EventType.COMMUNITY)
        guild = attrs.get("guild", instance.guild if instance else None)
        guild_required_types = {CommunityEvent.EventType.GUILD_MEETING, CommunityEvent.EventType.STUDIO_HOURS}
        if event_type in guild_required_types and guild is None:
            raise serializers.ValidationError(
                {"guild": "A guild is required for guild_meeting and studio_hours events."}
            )
        if event_type not in guild_required_types and guild is not None:
            raise serializers.ValidationError({"guild": "Community and lead_meeting events must not have a guild."})
        starts_at = attrs.get("starts_at")
        ends_at = attrs.get("ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError({"ends_at": "ends_at must be after starts_at."})
        return attrs


class GuildAnnouncementSerializer(serializers.ModelSerializer):
    guild_name = serializers.CharField(source="guild.name", read_only=True)
    author_name = serializers.CharField(source="author.member.display_name", read_only=True, default=None)

    class Meta:
        model = GuildAnnouncement
        fields: ClassVar = [
            "id",
            "guild",
            "guild_name",
            "author",
            "author_name",
            "title",
            "body",
            "expires_at",
            "send_email",
            "discord_channel",
            "moderation_state",
            "published_at",
            "updated_at",
        ]
        read_only_fields: ClassVar = [
            "id",
            "author",
            "author_name",
            "moderation_state",
            "published_at",
            "updated_at",
        ]
