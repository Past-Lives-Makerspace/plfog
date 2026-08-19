"""Specs for the ``/create-event`` slash command handler (membership.discord_commands).

Covers the command definition, every cheap-validation reply (returned immediately, before the
defer), the guild + date/time resolution helpers, and the deferred publish/propose/email
fan-out delivered via the Discord callback + followup REST calls (mocked with ``respx``).
"""

from __future__ import annotations

import json
from datetime import date, time, timedelta
from unittest.mock import patch

import httpx
import pytest
import respx

from core.events.discord_commands import dispatch
from core.models import NotificationPreference, SiteConfiguration
from membership.discord_commands import (
    _GENERAL_VALUE,
    CREATE_EVENT,
    _create_event,
    _parse_event_date,
    _parse_time,
    _parse_when,
    _resolve_target_guild,
)
from membership.models import CommunityEvent, Member
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db

_CALLBACK_URL = "https://discord.com/api/v10/interactions/intA/tokB/callback"
_FOLLOWUP_URL = "https://discord.com/api/v10/webhooks/appX/tokB/messages/@original"


def _interaction(*, interaction_id=None, token=None, channel_id=None, **options: object) -> dict:
    opts = [{"name": name, "value": value} for name, value in options.items()]
    interaction: dict = {"data": {"options": opts}}
    if interaction_id is not None:
        interaction["id"] = interaction_id
    if token is not None:
        interaction["token"] = token
    if channel_id is not None:
        interaction["channel_id"] = channel_id
    return interaction


def _content(member: object, **options: object) -> str:
    return _create_event(_interaction(**options), member)["data"]["content"]


def _set_policy(value: str) -> None:
    config = SiteConfiguration.load()
    config.member_event_policy = value
    config.save()


def _discord_settings(settings) -> None:
    settings.DISCORD_BOT_TOKEN = "bot"
    settings.DISCORD_CLIENT_ID = "appX"
    settings.MEMBER_BASE_URL = "https://members.example"


def _mock_discord() -> object:
    """Mock the deferred-ack callback + the followup PATCH; return the followup route."""
    respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(204))
    return respx.patch(_FOLLOWUP_URL).mock(return_value=httpx.Response(200, json={"id": "m"}))


def _followup_content(followup) -> str:
    return json.loads(followup.calls.last.request.content)["content"]


# --- Command definition -------------------------------------------------------


def describe_command_definition():
    def it_is_link_gated_ephemeral_and_not_deferred():
        assert CREATE_EVENT.name == "create-event"
        assert (CREATE_EVENT.requires_link, CREATE_EVENT.ephemeral, CREATE_EVENT.defer) == (True, True, False)
        assert CREATE_EVENT.scope == "guild"

    def it_exposes_the_expected_options_with_required_ones_first():
        opts = CREATE_EVENT.to_api_dict()["options"]
        assert {o["name"] for o in opts} == {
            "title",
            "date",
            "start_time",
            "end_time",
            "duration_minutes",
            "details",
            "guild",
            "calendar",
            "email",
        }
        required_flags = [o.get("required", False) for o in opts]
        assert required_flags[:3] == [True, True, True]
        assert not any(required_flags[3:])

    def it_offers_a_general_choice_plus_active_guild_slugs():
        GuildFactory(name="Ceramics Guild")
        guild_opt = next(o for o in CREATE_EVENT.to_api_dict()["options"] if o["name"] == "guild")
        values = {c["value"] for c in guild_opt["choices"]}
        assert _GENERAL_VALUE in values
        assert "ceramics-guild" in values


# --- Parsing helpers ----------------------------------------------------------


def describe_parse_helpers():
    def it_parses_iso_dates():
        assert _parse_event_date("2026-08-01") == date(2026, 8, 1)

    def it_rejects_a_malformed_date():
        assert _parse_event_date("2026/08/01") is None

    def it_parses_24_hour_and_am_pm_times():
        assert _parse_time("18:00") == time(18, 0)
        assert _parse_time("6:00 PM") == time(18, 0)
        assert _parse_time("6:00PM") == time(18, 0)
        assert _parse_time("6pm") == time(18, 0)
        assert _parse_time("6 PM") == time(18, 0)

    def it_rejects_an_unparseable_time():
        assert _parse_time("nope") is None


def describe_parse_when():
    def it_defaults_to_a_sixty_minute_length():
        start, end = _parse_when(_interaction(date="2026-08-01", start_time="18:00"))
        assert end - start == timedelta(minutes=60)

    def it_honors_an_explicit_duration():
        start, end = _parse_when(_interaction(date="2026-08-01", start_time="18:00", duration_minutes=90))
        assert end - start == timedelta(minutes=90)

    def it_honors_an_explicit_end_time():
        start, end = _parse_when(_interaction(date="2026-08-01", start_time="18:00", end_time="20:30"))
        assert (end.hour, end.minute) == (20, 30)

    def it_returns_none_on_a_bad_start_time():
        assert _parse_when(_interaction(date="2026-08-01", start_time="nope")) is None

    def it_returns_none_on_a_bad_date():
        assert _parse_when(_interaction(date="nope", start_time="18:00")) is None

    def it_returns_none_on_a_bad_end_time():
        assert _parse_when(_interaction(date="2026-08-01", start_time="18:00", end_time="nope")) is None


def describe_resolve_target_guild():
    def it_returns_no_guild_for_the_general_choice():
        guild, error = _resolve_target_guild(_interaction(guild=_GENERAL_VALUE))
        assert (guild, error) == (None, None)

    def it_resolves_an_explicit_slug():
        target = GuildFactory(name="Metals")
        guild, error = _resolve_target_guild(_interaction(guild=target.slug))
        assert guild == target and error is None

    def it_errors_on_an_unknown_slug():
        GuildFactory(name="Real Guild")
        guild, error = _resolve_target_guild(_interaction(guild="ghost"))
        assert guild is None
        assert "couldn't find an active guild" in error["data"]["content"]

    def it_falls_back_to_the_channel_guild_when_omitted():
        target = GuildFactory(name="Wood")
        target.discord_channel_id = "chan-wood"
        target.save(update_fields=["discord_channel_id"])
        guild, error = _resolve_target_guild({"data": {"options": []}, "channel_id": "chan-wood"})
        assert guild == target and error is None

    def it_returns_no_guild_when_the_channel_has_no_mapping():
        guild, error = _resolve_target_guild({"data": {"options": []}, "channel_id": "unknown"})
        assert (guild, error) == (None, None)


# --- Cheap validation (immediate replies, no defer) ---------------------------


def describe_cheap_validation():
    def it_reports_when_the_linked_account_has_no_user():
        member = MemberFactory()  # unlinked → member.user is None
        assert "isn't fully set up" in _content(member, title="X", date="2099-08-01", start_time="18:00")

    def it_reports_an_unknown_guild(linked_member):
        member = linked_member()
        content = _content(member, title="X", date="2099-08-01", start_time="18:00", guild="ghost")
        assert "couldn't find an active guild" in content

    def it_reports_an_unparseable_date(linked_member):
        member = linked_member()
        content = _content(member, title="X", date="not-a-date", start_time="18:00")
        assert "couldn't read that date or time" in content

    def it_reports_an_unparseable_start_time(linked_member):
        member = linked_member()
        content = _content(member, title="X", date="2099-08-01", start_time="nope")
        assert "couldn't read that date or time" in content

    def it_reports_an_unparseable_end_time(linked_member):
        member = linked_member()
        content = _content(member, title="X", date="2099-08-01", start_time="18:00", end_time="nope")
        assert "couldn't read that date or time" in content

    def it_rejects_an_end_before_the_start(linked_member):
        member = linked_member()
        content = _content(
            member, title="X", date="2099-08-01", start_time="20:00", end_time="18:00", guild=_GENERAL_VALUE
        )
        assert "End time must be after the start" in content

    def it_refuses_a_non_lead_under_the_disabled_policy(linked_member):
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        member = linked_member()
        content = _content(member, title="X", date="2099-08-01", start_time="18:00", guild=_GENERAL_VALUE)
        assert "limited to guild leads and admins" in content

    def it_rejects_a_title_longer_than_200_characters(linked_member):
        member = linked_member()
        content = _content(member, title="x" * 201, date="2099-08-01", start_time="18:00", guild=_GENERAL_VALUE)
        assert "200 characters" in content
        assert "Nothing was created" in content
        assert not CommunityEvent.objects.exists()


# --- Deferred publish / propose fan-out ---------------------------------------


def describe_authoring_published_events():
    @respx.mock
    def it_publishes_immediately_for_an_admin_even_when_disabled(settings, linked_member):
        _discord_settings(settings)
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Ceramics")

        result = _create_event(
            _interaction(
                interaction_id="intA",
                token="tokB",
                title="Potluck",
                date="2099-08-01",
                start_time="18:00",
                duration_minutes=120,
                guild=guild.slug,
            ),
            admin,
        )

        assert result == {}
        assert "live on the Community Calendar" in _followup_content(followup)
        event = CommunityEvent.objects.get(title="Potluck")
        assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED
        assert event.event_type == CommunityEvent.EventType.GUILD_MEETING
        assert event.guild == guild
        assert event.ends_at - event.starts_at == timedelta(minutes=120)

    @respx.mock
    def it_lets_a_guild_lead_publish_their_guilds_event(settings, linked_member):
        _discord_settings(settings)
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        followup = _mock_discord()
        lead = linked_member()
        guild = GuildFactory(name="Fibers")
        guild.guild_lead = lead
        guild.save(update_fields=["guild_lead"])

        _create_event(
            _interaction(
                interaction_id="intA",
                token="tokB",
                title="Fiber Night",
                date="2099-08-01",
                start_time="18:00",
                guild=guild.slug,
            ),
            lead,
        )
        assert "live on the Community Calendar" in _followup_content(followup)
        assert CommunityEvent.objects.get(title="Fiber Night").moderation_state == (
            CommunityEvent.ModerationState.PUBLISHED
        )

    @respx.mock
    def it_creates_a_site_wide_community_event_for_general(settings, linked_member):
        _discord_settings(settings)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)

        _create_event(
            _interaction(
                interaction_id="intA",
                token="tokB",
                title="One Mic Night",
                date="2099-08-01",
                start_time="18:00",
                guild=_GENERAL_VALUE,
                calendar="public",
            ),
            admin,
        )
        assert "live on the Community Calendar" in _followup_content(followup)
        event = CommunityEvent.objects.get(title="One Mic Night")
        assert event.event_type == CommunityEvent.EventType.COMMUNITY
        assert event.guild is None
        assert event.google_calendar_target == CommunityEvent.GoogleCalendarTarget.PUBLIC

    @respx.mock
    def it_saves_the_details_option_as_the_event_description(settings, linked_member):
        _discord_settings(settings)
        _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Print")

        _create_event(
            _interaction(
                interaction_id="intA",
                token="tokB",
                title="Zine Night",
                details="Bring paper and a stapler.",
                date="2099-08-01",
                start_time="18:00",
                guild=guild.slug,
            ),
            admin,
        )
        assert CommunityEvent.objects.get(title="Zine Night").description == "Bring paper and a stapler."


def describe_member_proposals():
    @respx.mock
    def it_submits_for_review_under_the_approval_policy(settings, linked_member):
        _discord_settings(settings)
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        followup = _mock_discord()
        member = linked_member()

        result = _create_event(
            _interaction(
                interaction_id="intA",
                token="tokB",
                title="My Proposal",
                date="2099-08-01",
                start_time="18:00",
                guild=_GENERAL_VALUE,
            ),
            member,
        )
        assert result == {}
        assert "submitted for review" in _followup_content(followup)
        event = CommunityEvent.objects.get(title="My Proposal")
        assert event.moderation_state == CommunityEvent.ModerationState.PENDING
        assert event.submitted_by == member.user

    @respx.mock
    def it_publishes_directly_under_the_open_policy(settings, linked_member):
        _discord_settings(settings)
        _set_policy(SiteConfiguration.MemberEventPolicy.OPEN)
        followup = _mock_discord()
        member = linked_member()

        _create_event(
            _interaction(
                interaction_id="intA",
                token="tokB",
                title="Open Proposal",
                date="2099-08-01",
                start_time="18:00",
                guild=_GENERAL_VALUE,
            ),
            member,
        )
        assert "live on the Community Calendar" in _followup_content(followup)
        assert CommunityEvent.objects.get(title="Open Proposal").moderation_state == (
            CommunityEvent.ModerationState.PUBLISHED
        )


def describe_the_also_email_option():
    @respx.mock
    def it_emails_the_guild_members_and_reports_the_count(settings, linked_member, mailoutbox):
        _discord_settings(settings)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Glass")
        # Guild-event emails are ON by default, so a member on the default already gets the
        # spine launch email; the "also email" escalation adds only members who opted out. Opt
        # both out so the escalation force-reaches them and reports the count.
        for _ in range(2):
            member = linked_member()
            GuildMembershipFactory(guild=guild, member=member)
            NotificationPreference.objects.create(
                user=member.user, event_key="event.guild_published", channel="email", enabled=False
            )

        _create_event(
            _interaction(
                interaction_id="intA",
                token="tokB",
                title="Glass Meetup",
                date="2099-08-01",
                start_time="18:00",
                guild=guild.slug,
                email="guild_members",
            ),
            admin,
        )
        assert "Emailed 2 members" in _followup_content(followup)
        assert len(mailoutbox) == 2


def describe_post_publish_email_failure():
    @respx.mock
    def it_reports_the_event_live_when_the_email_fan_out_raises(settings, linked_member):
        _discord_settings(settings)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Solder")

        with patch.object(CommunityEvent, "email_announcement", side_effect=Exception("smtp down")):
            result = _create_event(
                _interaction(
                    interaction_id="intA",
                    token="tokB",
                    title="Repair Cafe",
                    date="2099-08-01",
                    start_time="18:00",
                    guild=guild.slug,
                    email="all_active",
                ),
                admin,
            )

        assert result == {}
        content = _followup_content(followup)
        assert "live on the Community Calendar" in content
        assert "went wrong" not in content
        assert "Emailed" not in content
        event = CommunityEvent.objects.get(title="Repair Cafe")
        assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED


def describe_error_handling():
    @respx.mock
    def it_returns_the_generic_error_reply_when_the_fan_out_raises(settings, linked_member):
        _discord_settings(settings)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()

        with patch("membership.discord_commands._finalize_event", side_effect=Exception("boom")):
            result = _create_event(
                _interaction(
                    interaction_id="intA",
                    token="tokB",
                    title="X",
                    date="2099-08-01",
                    start_time="18:00",
                    guild=guild.slug,
                ),
                admin,
            )
        assert result == {}
        assert "went wrong" in _followup_content(followup)


def describe_dispatch_integration():
    def it_shows_the_connect_prompt_for_an_unlinked_member(rf):
        interaction = {
            "type": 2,
            "data": {"name": "create-event", "options": []},
            "member": {"user": {"id": "000"}},
        }
        result = dispatch(interaction, rf.post("/"))
        button = result["data"]["components"][0]["components"][0]
        assert button["url"].endswith("/discord/link/")
