"""Per-member Discord DM channel — bot delivery, the adapter, emit, and the matrix.

All Discord HTTP is mocked with ``respx`` (never the network). Covers the
:mod:`core.events.discord_dm` helpers, the :class:`DiscordDMAdapter` (linked vs
unlinked vs token-blank vs swallowed-error), end-to-end delivery through ``emit()``,
and the new "Discord" column in the settings matrix (visibility, default-off,
linked/unlinked availability, and save).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from core.events import discord_dm, settings_matrix
from core.events.channels import DiscordDMAdapter, Message, get_adapter, is_implemented
from core.events.emit import emit
from core.events.registry import Channel, ChannelDefault, get_event
from core.models import NotificationPreference

pytestmark = pytest.mark.django_db

_CHANNELS_URL = "https://discord.com/api/v10/users/@me/channels"
_MESSAGES_URL = "https://discord.com/api/v10/channels/dm99/messages"


def _message(**kw) -> Message:
    base = dict(title="Class reminder", body="Your class starts soon", url="/account/", trigger_kind="class_reminder")
    base.update(kw)
    return Message(**base)


def _mock_dm_ok():
    """Mock both Discord calls (open DM channel → post message) for a happy-path DM."""
    chan = respx.post(_CHANNELS_URL).mock(return_value=httpx.Response(200, json={"id": "dm99"}))
    msg = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={"id": "m1"}))
    return chan, msg


def describe_bot_token():
    def it_strips_whitespace(settings):
        settings.DISCORD_BOT_TOKEN = "  tok  "
        assert discord_dm.bot_token() == "tok"


def describe_format_dm_content():
    def it_bolds_the_title_and_appends_body_and_url():
        content = discord_dm.format_dm_content(_message())
        assert "**Class reminder**" in content
        assert "Your class starts soon" in content
        assert content.endswith("/account/")

    def it_omits_blank_parts():
        content = discord_dm.format_dm_content(_message(title="", url=""))
        assert content == "Your class starts soon"


def describe_discord_user_id_for():
    def it_returns_the_linked_members_id(linked_member):
        member = linked_member(discord_user_id="555000111")
        assert discord_dm.discord_user_id_for(member.user) == "555000111"

    def it_returns_blank_for_an_unlinked_member(linked_member):
        member = linked_member()
        assert discord_dm.discord_user_id_for(member.user) == ""

    def it_returns_blank_for_a_user_with_no_member():
        from django.contrib.auth.models import User
        from django.db.models.signals import post_save
        from factory.django import mute_signals

        with mute_signals(post_save):
            user = User.objects.create_user(username="nomember", email="n@example.com")
        assert discord_dm.discord_user_id_for(user) == ""


def describe_open_dm_channel():
    @respx.mock
    def it_returns_the_channel_id_on_success(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.post(_CHANNELS_URL).mock(return_value=httpx.Response(200, json={"id": "dm99"}))
        assert discord_dm.open_dm_channel("555") == "dm99"

    def it_is_a_noop_when_the_token_is_blank(settings):
        settings.DISCORD_BOT_TOKEN = ""
        assert discord_dm.open_dm_channel("555") == ""

    def it_is_a_noop_when_the_user_id_is_blank(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        assert discord_dm.open_dm_channel("") == ""

    @respx.mock
    def it_swallows_a_non_2xx_and_returns_blank(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.post(_CHANNELS_URL).mock(return_value=httpx.Response(403, text="forbidden"))
        assert discord_dm.open_dm_channel("555") == ""

    @respx.mock
    def it_swallows_a_network_error_and_returns_blank(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.post(_CHANNELS_URL).mock(side_effect=httpx.ConnectError("no route"))
        assert discord_dm.open_dm_channel("555") == ""

    @respx.mock
    def it_returns_blank_when_the_response_has_no_id(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.post(_CHANNELS_URL).mock(return_value=httpx.Response(200, json={}))
        assert discord_dm.open_dm_channel("555") == ""


def describe_post_dm():
    @respx.mock
    def it_opens_the_channel_then_posts_and_returns_true(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        chan, msg = _mock_dm_ok()
        assert discord_dm.post_dm("555", _message()) is True
        assert chan.called and msg.called
        body = msg.calls.last.request.read().decode()
        assert "Class reminder" in body

    def it_is_a_noop_when_the_token_is_blank(settings):
        settings.DISCORD_BOT_TOKEN = ""
        assert discord_dm.post_dm("555", _message()) is False

    def it_is_a_noop_when_the_user_id_is_blank(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        assert discord_dm.post_dm("", _message()) is False

    @respx.mock
    def it_returns_false_when_the_channel_cannot_be_opened(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.post(_CHANNELS_URL).mock(return_value=httpx.Response(500))
        assert discord_dm.post_dm("555", _message()) is False

    @respx.mock
    def it_swallows_a_non_2xx_on_the_message_post(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.post(_CHANNELS_URL).mock(return_value=httpx.Response(200, json={"id": "dm99"}))
        respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(429, text="rate limited"))
        assert discord_dm.post_dm("555", _message()) is False

    @respx.mock
    def it_swallows_a_network_error_on_the_message_post(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.post(_CHANNELS_URL).mock(return_value=httpx.Response(200, json={"id": "dm99"}))
        respx.post(_MESSAGES_URL).mock(side_effect=httpx.ConnectError("down"))
        assert discord_dm.post_dm("555", _message()) is False


def describe_DiscordDMAdapter():
    def it_is_a_per_recipient_channel():
        assert DiscordDMAdapter().is_broadcast is False

    def it_is_registered_and_implemented():
        assert get_adapter(Channel.DISCORD_DM).channel is Channel.DISCORD_DM
        assert is_implemented(Channel.DISCORD_DM) is True

    @respx.mock
    def it_dms_a_linked_member(settings, linked_member):
        settings.DISCORD_BOT_TOKEN = "tok"
        member = linked_member(discord_user_id="555000111")
        chan, msg = _mock_dm_ok()
        DiscordDMAdapter().deliver(member.user, _message())
        assert chan.called and msg.called

    def it_is_a_noop_for_an_unlinked_member(settings, linked_member):
        settings.DISCORD_BOT_TOKEN = "tok"
        member = linked_member()
        with respx.mock:
            route = respx.post(_CHANNELS_URL)
            DiscordDMAdapter().deliver(member.user, _message())
        assert not route.called

    def it_is_a_noop_when_the_token_is_blank(settings, linked_member):
        settings.DISCORD_BOT_TOKEN = ""
        member = linked_member(discord_user_id="555000111")
        with respx.mock:
            route = respx.post(_CHANNELS_URL)
            DiscordDMAdapter().deliver(member.user, _message())
        assert not route.called

    @respx.mock
    def it_swallows_a_discord_error_and_never_raises(settings, linked_member):
        settings.DISCORD_BOT_TOKEN = "tok"
        member = linked_member(discord_user_id="555000111")
        respx.post(_CHANNELS_URL).mock(side_effect=httpx.ConnectError("down"))
        # Must not raise — best-effort delivery keeps the fan-out going.
        DiscordDMAdapter().deliver(member.user, _message())


def describe_emit_to_discord_dm():
    @respx.mock
    def it_delivers_when_the_member_opted_in_and_linked(settings, linked_member):
        settings.DISCORD_BOT_TOKEN = "tok"
        member = linked_member(discord_user_id="555000111")
        NotificationPreference.objects.create(
            user=member.user, event_key="class_reminder", channel=Channel.DISCORD_DM.value, enabled=True
        )
        chan, msg = _mock_dm_ok()
        result = emit(
            "class_reminder", context={"member": member}, title="Class reminder", body="Your class starts soon"
        )
        assert chan.called and msg.called
        assert (member.user.pk, Channel.DISCORD_DM) in result.delivered

    def it_does_not_dm_when_the_member_has_not_opted_in(settings, linked_member):
        settings.DISCORD_BOT_TOKEN = "tok"
        member = linked_member(discord_user_id="555000111")
        with respx.mock:
            route = respx.post(_CHANNELS_URL)
            result = emit("class_reminder", context={"member": member}, title="Class reminder", body="x")
        assert not route.called
        assert all(channel is not Channel.DISCORD_DM for _pk, channel in result.delivered)


def describe_registry_declares_discord_dm():
    def it_offers_discord_dm_off_on_seeded_events():
        spec = get_event("class_reminder").channel(Channel.DISCORD_DM)
        assert spec is not None
        assert spec.default is ChannelDefault.OFF
        assert not spec.is_forced

    def it_does_not_offer_discord_dm_on_the_email_only_new_events():
        # member.invited reaches a person with no account — a DM is impossible, so the
        # net-new email-only events do not declare the per-member DM channel.
        assert get_event("member.invited").channel(Channel.DISCORD_DM) is None


def _cell(matrix, event_key, channel):
    for _category, rows in matrix:
        for row in rows:
            if row.event_key == event_key:
                for cell in row.cells:
                    if cell.channel is channel:
                        return cell
    return None


def describe_settings_matrix_discord_column():
    def it_lists_discord_dm_as_a_visible_channel(linked_member):
        member = linked_member()
        assert Channel.DISCORD_DM in settings_matrix.visible_channels(member.user)

    def it_labels_the_column_Discord():
        assert settings_matrix.CHANNEL_LABELS[Channel.DISCORD_DM] == "Discord"

    def it_defaults_the_discord_dm_cell_off(linked_member):
        member = linked_member(discord_user_id="555")
        matrix = settings_matrix.build_matrix(member.user)
        cell = _cell(matrix, "class_reminder", Channel.DISCORD_DM)
        assert cell is not None
        assert cell.enabled is False

    def it_disables_the_discord_cells_for_an_unlinked_member(linked_member):
        member = linked_member()
        matrix = settings_matrix.build_matrix(member.user)
        cell = _cell(matrix, "class_reminder", Channel.DISCORD_DM)
        assert cell.available is False
        assert cell.hint

    def it_enables_the_discord_cells_for_a_linked_member(linked_member):
        member = linked_member(discord_user_id="555")
        matrix = settings_matrix.build_matrix(member.user)
        cell = _cell(matrix, "class_reminder", Channel.DISCORD_DM)
        assert cell.available is True
        assert cell.hint == ""

    def it_saves_a_discord_dm_preference_for_a_linked_member(linked_member):
        member = linked_member(discord_user_id="555")
        posted = {settings_matrix.field_name("class_reminder", Channel.DISCORD_DM): "on"}
        settings_matrix.save_matrix(member.user, posted)
        assert NotificationPreference.objects.filter(
            user=member.user, event_key="class_reminder", channel=Channel.DISCORD_DM.value, enabled=True
        ).exists()

    def it_does_not_save_discord_dm_for_an_unlinked_member(linked_member):
        # The cell renders disabled, so the browser omits it; skipping the write keeps a
        # prior preference intact across an unlink/relink instead of wiping it to off.
        member = linked_member()
        settings_matrix.save_matrix(member.user, {})
        assert not NotificationPreference.objects.filter(user=member.user, channel=Channel.DISCORD_DM.value).exists()
