"""Specs for the ``/create`` modal command (membership.discord_commands).

Covers the zero-option command that opens the Create-an-Event modal, the guild-select and
channel-default resolution, the modal submit (when-parse + form validation → the error card
with an Edit Event button; a valid submit → the upgraded gold preview card), the config
selects (Repeats / Calendar / Email / conditional Duration) that mutate the draft and
re-render the card, the Edit reopens, and the Create Event / Cancel confirm flow behind the
type-6 deferred ack (Discord REST mocked with ``respx``).
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
    _channel_guild,
    _create_component,
    _create_event,
    _create_submit,
    _event_cfg_component,
    _event_edit_component,
    _guild_from_select,
)
from membership.models import CommunityEvent, CommunityEventDraft, Member
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db

_CALLBACK_URL = "https://discord.com/api/v10/interactions/intA/tokB/callback"
_FOLLOWUP_URL = "https://discord.com/api/v10/webhooks/appX/tokB/messages/@original"


@pytest.fixture(autouse=True)
def _clear_state():
    cache.clear()
    yield
    cache.clear()


# --- Interaction builders -----------------------------------------------------


def _open_modal(member: object, *, channel_id: str | None = None) -> dict:
    interaction: dict = {"data": {"options": []}}
    if channel_id is not None:
        interaction["channel_id"] = channel_id
    return _create_event(interaction, member)


def _modal_submit(
    *,
    title: str = "X",
    when: str = "tomorrow 6pm",
    guild_slug: str = _GENERAL_VALUE,
    location: str = "",
    description: str = "",
) -> dict:
    """A Create-an-Event MODAL_SUBMIT payload (Components-v2 Label rows echo back)."""
    return {
        "data": {
            "custom_id": "eventform",
            "components": [
                {"type": 18, "component": {"custom_id": "title", "value": title}},
                {"type": 18, "component": {"custom_id": "when", "value": when}},
                {"type": 18, "component": {"custom_id": "guild", "values": [guild_slug]}},
                {"type": 18, "component": {"custom_id": "location", "value": location}},
                {"type": 18, "component": {"custom_id": "description", "value": description}},
            ],
        }
    }


def _submit(member: object, **kwargs: object) -> dict:
    return _create_submit(_modal_submit(**kwargs), member)


def _preview(member: object, **kwargs: object) -> tuple[dict, CommunityEventDraft]:
    """Submit the modal and return (the preview-card reply, the persisted draft)."""
    result = _submit(member, **kwargs)
    return result, CommunityEventDraft.objects.latest("pk")


def _cfg(member: object, draft_pk: int, field: str, value: object) -> dict:
    interaction = {"data": {"custom_id": f"eventcfg:{field}:{draft_pk}", "values": [str(value)]}}
    return _event_cfg_component(interaction, member)


def _confirm(member: object, draft_pk: int, *, action: str = "confirm") -> dict:
    interaction = {"id": "intA", "token": "tokB", "data": {"custom_id": f"create:{action}:{draft_pk}"}}
    return _create_component(interaction, member)


def _custom_ids(result: dict) -> list[str]:
    return [comp.get("custom_id", "") for row in result["data"]["components"] for comp in row["components"]]


def _select_options(result: dict, custom_id: str) -> list[str]:
    """The option values of the card select whose custom_id is ``custom_id``."""
    for row in result["data"]["components"]:
        for comp in row["components"]:
            if comp.get("custom_id") == custom_id:
                return [option["value"] for option in comp["options"]]
    raise AssertionError(f"no select with custom_id {custom_id!r}")


def _label(result: dict, custom_id: str) -> dict:
    return next(
        row["component"]
        for row in result["data"]["components"]
        if row.get("type") == 18 and row["component"].get("custom_id") == custom_id
    )


def _set_policy(value: str) -> None:
    config = SiteConfiguration.load()
    config.member_event_policy = value
    config.save()


def _discord_settings(settings) -> None:
    settings.DISCORD_BOT_TOKEN = "bot"
    settings.DISCORD_CLIENT_ID = "appX"
    settings.MEMBER_BASE_URL = "https://members.example"
    settings.DISCORD_NOTIFY_WEBHOOK_URL = ""


def _mock_discord() -> object:
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
    def it_is_link_gated_and_carries_no_options():
        assert CREATE.name == "create"
        assert (CREATE.requires_link, CREATE.defer) == (True, False)
        assert CREATE.scope == "guild"
        assert CREATE.to_api_dict()["options"] == []  # the modal replaces the slash options


def describe_guild_resolution():
    def it_returns_no_guild_for_the_all_makerspace_choice():
        assert _guild_from_select(_GENERAL_VALUE) == (None, None)

    def it_returns_no_guild_for_a_blank_value():
        assert _guild_from_select("") == (None, None)

    def it_resolves_an_active_slug():
        target = GuildFactory(name="Woodshop")
        assert _guild_from_select(target.slug) == (target, None)

    def it_errors_on_an_unknown_slug():
        guild, error = _guild_from_select("ghost")
        assert guild is None
        assert "couldn't find an active guild" in error["data"]["content"]

    def it_maps_the_channel_to_its_guild():
        guild = GuildFactory(name="Wood", discord_channel_id="chan-wood")
        assert _channel_guild({"channel_id": "chan-wood"}) == guild

    def it_returns_none_when_the_channel_has_no_mapping():
        assert _channel_guild({"channel_id": "unknown"}) is None


# --- Opening the modal --------------------------------------------------------


def describe_opening_the_modal():
    def it_reports_when_the_linked_account_has_no_user():
        member = MemberFactory()
        assert "isn't fully set up" in _open_modal(member)["data"]["content"]

    def it_refuses_when_the_member_is_over_the_rate_limit(linked_member):
        member = linked_member()
        _exhaust_rate_limit(member)
        assert "hit the limit" in _open_modal(member)["data"]["content"]

    def it_opens_the_modal_defaulting_the_guild_to_the_channel(linked_member):
        member = linked_member()
        guild = GuildFactory(name="Ceramics", discord_channel_id="chan-clay")
        result = _open_modal(member, channel_id="chan-clay")
        assert result["type"] == 9  # MODAL
        assert result["data"]["custom_id"] == "eventform"
        assert result["data"]["title"] == "Create an Event"
        default = next(o for o in _label(result, "guild")["options"] if o["default"])
        assert default["value"] == guild.slug

    def it_defaults_to_all_makerspace_when_the_channel_has_no_guild(linked_member):
        result = _open_modal(linked_member(), channel_id="nowhere")
        default = next(o for o in _label(result, "guild")["options"] if o["default"])
        assert default["value"] == _GENERAL_VALUE

    def it_peeks_the_rate_limit_without_recording_it(linked_member):
        member = linked_member()
        _open_modal(member)
        assert cache.get(f"abuse:{_CREATE_RATE_SCOPE}:{member.pk}:hourly", 0) == 0


# --- Submitting the modal (validation) ----------------------------------------


def describe_submitting_the_modal():
    def it_reports_when_the_linked_account_has_no_user():
        assert "isn't fully set up" in _submit(MemberFactory())["data"]["content"]

    def it_reports_an_unknown_guild_listing_the_active_ones(linked_member):
        GuildFactory(name="Woodshop")
        content = _submit(linked_member(), guild_slug="ghost")["data"]["content"]
        assert "couldn't find an active guild" in content
        assert "Woodshop" in content  # the friendly list so it is never a dead end

    def it_reports_an_unreadable_when_with_an_edit_button(linked_member):
        result = _submit(linked_member(), when="???")
        assert "could not read that date and time" in result["data"]["content"]
        assert "Nothing was created yet." in result["data"]["content"]
        assert result["data"]["components"][0]["components"][0]["label"] == "Edit Event"
        assert not CommunityEventDraft.objects.exists()

    def it_reports_a_day_with_no_time(linked_member):
        assert "not a start time" in _submit(linked_member(), when="next friday")["data"]["content"]

    def it_reports_a_past_start(linked_member):
        assert "already passed" in _submit(linked_member(), when="jan 5 2020 6pm")["data"]["content"]

    def it_reports_a_start_more_than_a_year_out(linked_member):
        assert "more than a year away" in _submit(linked_member(), when="2031-08-29 6pm")["data"]["content"]

    def it_rejects_a_title_longer_than_200_characters(linked_member):
        result = _submit(linked_member(), title="x" * 201)
        assert "Edit Event" == result["data"]["components"][0]["components"][0]["label"]
        assert not CommunityEventDraft.objects.exists()

    def it_refuses_a_non_lead_under_the_disabled_policy(linked_member):
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        content = _submit(linked_member())["data"]["content"]
        assert "limited to guild leads and admins" in content
        assert not CommunityEventDraft.objects.exists()


# --- The upgraded preview card ------------------------------------------------


def describe_the_preview_card():
    def it_persists_a_draft_and_renders_the_gold_embed(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Metal")
        result, draft = _preview(
            admin,
            title="Forge Night",
            when="tomorrow 6pm",
            guild_slug=guild.slug,
            location="Shop",
            description="Bring gloves",
        )
        assert draft.title == "Forge Night"
        embed = result["data"]["embeds"][0]
        assert embed["title"] == "Forge Night"
        assert embed["color"] == 0xEEB44B
        by_name = {f["name"]: f["value"] for f in embed["fields"]}
        assert "<t:" in by_name["When"]
        assert by_name["Guild"] == "Metal"
        assert by_name["Location"] == "Shop"
        assert by_name["Description"] == "Bring gloves"

    def it_omits_location_and_description_fields_when_unset(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        result, _draft = _preview(admin, title="Plain", when="tomorrow 6pm")
        names = [f["name"] for f in result["data"]["embeds"][0]["fields"]]
        assert names == ["When", "Guild"]

    def it_carries_the_config_selects_and_action_buttons(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        ids = _custom_ids(result)
        assert f"eventcfg:repeats:{draft.pk}" in ids
        assert f"eventcfg:calendar:{draft.pk}" in ids
        assert f"eventcfg:email:{draft.pk}" in ids
        assert f"create:confirm:{draft.pk}" in ids
        assert f"create:edit:{draft.pk}" in ids
        assert f"create:cancel:{draft.pk}" in ids

    def it_offers_a_duration_select_only_when_the_when_had_no_end(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        no_end, draft_a = _preview(admin, title="Open Ended", when="tomorrow 6pm")
        assert f"eventcfg:duration:{draft_a.pk}" in _custom_ids(no_end)
        with_end, draft_b = _preview(admin, title="Ranged", when="tomorrow 6pm to 8pm")
        assert f"eventcfg:duration:{draft_b.pk}" not in _custom_ids(with_end)

    def it_defaults_a_missing_end_to_a_two_hour_span(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        assert draft.ends_at - draft.starts_at == timedelta(hours=2)
        assert draft.when_had_end is False

    def it_tells_a_lead_their_guild_event_publishes_right_away(linked_member):
        lead = linked_member()
        guild = GuildFactory(name="Fibers")
        guild.guild_lead = lead
        guild.save(update_fields=["guild_lead"])
        content = _submit(lead, guild_slug=guild.slug)["data"]["content"]
        assert "publish right away" in content

    def it_tells_a_member_under_approval_policy_about_the_review_queue(linked_member):
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        content = _submit(linked_member())["data"]["content"]
        assert "review queue" in content

    def it_replaces_the_authors_older_unconfirmed_drafts(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _submit(admin, title="First", when="tomorrow 6pm")
        _submit(admin, title="Second", when="tomorrow 7pm")
        assert list(CommunityEventDraft.objects.values_list("title", flat=True)) == ["Second"]


# --- Config selects on the card -----------------------------------------------


def describe_config_selects():
    def it_applies_the_repeat_cadence_and_re_renders_in_place(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        updated = _cfg(admin, draft.pk, "repeats", "monthly")
        assert updated["type"] == 7  # in-place UPDATE_MESSAGE
        draft.refresh_from_db()
        assert draft.recurrence == CommunityEvent.Recurrence.MONTHLY

    def it_applies_the_calendar_target(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        _cfg(admin, draft.pk, "calendar", "public")
        draft.refresh_from_db()
        assert draft.google_calendar_target == CommunityEvent.GoogleCalendarTarget.PUBLIC

    def it_applies_the_email_audience(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        _cfg(admin, draft.pk, "email", "all_active")
        draft.refresh_from_db()
        assert draft.email_choice == CommunityEventDraft.EmailChoice.ALL_ACTIVE

    def it_omits_guild_members_from_a_site_wide_email_select(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        result, draft = _preview(admin, title="X", when="tomorrow 6pm")  # All Makerspace → no guild
        assert _select_options(result, f"eventcfg:email:{draft.pk}") == ["none", "all_active"]

    def it_offers_all_three_email_options_for_a_guild_draft(linked_member):
        lead = linked_member()
        guild = GuildFactory(name="Fibers")
        guild.guild_lead = lead
        guild.save(update_fields=["guild_lead"])
        result, draft = _preview(lead, title="X", when="tomorrow 6pm", guild_slug=guild.slug)
        assert _select_options(result, f"eventcfg:email:{draft.pk}") == ["none", "guild_members", "all_active"]

    def it_rejects_a_forged_guild_members_email_on_a_site_wide_draft(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")  # site-wide
        result = _cfg(admin, draft.pk, "email", "guild_members")
        assert "went wrong" in result["data"]["content"]
        draft.refresh_from_db()
        assert draft.email_choice == CommunityEventDraft.EmailChoice.NONE  # unchanged

    def it_adds_the_email_caveat_to_a_proposal_card(linked_member):
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        member = linked_member()
        _result, draft = _preview(member, title="X", when="tomorrow 6pm")
        updated = _cfg(member, draft.pk, "email", "all_active")  # a whole-membership email on a proposal
        assert "email option only applies when an event publishes" in updated["data"]["content"]

    def it_recomputes_the_end_when_the_duration_changes(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        _cfg(admin, draft.pk, "duration", 180)
        draft.refresh_from_db()
        assert draft.ends_at - draft.starts_at == timedelta(minutes=180)

    def it_rejects_a_tampered_value(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        assert "went wrong" in _cfg(admin, draft.pk, "repeats", "yearly")["data"]["content"]

    def it_rejects_a_tampered_duration_value(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        assert "went wrong" in _cfg(admin, draft.pk, "duration", 999)["data"]["content"]

    def it_error_replies_on_an_unknown_field(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        assert "went wrong" in _cfg(admin, draft.pk, "bogus", "x")["data"]["content"]

    def it_error_replies_on_a_malformed_custom_id(linked_member):
        result = _event_cfg_component({"data": {"custom_id": "eventcfg:repeats:x"}}, linked_member())
        assert "went wrong" in result["data"]["content"]

    def it_reports_an_expired_draft(linked_member):
        result = _cfg(linked_member(), 424242, "repeats", "weekly")
        assert "expired or was already handled" in result["data"]["content"]

    def it_reports_setup_incomplete_for_a_userless_member():
        result = _event_cfg_component({"data": {"custom_id": "eventcfg:repeats:1"}}, MemberFactory())
        assert "isn't fully set up" in result["data"]["content"]


# --- Editing (modal reopen) ---------------------------------------------------


def describe_editing():
    def it_reopens_the_modal_prefilled_from_the_draft(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="Zine Night", when="tomorrow 6pm", location="Print shop")
        reopened = _confirm(admin, draft.pk, action="edit")
        assert reopened["type"] == 9
        assert _label(reopened, "title")["value"] == "Zine Night"
        assert _label(reopened, "location")["value"] == "Print shop"

    def it_reconstructs_a_ranged_when_for_a_draft_with_an_explicit_end(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="Ranged", when="tomorrow 6pm to 8pm")
        reopened = _confirm(admin, draft.pk, action="edit")
        assert " to " in _label(reopened, "when")["value"]  # the explicit-end reconstruction

    def it_reopens_from_a_cached_failed_submission(linked_member):
        member = linked_member()
        error = _submit(member, title="Kept Title", when="???")  # unreadable → cached
        token_id = error["data"]["components"][0]["components"][0]["custom_id"]
        reopened = _event_edit_component({"data": {"custom_id": token_id}}, member)
        assert reopened["type"] == 9
        assert _label(reopened, "title")["value"] == "Kept Title"

    def it_reopens_a_blank_modal_when_the_token_expired(linked_member):
        reopened = _event_edit_component({"data": {"custom_id": "eventedit:gone"}}, linked_member())
        assert reopened["type"] == 9
        assert "value" not in _label(reopened, "title")


# --- Confirming the preview ---------------------------------------------------


def describe_confirming_a_preview():
    @respx.mock
    def it_publishes_for_an_admin_and_replaces_the_preview(settings, linked_member):
        _discord_settings(settings)
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Ceramics")
        _result, draft = _preview(admin, title="Potluck", when="tomorrow 6pm", guild_slug=guild.slug)

        result = _confirm(admin, draft.pk)

        assert result == {}
        payload = _followup_payload(followup)
        assert "live on the Community Calendar" in payload["content"]
        assert payload["components"][0]["components"][0]["label"] == "Open the event"
        event = CommunityEvent.objects.get(title="Potluck")
        assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED
        assert event.event_type == CommunityEvent.EventType.GUILD_MEETING
        assert event.guild == guild
        assert event.ends_at - event.starts_at == timedelta(hours=2)

    @respx.mock
    def it_lets_a_guild_lead_publish_their_guilds_event(settings, linked_member):
        _discord_settings(settings)
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        followup = _mock_discord()
        lead = linked_member()
        guild = GuildFactory(name="Fibers")
        guild.guild_lead = lead
        guild.save(update_fields=["guild_lead"])
        _result, draft = _preview(lead, title="Fiber Night", when="tomorrow 6pm", guild_slug=guild.slug)

        _confirm(lead, draft.pk)
        assert "live on the Community Calendar" in _followup_payload(followup)["content"]
        assert CommunityEvent.objects.get(title="Fiber Night").moderation_state == (
            CommunityEvent.ModerationState.PUBLISHED
        )

    @respx.mock
    def it_applies_a_public_calendar_choice_from_the_card(settings, linked_member):
        _discord_settings(settings)
        _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="One Mic Night", when="tomorrow 6pm")
        _cfg(admin, draft.pk, "calendar", "public")

        _confirm(admin, draft.pk)
        event = CommunityEvent.objects.get(title="One Mic Night")
        assert event.event_type == CommunityEvent.EventType.COMMUNITY
        assert event.guild is None
        assert event.google_calendar_target == CommunityEvent.GoogleCalendarTarget.PUBLIC

    @respx.mock
    def it_saves_the_description_location_and_card_recurrence(settings, linked_member):
        _discord_settings(settings)
        _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Print")
        _result, draft = _preview(
            admin,
            title="Zine Night",
            when="tomorrow 6pm",
            guild_slug=guild.slug,
            location="Print shop",
            description="Bring paper.",
        )
        _cfg(admin, draft.pk, "repeats", "monthly")

        _confirm(admin, draft.pk)
        event = CommunityEvent.objects.get(title="Zine Night")
        assert event.description == "Bring paper."
        assert event.location == "Print shop"
        assert event.recurrence == CommunityEvent.Recurrence.MONTHLY

    @respx.mock
    def it_submits_for_review_under_the_approval_policy_stripping_the_buttons(settings, linked_member):
        _discord_settings(settings)
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        followup = _mock_discord()
        member = linked_member()
        _result, draft = _preview(member, title="My Proposal", when="tomorrow 6pm")

        result = _confirm(member, draft.pk)
        assert result == {}
        payload = _followup_payload(followup)
        assert "submitted for review" in payload["content"]
        assert payload["components"] == []
        event = CommunityEvent.objects.get(title="My Proposal")
        assert event.moderation_state == CommunityEvent.ModerationState.PENDING
        assert event.submitted_by == member.user

    @respx.mock
    def it_publishes_directly_under_the_open_policy(settings, linked_member):
        _discord_settings(settings)
        _set_policy(SiteConfiguration.MemberEventPolicy.OPEN)
        followup = _mock_discord()
        member = linked_member()
        _result, draft = _preview(member, title="Open Proposal", when="tomorrow 6pm")

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
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        assert cache.get(f"abuse:{_CREATE_RATE_SCOPE}:{admin.pk}:hourly", 0) == 0  # the submit costs nothing

        _confirm(admin, draft.pk)
        assert cache.get(f"abuse:{_CREATE_RATE_SCOPE}:{admin.pk}:hourly", 0) == 1


def describe_cancelling_a_preview():
    def it_deletes_the_draft_and_replaces_the_preview(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")

        result = _confirm(admin, draft.pk, action="cancel")
        assert result["type"] == 7  # in-place UPDATE_MESSAGE
        assert "Cancelled. Nothing was created." in result["data"]["content"]
        assert not CommunityEventDraft.objects.exists()
        assert not CommunityEvent.objects.exists()


def describe_stale_and_foreign_drafts():
    def it_reports_an_unknown_draft_as_expired(linked_member):
        result = _confirm(linked_member(), 424242)
        assert "expired or was already handled" in result["data"]["content"]

    def it_reports_someone_elses_draft_as_expired(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        other = linked_member()
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        result = _confirm(other, draft.pk)
        assert "expired or was already handled" in result["data"]["content"]
        assert CommunityEventDraft.objects.filter(pk=draft.pk).exists()  # untouched

    def it_expires_a_draft_older_than_the_confirm_window(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        CommunityEventDraft.objects.filter(pk=draft.pk).update(created_at=timezone.now() - timedelta(minutes=31))

        result = _confirm(admin, draft.pk)
        assert "expired or was already handled" in result["data"]["content"]
        assert not CommunityEventDraft.objects.exists()

    def it_treats_a_double_click_as_already_handled(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        CommunityEventDraft.objects.filter(pk=draft.pk).update(confirmed_at=timezone.now())

        result = _confirm(admin, draft.pk)
        assert "expired or was already handled" in result["data"]["content"]
        assert not CommunityEvent.objects.exists()

    def it_lets_exactly_one_of_two_racing_confirms_win_the_claim(linked_member):
        from membership.discord_commands import _confirm_create

        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        stale = CommunityEventDraft.objects.get(pk=draft.pk)
        CommunityEventDraft.objects.filter(pk=draft.pk).update(confirmed_at=timezone.now())

        interaction = {"id": "intA", "token": "tokB", "data": {"custom_id": f"create:confirm:{draft.pk}"}}
        result = _confirm_create(interaction, admin, stale)
        assert "expired or was already handled" in result["data"]["content"]
        assert not CommunityEvent.objects.exists()

    def it_returns_the_generic_error_on_a_malformed_custom_id(linked_member):
        result = _create_component({"data": {"custom_id": "create:nope"}}, linked_member())
        assert "went wrong" in result["data"]["content"]

    def it_reports_setup_incomplete_for_a_userless_member():
        result = _create_component({"data": {"custom_id": "create:confirm:1"}}, MemberFactory())
        assert "isn't fully set up" in result["data"]["content"]


def describe_confirm_time_rechecks():
    def it_refuses_when_the_policy_became_disabled_after_the_preview(linked_member):
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        member = linked_member()
        _result, draft = _preview(member, title="X", when="tomorrow 6pm")
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)

        result = _confirm(member, draft.pk)
        assert "limited to guild leads and admins" in result["data"]["content"]
        assert not CommunityEventDraft.objects.exists()
        assert not CommunityEvent.objects.exists()

    def it_refuses_when_the_rate_limit_filled_after_the_preview(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
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
        for _ in range(2):
            member = linked_member()
            GuildMembershipFactory(guild=guild, member=member)
            NotificationPreference.objects.create(
                user=member.user, event_key="event.guild_published", channel="email", enabled=False
            )
        _result, draft = _preview(admin, title="Glass Meetup", when="tomorrow 6pm", guild_slug=guild.slug)
        _cfg(admin, draft.pk, "email", "guild_members")

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
        _result, draft = _preview(admin, title="Repair Cafe", when="tomorrow 6pm", guild_slug=guild.slug)
        _cfg(admin, draft.pk, "email", "all_active")

        with patch.object(CommunityEvent, "email_announcement", side_effect=Exception("smtp down")):
            result = _confirm(admin, draft.pk)

        assert result == {}
        content = _followup_payload(followup)["content"]
        assert "live on the Community Calendar" in content
        assert "went wrong" not in content
        assert "Emailed" not in content

    @respx.mock
    def it_reports_a_fan_out_failure_after_the_claim(settings, linked_member):
        _discord_settings(settings)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")

        with patch("membership.discord_commands._finalize_event", side_effect=Exception("boom")):
            result = _confirm(admin, draft.pk)
        assert result == {}
        assert "was not fully posted" in _followup_payload(followup)["content"]
        assert cache.get(f"abuse:{_CREATE_RATE_SCOPE}:{admin.pk}:hourly", 0) == 0

    @respx.mock
    def it_reports_a_failure_when_the_draft_no_longer_revalidates(settings, linked_member):
        _discord_settings(settings)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        _result, draft = _preview(admin, title="X", when="tomorrow 6pm")
        CommunityEventDraft.objects.filter(pk=draft.pk).update(ends_at=draft.starts_at - timedelta(hours=1))

        result = _confirm(admin, draft.pk)
        assert result == {}
        assert "was not fully posted" in _followup_payload(followup)["content"]
        assert not CommunityEvent.objects.exists()


def describe_dispatch_integration():
    def it_shows_the_connect_prompt_for_an_unlinked_member(rf):
        interaction = {"type": 2, "data": {"name": "create", "options": []}, "member": {"user": {"id": "000"}}}
        result = dispatch(interaction, rf.post("/"))
        button = result["data"]["components"][0]["components"][0]
        assert button["url"].endswith("/discord/link/")
