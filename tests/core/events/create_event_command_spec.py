"""Specs for the ``/create`` slash command (membership.discord_commands).

Covers the command definition, every cheap-validation reply, the preview + Confirm /
Cancel component flow (draft persistence, atomic claim, expiry, authority re-checks),
and the publish / propose / email fan-out behind the type-6 deferred ack (Discord REST
mocked with ``respx``).
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
import respx
from django.core.cache import cache
from django.utils import timezone

from core.abuse_limits import record_keyed_attempt
from core.events.discord_commands import dispatch
from core.models import NotificationPreference, SiteConfiguration
from membership.discord_commands import (
    _CREATE_DAILY_LIMIT,
    _CREATE_HOURLY_LIMIT,
    _CREATE_RATE_SCOPE,
    _GENERAL_VALUE,
    CREATE,
    _create_component,
    _create_event,
    _resolve_target_guild,
)
from membership.models import CommunityEvent, CommunityEventDraft, Member
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db

_CALLBACK_URL = "https://discord.com/api/v10/interactions/intA/tokB/callback"
_FOLLOWUP_URL = "https://discord.com/api/v10/webhooks/appX/tokB/messages/@original"


@pytest.fixture(autouse=True)
def _clear_rate_counters():
    cache.clear()
    yield
    cache.clear()


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


def _preview(member: object, **options: object) -> tuple[str, CommunityEventDraft]:
    """Run /create and return (preview content, the persisted draft)."""
    content = _content(member, **options)
    return content, CommunityEventDraft.objects.latest("pk")


def _confirm(member: object, draft_pk: int, *, action: str = "confirm") -> dict:
    interaction = {"id": "intA", "token": "tokB", "data": {"custom_id": f"create:{action}:{draft_pk}"}}
    return _create_component(interaction, member)


def _set_policy(value: str) -> None:
    config = SiteConfiguration.load()
    config.member_event_policy = value
    config.save()


def _discord_settings(settings) -> None:
    settings.DISCORD_BOT_TOKEN = "bot"
    settings.DISCORD_CLIENT_ID = "appX"
    settings.MEMBER_BASE_URL = "https://members.example"
    # The publish fan-out's channel announcement must not reach a real webhook when a
    # developer's local .env carries one — blank it so the broadcast adapter no-ops.
    settings.DISCORD_NOTIFY_WEBHOOK_URL = ""


def _mock_discord() -> object:
    """Mock the type-6 ack callback + the followup PATCH; return the followup route."""
    respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(204))
    return respx.patch(_FOLLOWUP_URL).mock(return_value=httpx.Response(200, json={"id": "m"}))


def _followup_payload(followup) -> dict:
    return json.loads(followup.calls.last.request.content)


def _exhaust_rate_limit(member) -> None:
    for _ in range(_CREATE_HOURLY_LIMIT):
        record_keyed_attempt(
            _CREATE_RATE_SCOPE, str(member.pk), hourly_limit=_CREATE_HOURLY_LIMIT, daily_limit=_CREATE_DAILY_LIMIT
        )


# --- Command definition -------------------------------------------------------


def describe_command_definition():
    def it_is_link_gated_ephemeral_and_not_deferred():
        assert CREATE.name == "create"
        assert (CREATE.requires_link, CREATE.ephemeral, CREATE.defer) == (True, True, False)
        assert CREATE.scope == "guild"

    def it_exposes_the_expected_options_with_required_ones_first():
        opts = CREATE.to_api_dict()["options"]
        assert {o["name"] for o in opts} == {
            "title",
            "when",
            "duration_minutes",
            "guild",
            "details",
            "location",
            "video_url",
            "recurrence",
            "calendar",
            "email",
        }
        required_flags = [o.get("required", False) for o in opts]
        assert required_flags[:2] == [True, True]
        assert not any(required_flags[2:])

    def it_offers_a_general_choice_plus_active_guild_slugs():
        GuildFactory(name="Ceramics Guild")
        guild_opt = next(o for o in CREATE.to_api_dict()["options"] if o["name"] == "guild")
        values = {c["value"] for c in guild_opt["choices"]}
        assert _GENERAL_VALUE in values
        assert "ceramics-guild" in values

    def it_offers_the_basic_recurrence_subset():
        recurrence_opt = next(o for o in CREATE.to_api_dict()["options"] if o["name"] == "recurrence")
        assert [c["value"] for c in recurrence_opt["choices"]] == ["none", "weekly", "semi_monthly", "monthly"]


# --- Guild resolution ---------------------------------------------------------


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


# --- Cheap validation (immediate replies, nothing persisted) ------------------


def describe_cheap_validation():
    def it_reports_when_the_linked_account_has_no_user():
        member = MemberFactory()  # unlinked → member.user is None
        assert "isn't fully set up" in _content(member, title="X", when="tomorrow 6pm")

    def it_refuses_when_the_member_is_over_the_rate_limit(linked_member):
        member = linked_member()
        _exhaust_rate_limit(member)
        content = _content(member, title="X", when="tomorrow 6pm")
        assert "hit the limit" in content
        assert not CommunityEventDraft.objects.exists()

    def it_reports_an_unknown_guild(linked_member):
        member = linked_member()
        assert "couldn't find an active guild" in _content(member, title="X", when="tomorrow 6pm", guild="ghost")

    def it_reports_an_unreadable_when_phrase(linked_member):
        member = linked_member()
        assert "could not read that date and time" in _content(member, title="X", when="???")

    def it_reports_a_day_with_no_time(linked_member):
        member = linked_member()
        assert "not a start time" in _content(member, title="X", when="next friday")

    def it_reports_a_past_start(linked_member):
        member = linked_member()
        assert "already passed" in _content(member, title="X", when="jan 5 2020 6pm")

    def it_reports_a_start_more_than_a_year_out(linked_member):
        member = linked_member()
        assert "more than a year away" in _content(member, title="X", when="2031-08-29 6pm")

    def it_requires_a_guild_for_the_guild_members_email_choice(linked_member):
        member = linked_member()
        content = _content(member, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE, email="guild_members")
        assert "Pick a guild to email its members" in content

    def it_refuses_a_non_lead_under_the_disabled_policy(linked_member):
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        member = linked_member()
        content = _content(member, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE)
        assert "limited to guild leads and admins" in content

    def it_rejects_a_title_longer_than_200_characters(linked_member):
        member = linked_member()
        content = _content(member, title="x" * 201, when="tomorrow 6pm", guild=_GENERAL_VALUE)
        assert "200 characters" in content
        assert "Nothing was created" in content
        assert not CommunityEvent.objects.exists()
        assert not CommunityEventDraft.objects.exists()

    def it_rejects_a_malformed_video_url(linked_member):
        member = linked_member()
        content = _content(member, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE, video_url="not a url")
        assert "Nothing was created" in content
        assert not CommunityEventDraft.objects.exists()


# --- The preview --------------------------------------------------------------


def describe_the_preview():
    def it_persists_a_draft_and_shows_every_chosen_value(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Ceramics")
        content, draft = _preview(
            admin,
            title="Potluck",
            when="tomorrow 6pm",
            guild=guild.slug,
            details="Bring a dish.",
            location="Main hall",
            video_url="https://meet.example/x",
            recurrence="weekly",
            email="guild_members",
        )
        assert "Here is your event. Please confirm." in content
        assert "**Potluck**" in content
        assert "Guild: Ceramics" in content
        assert "Location: Main hall" in content
        assert "Join online: https://meet.example/x" in content
        assert "Repeats: Every week" in content
        assert "Also emails: this guild's members" in content
        assert draft.title == "Potluck"
        assert draft.guild == guild
        assert draft.recurrence == "weekly"
        assert draft.email_choice == "guild_members"
        assert not CommunityEvent.objects.exists()  # nothing published at preview time

    def it_omits_the_optional_lines_when_unset(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        content, _draft = _preview(admin, title="Plain", when="tomorrow 6pm", guild=_GENERAL_VALUE)
        assert "Location:" not in content
        assert "Join online:" not in content
        assert "Repeats:" not in content
        assert "Also emails:" not in content
        assert "Guild: Whole makerspace" in content

    def it_tells_a_lead_their_guild_event_publishes_right_away(linked_member):
        lead = linked_member()
        guild = GuildFactory(name="Fibers")
        guild.guild_lead = lead
        guild.save(update_fields=["guild_lead"])
        content, _draft = _preview(lead, title="X", when="tomorrow 6pm", guild=guild.slug)
        assert "You can post for this guild, so this will publish right away." in content

    def it_tells_an_admin_their_site_wide_event_publishes_right_away(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        content, _draft = _preview(admin, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE)
        assert "You can post site wide events, so this will publish right away." in content

    def it_tells_a_member_under_open_policy_it_publishes_right_away(linked_member):
        _set_policy(SiteConfiguration.MemberEventPolicy.OPEN)
        member = linked_member()
        content, _draft = _preview(member, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE)
        assert content.rstrip().endswith("This will publish right away.")

    def it_tells_a_member_under_approval_policy_about_the_review_queue(linked_member):
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        member = linked_member()
        content, _draft = _preview(member, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE)
        assert "review queue" in content
        assert "email option" not in content  # no email chosen → no caveat

    def it_adds_the_email_caveat_to_a_proposals_preview(linked_member):
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        member = linked_member()
        guild = GuildFactory(name="Glass")
        content, _draft = _preview(member, title="X", when="tomorrow 6pm", guild=guild.slug, email="guild_members")
        assert "will not be sent for a proposal" in content

    def it_carries_confirm_and_cancel_buttons(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        result = _create_event(_interaction(title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE), admin)
        draft = CommunityEventDraft.objects.get()
        buttons = result["data"]["components"][0]["components"]
        assert [b["custom_id"] for b in buttons] == [f"create:confirm:{draft.pk}", f"create:cancel:{draft.pk}"]

    def it_replaces_the_authors_older_unconfirmed_drafts(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _preview(admin, title="First", when="tomorrow 6pm", guild=_GENERAL_VALUE)
        _preview(admin, title="Second", when="tomorrow 7pm", guild=_GENERAL_VALUE)
        assert list(CommunityEventDraft.objects.values_list("title", flat=True)) == ["Second"]


# --- Confirm / Cancel ---------------------------------------------------------


def describe_confirming_a_preview():
    @respx.mock
    def it_publishes_for_an_admin_and_replaces_the_preview(settings, linked_member):
        _discord_settings(settings)
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Ceramics")
        _, draft = _preview(admin, title="Potluck", when="tomorrow 6pm", guild=guild.slug, duration_minutes=120)

        result = _confirm(admin, draft.pk)

        assert result == {}
        payload = _followup_payload(followup)
        assert "live on the Community Calendar" in payload["content"]
        assert payload["components"][0]["components"][0]["label"] == "Open the event"
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
        _, draft = _preview(lead, title="Fiber Night", when="tomorrow 6pm", guild=guild.slug)

        _confirm(lead, draft.pk)
        assert "live on the Community Calendar" in _followup_payload(followup)["content"]
        assert CommunityEvent.objects.get(title="Fiber Night").moderation_state == (
            CommunityEvent.ModerationState.PUBLISHED
        )

    @respx.mock
    def it_creates_a_site_wide_community_event_with_the_chosen_calendar(settings, linked_member):
        _discord_settings(settings)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _, draft = _preview(admin, title="One Mic Night", when="tomorrow 6pm", guild=_GENERAL_VALUE, calendar="public")

        _confirm(admin, draft.pk)
        assert "live on the Community Calendar" in _followup_payload(followup)["content"]
        event = CommunityEvent.objects.get(title="One Mic Night")
        assert event.event_type == CommunityEvent.EventType.COMMUNITY
        assert event.guild is None
        assert event.google_calendar_target == CommunityEvent.GoogleCalendarTarget.PUBLIC

    @respx.mock
    def it_saves_details_location_video_and_recurrence(settings, linked_member):
        _discord_settings(settings)
        _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Print")
        _, draft = _preview(
            admin,
            title="Zine Night",
            when="tomorrow 6pm",
            guild=guild.slug,
            details="Bring paper and a stapler.",
            location="Print shop",
            video_url="https://meet.example/zine",
            recurrence="monthly",
        )

        _confirm(admin, draft.pk)
        event = CommunityEvent.objects.get(title="Zine Night")
        assert event.description == "Bring paper and a stapler."
        assert event.location == "Print shop"
        assert event.video_url == "https://meet.example/zine"
        assert event.recurrence == CommunityEvent.Recurrence.MONTHLY

    @respx.mock
    def it_submits_for_review_under_the_approval_policy_stripping_the_buttons(settings, linked_member):
        _discord_settings(settings)
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        followup = _mock_discord()
        member = linked_member()
        _, draft = _preview(member, title="My Proposal", when="tomorrow 6pm", guild=_GENERAL_VALUE)

        result = _confirm(member, draft.pk)
        assert result == {}
        payload = _followup_payload(followup)
        assert "submitted for review" in payload["content"]
        assert payload["components"] == []  # the Confirm / Cancel row is replaced by nothing
        event = CommunityEvent.objects.get(title="My Proposal")
        assert event.moderation_state == CommunityEvent.ModerationState.PENDING
        assert event.submitted_by == member.user

    @respx.mock
    def it_publishes_directly_under_the_open_policy(settings, linked_member):
        _discord_settings(settings)
        _set_policy(SiteConfiguration.MemberEventPolicy.OPEN)
        followup = _mock_discord()
        member = linked_member()
        _, draft = _preview(member, title="Open Proposal", when="tomorrow 6pm", guild=_GENERAL_VALUE)

        _confirm(member, draft.pk)
        assert "live on the Community Calendar" in _followup_payload(followup)["content"]
        assert CommunityEvent.objects.get(title="Open Proposal").moderation_state == (
            CommunityEvent.ModerationState.PUBLISHED
        )

    @respx.mock
    def it_records_the_rate_limit_only_on_a_created_event(settings, linked_member):
        _discord_settings(settings)
        _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _, draft = _preview(admin, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE)
        assert cache.get(f"abuse:{_CREATE_RATE_SCOPE}:{admin.pk}:hourly", 0) == 0  # preview costs nothing

        _confirm(admin, draft.pk)
        assert cache.get(f"abuse:{_CREATE_RATE_SCOPE}:{admin.pk}:hourly", 0) == 1


def describe_cancelling_a_preview():
    def it_deletes_the_draft_and_replaces_the_preview(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _, draft = _preview(admin, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE)

        result = _confirm(admin, draft.pk, action="cancel")
        assert result["type"] == 7  # in-place UPDATE_MESSAGE
        assert "Cancelled. Nothing was created." in result["data"]["content"]
        assert not CommunityEventDraft.objects.exists()
        assert not CommunityEvent.objects.exists()
        assert cache.get(f"abuse:{_CREATE_RATE_SCOPE}:{admin.pk}:hourly", 0) == 0


def describe_stale_and_foreign_drafts():
    def it_reports_an_unknown_draft_as_expired(linked_member):
        member = linked_member()
        result = _confirm(member, 424242)
        assert "expired or was already handled" in result["data"]["content"]

    def it_reports_someone_elses_draft_as_expired(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        other = linked_member()
        _, draft = _preview(admin, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE)
        result = _confirm(other, draft.pk)
        assert "expired or was already handled" in result["data"]["content"]
        assert CommunityEventDraft.objects.filter(pk=draft.pk).exists()  # untouched

    def it_expires_a_draft_older_than_the_confirm_window(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _, draft = _preview(admin, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE)
        CommunityEventDraft.objects.filter(pk=draft.pk).update(created_at=timezone.now() - timedelta(minutes=31))

        result = _confirm(admin, draft.pk)
        assert "expired or was already handled" in result["data"]["content"]
        assert not CommunityEventDraft.objects.exists()
        assert not CommunityEvent.objects.exists()

    def it_treats_a_double_click_as_already_handled(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _, draft = _preview(admin, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE)
        CommunityEventDraft.objects.filter(pk=draft.pk).update(confirmed_at=timezone.now())

        result = _confirm(admin, draft.pk)
        assert "expired or was already handled" in result["data"]["content"]
        assert not CommunityEvent.objects.exists()

    def it_lets_exactly_one_of_two_racing_confirms_win_the_claim(linked_member):
        # Simulate the true race: both clicks loaded the draft while it was still claimable,
        # then one claimed it first. The loser's conditional UPDATE hits 0 rows and must not
        # create a second event.
        from membership.discord_commands import _confirm_create

        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _, draft = _preview(admin, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE)
        stale = CommunityEventDraft.objects.get(pk=draft.pk)  # loaded before the winner claims
        CommunityEventDraft.objects.filter(pk=draft.pk).update(confirmed_at=timezone.now())

        interaction = {"id": "intA", "token": "tokB", "data": {"custom_id": f"create:confirm:{draft.pk}"}}
        result = _confirm_create(interaction, admin, stale)
        assert "expired or was already handled" in result["data"]["content"]
        assert not CommunityEvent.objects.exists()

    def it_returns_the_generic_error_on_a_malformed_custom_id(linked_member):
        member = linked_member()
        result = _create_component({"data": {"custom_id": "create:nope"}}, member)
        assert "went wrong" in result["data"]["content"]

    def it_reports_setup_incomplete_for_a_userless_member():
        member = MemberFactory()
        result = _create_component({"data": {"custom_id": "create:confirm:1"}}, member)
        assert "isn't fully set up" in result["data"]["content"]


def describe_confirm_time_rechecks():
    def it_refuses_when_the_policy_became_disabled_after_the_preview(linked_member):
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        member = linked_member()
        _, draft = _preview(member, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE)
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)

        result = _confirm(member, draft.pk)
        assert "limited to guild leads and admins" in result["data"]["content"]
        assert not CommunityEventDraft.objects.exists()  # the dead draft is dropped
        assert not CommunityEvent.objects.exists()

    def it_refuses_when_the_rate_limit_filled_after_the_preview(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _, draft = _preview(admin, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE)
        _exhaust_rate_limit(admin)

        result = _confirm(admin, draft.pk)
        assert "hit the limit" in result["data"]["content"]
        assert not CommunityEventDraft.objects.exists()
        assert not CommunityEvent.objects.exists()


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
        _, draft = _preview(admin, title="Glass Meetup", when="tomorrow 6pm", guild=guild.slug, email="guild_members")

        _confirm(admin, draft.pk)
        assert "Emailed 2 members" in _followup_payload(followup)["content"]
        assert len(mailoutbox) == 2


def describe_error_handling():
    @respx.mock
    def it_reports_the_event_live_when_the_email_fan_out_raises(settings, linked_member):
        _discord_settings(settings)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Solder")
        _, draft = _preview(admin, title="Repair Cafe", when="tomorrow 6pm", guild=guild.slug, email="all_active")

        with patch.object(CommunityEvent, "email_announcement", side_effect=Exception("smtp down")):
            result = _confirm(admin, draft.pk)

        assert result == {}
        content = _followup_payload(followup)["content"]
        assert "live on the Community Calendar" in content
        assert "went wrong" not in content
        assert "Emailed" not in content
        assert CommunityEvent.objects.get(title="Repair Cafe").moderation_state == (
            CommunityEvent.ModerationState.PUBLISHED
        )

    @respx.mock
    def it_reports_a_fan_out_failure_after_the_claim(settings, linked_member):
        _discord_settings(settings)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _, draft = _preview(admin, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE)

        with patch("membership.discord_commands._finalize_event", side_effect=Exception("boom")):
            result = _confirm(admin, draft.pk)
        assert result == {}
        assert "was not fully posted" in _followup_payload(followup)["content"]
        assert cache.get(f"abuse:{_CREATE_RATE_SCOPE}:{admin.pk}:hourly", 0) == 0  # nothing created, nothing counted

    @respx.mock
    def it_reports_a_failure_when_the_draft_no_longer_revalidates(settings, linked_member):
        # Corrupted-draft safety net: the claim already happened, so the reply must be the
        # honest failure copy, not a crash — and no event may exist.
        _discord_settings(settings)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _, draft = _preview(admin, title="X", when="tomorrow 6pm", guild=_GENERAL_VALUE)
        # No end-after-start constraint exists on the draft table, so this can be stored —
        # but the shared form must reject it on the confirm-time rebuild.
        CommunityEventDraft.objects.filter(pk=draft.pk).update(ends_at=draft.starts_at - timedelta(hours=1))

        result = _confirm(admin, draft.pk)
        assert result == {}
        assert "was not fully posted" in _followup_payload(followup)["content"]
        assert not CommunityEvent.objects.exists()


def describe_dispatch_integration():
    def it_shows_the_connect_prompt_for_an_unlinked_member(rf):
        interaction = {
            "type": 2,
            "data": {"name": "create", "options": []},
            "member": {"user": {"id": "000"}},
        }
        result = dispatch(interaction, rf.post("/"))
        button = result["data"]["components"][0]["components"][0]
        assert button["url"].endswith("/discord/link/")
