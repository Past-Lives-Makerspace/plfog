"""The Discord channel picker — form, widget, helpers, and rendered partial.

Covers ``ChannelRadioSelect`` (disabled-option mechanism), the configured-channel helpers,
the default step-down (§5.3), the guild-aware ``clean`` on both the lead's post form and the
member-approval decision form, and the rendered partial's disabled/hint/empty states (parsed
from HTML, since a test-client POST can't catch a structure bug — MEMORY reference_nested).
"""

from __future__ import annotations

import pytest
from django.template.loader import render_to_string

from core.models import SiteConfiguration
from hub.forms import (
    ChannelRadioSelect,
    GuildAnnouncementDecisionForm,
    GuildAnnouncementForm,
    _configured_discord_channels,
    _default_discord_channel,
)
from membership.models import GuildAnnouncement
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

_Channel = GuildAnnouncement.DiscordChannel
_HOOK = "https://discord.com/api/webhooks/9/x"


def _set_shared(*, general: str = "", leadership: str = "", officers: str = "") -> None:
    config = SiteConfiguration.load()
    config.discord_general_webhook_url = general
    config.discord_leadership_webhook_url = leadership
    config.discord_officers_webhook_url = officers
    config.save()


def describe_configured_discord_channels():
    def it_is_empty_when_nothing_is_configured():
        assert _configured_discord_channels(GuildFactory()) == set()

    def it_includes_guild_when_the_guild_has_a_webhook():
        assert _Channel.GUILD.value in _configured_discord_channels(GuildFactory(discord_webhook_url=_HOOK))

    def it_includes_general_leadership_and_officers_from_site_config():
        _set_shared(general=_HOOK, leadership=_HOOK, officers=_HOOK)
        configured = _configured_discord_channels(GuildFactory())
        assert configured == {_Channel.GENERAL.value, _Channel.LEADERSHIP.value, _Channel.OFFICERS.value}

    def it_tolerates_a_none_guild():
        assert _configured_discord_channels(None) == set()


def describe_default_discord_channel():
    def it_prefers_the_guild_channel():
        assert _default_discord_channel({_Channel.GUILD.value, _Channel.GENERAL.value}) == _Channel.GUILD.value

    def it_steps_down_to_general_then_leadership_then_officers():
        assert _default_discord_channel({_Channel.GENERAL.value}) == _Channel.GENERAL.value
        assert _default_discord_channel({_Channel.LEADERSHIP.value}) == _Channel.LEADERSHIP.value
        assert _default_discord_channel({_Channel.OFFICERS.value}) == _Channel.OFFICERS.value

    def it_falls_back_to_none_when_nothing_is_configured():
        assert _default_discord_channel(set()) == _Channel.NONE.value


def describe_ChannelRadioSelect():
    def it_disables_an_unconfigured_non_none_option():
        widget = ChannelRadioSelect(configured_channels={_Channel.GUILD.value})
        option = widget.create_option("discord_channel", _Channel.GENERAL.value, "#general-chat", False, 1)
        assert option["attrs"]["disabled"] is True
        assert option["attrs"]["data-hint"] == "Not set up yet."

    def it_leaves_a_configured_option_enabled():
        widget = ChannelRadioSelect(configured_channels={_Channel.GENERAL.value})
        option = widget.create_option("discord_channel", _Channel.GENERAL.value, "#general-chat", False, 1)
        assert "disabled" not in option["attrs"]

    def it_never_disables_the_dont_post_option():
        widget = ChannelRadioSelect(configured_channels=set())
        option = widget.create_option("discord_channel", _Channel.NONE.value, "Don't post", False, 3)
        assert "disabled" not in option["attrs"]


def describe_GuildAnnouncementForm_discord_channel():
    def it_preselects_the_guild_channel_when_configured():
        form = GuildAnnouncementForm(guild=GuildFactory(discord_webhook_url=_HOOK))
        assert form["discord_channel"].value() == _Channel.GUILD.value

    def it_preselects_dont_post_when_nothing_is_configured():
        form = GuildAnnouncementForm(guild=GuildFactory())
        assert form["discord_channel"].value() == _Channel.NONE.value

    def it_accepts_a_configured_channel():
        guild = GuildFactory(discord_webhook_url=_HOOK)
        form = GuildAnnouncementForm(
            {"title": "T", "body": "B", "expires_at": "", "discord_channel": _Channel.GUILD.value}, guild=guild
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["discord_channel"] == _Channel.GUILD.value

    def it_accepts_the_officers_channel_when_its_shared_webhook_is_set():
        _set_shared(officers=_HOOK)
        form = GuildAnnouncementForm(
            {"title": "T", "body": "B", "expires_at": "", "discord_channel": _Channel.OFFICERS.value},
            guild=GuildFactory(),
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["discord_channel"] == _Channel.OFFICERS.value

    def it_accepts_dont_post_even_with_no_webhooks():
        form = GuildAnnouncementForm(
            {"title": "T", "body": "B", "expires_at": "", "discord_channel": _Channel.NONE.value},
            guild=GuildFactory(),
        )
        assert form.is_valid(), form.errors

    def it_rejects_a_channel_whose_webhook_is_unset():
        form = GuildAnnouncementForm(
            {"title": "T", "body": "B", "expires_at": "", "discord_channel": _Channel.GENERAL.value},
            guild=GuildFactory(),
        )
        assert not form.is_valid()
        assert "discord_channel" in form.errors

    def it_leaves_the_saved_channel_untouched_when_omitted_on_edit():
        # The edit modal renders no picker; an omitted channel must keep the default/existing.
        guild = GuildFactory()
        form = GuildAnnouncementForm({"title": "T", "body": "B", "expires_at": ""}, guild=guild)
        assert form.is_valid(), form.errors
        assert form.cleaned_data.get("discord_channel", "") == ""


def describe_GuildAnnouncementDecisionForm_discord_channel():
    def it_defaults_the_picker_from_the_guilds_configuration():
        guild = GuildFactory(discord_webhook_url=_HOOK)
        form = GuildAnnouncementDecisionForm(guild=guild)
        assert form["discord_channel"].value() == _Channel.GUILD.value

    def it_allows_a_blank_channel_on_a_decline():
        # changes/decline post no picker → channel blank is fine (view only reads it on approve).
        form = GuildAnnouncementDecisionForm({"decision": "decline", "notes": "no"}, guild=GuildFactory())
        assert form.is_valid(), form.errors
        assert form.cleaned_data["discord_channel"] == ""

    def it_rejects_an_unconfigured_channel_on_approve():
        form = GuildAnnouncementDecisionForm(
            {"decision": "approve", "discord_channel": _Channel.LEADERSHIP.value}, guild=GuildFactory()
        )
        assert not form.is_valid()
        assert "discord_channel" in form.errors

    def it_tolerates_no_guild_kwarg():
        form = GuildAnnouncementDecisionForm(guild=None)
        assert form["discord_channel"].value() == _Channel.NONE.value


def _render_picker(guild) -> str:
    form = GuildAnnouncementForm(guild=guild)
    return render_to_string("hub/partials/_announcement_channel_picker.html", {"field": form["discord_channel"]})


def describe_rendered_picker_partial():
    def it_disables_every_channel_and_shows_the_empty_note_when_nothing_is_configured():
        html = _render_picker(GuildFactory())
        assert html.count('data-hint="Not set up yet."') == 4
        assert "No Discord channels are set up yet" in html

    def it_enables_the_guild_option_and_hides_the_empty_note_when_configured():
        html = _render_picker(GuildFactory(discord_webhook_url=_HOOK))
        # Only #general-chat, #leadership, and #guild-officers remain unconfigured.
        assert html.count('data-hint="Not set up yet."') == 3
        assert "No Discord channels are set up yet" not in html

    def it_always_shows_the_regardless_hint():
        html = _render_picker(GuildFactory())
        assert "this only picks the Discord channel" in html
