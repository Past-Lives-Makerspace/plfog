"""Specs for the hub ``/join-guild`` slash command — the option builder + the handler.

The handler orchestrates three best-effort collaborators (Discord role sync, the guild
channel webhook post, and the in-app join fan-out). Most cases patch those three with
spies to assert *which* fire on each path; one end-to-end case leaves them real and mocks
the raw Discord REST with ``respx`` to prove the wiring (role PUT + welcome webhook payload).
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest import mock

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.core import mail

from core.events import discord as discord_module
from core.events import discord_roles
from hub.discord_commands import JOIN_GUILD, _guild_choices, _join_guild, _welcome_body
from membership import orientations
from membership.models import GuildMembership
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

_WEBHOOK = "https://discord.com/api/webhooks/1/tok"


def _interaction(slug: str | None) -> dict:
    """A ``/join-guild`` interaction carrying the chosen guild slug as the ``guild`` option."""
    options = [] if slug is None else [{"name": "guild", "value": slug}]
    return {"data": {"name": "join-guild", "options": options}}


@pytest.fixture
def spies(monkeypatch):
    """Patch the handler's three best-effort collaborators with recording spies.

    The handler re-imports each module at call time, so patching the module attribute is
    picked up. ``guild_webhook`` is left real so the post-gating (enabled + non-blank URL)
    is exercised for real; only ``post_embed`` itself is spied.
    """
    role = mock.MagicMock(name="on_membership_changed")
    post = mock.MagicMock(name="post_embed", return_value=True)
    fanout = mock.MagicMock(name="member_joined_guild")
    monkeypatch.setattr(discord_roles, "on_membership_changed", role)
    monkeypatch.setattr(discord_module, "post_embed", post)
    monkeypatch.setattr(orientations, "member_joined_guild", fanout)
    return SimpleNamespace(role=role, post=post, fanout=fanout)


def describe_join_guild_handler():
    def describe_a_brand_new_join():
        def it_creates_the_membership_as_an_app_join(spies):
            guild = GuildFactory(name="Forge", discord_webhook_url=_WEBHOOK, discord_welcome_message="From the lead!")
            member = MemberFactory(discord_user_id="555", preferred_name="Robin")
            _join_guild(_interaction(guild.slug), member)
            row = GuildMembership.objects.get(guild=guild, member=member)
            assert row.source == GuildMembership.Source.APP

        def it_ensures_the_discord_role(spies):
            guild = GuildFactory(discord_webhook_url=_WEBHOOK)
            member = MemberFactory(discord_user_id="555")
            _join_guild(_interaction(guild.slug), member)
            spies.role.assert_called_once_with(guild, member, joined=True)

        def it_posts_the_public_welcome_with_the_member_in_the_title_and_no_ping(spies):
            guild = GuildFactory(name="Forge", discord_webhook_url=_WEBHOOK, discord_welcome_message="From the lead!")
            member = MemberFactory(discord_user_id="555", preferred_name="Robin")
            _join_guild(_interaction(guild.slug), member)
            spies.post.assert_called_once()
            hook, message = spies.post.call_args.args
            assert hook == _WEBHOOK
            assert message.title == "Welcome Robin to Forge!"
            assert message.body == "From the lead!"
            # No user @-mention: Message.discord_mention only supports @here/@everyone.
            assert message.discord_mention == ""

        def it_fires_the_welcome_fanout(spies):
            guild = GuildFactory(discord_webhook_url=_WEBHOOK)
            member = MemberFactory(discord_user_id="555")
            _join_guild(_interaction(guild.slug), member)
            spies.fanout.assert_called_once_with(guild, member)

        def it_confirms_ephemerally_with_the_welcome_body(spies):
            guild = GuildFactory(name="Forge", discord_webhook_url=_WEBHOOK, discord_welcome_message="From the lead!")
            member = MemberFactory(discord_user_id="555")
            result = _join_guild(_interaction(guild.slug), member)
            assert result["data"]["flags"] == 64
            assert "You now follow **Forge**" in result["data"]["content"]
            assert "From the lead!" in result["data"]["content"]

    def describe_the_member_welcome_email():
        def _linked_member(username: str, discord_id: str) -> object:
            MembershipPlanFactory()
            user = User.objects.create_user(username=username, password="pw", email=f"{username}@example.com")
            member = user.member
            member.discord_user_id = discord_id
            member.save(update_fields=["discord_user_id"])
            return member

        def it_sends_the_welcome_on_a_brand_new_join(spies):
            member = _linked_member("dcw1", "901")
            guild = GuildFactory(discord_webhook_url=_WEBHOOK)
            GuildOrientationSettingsFactory(guild=guild)
            _join_guild(_interaction(guild.slug), member)
            assert len(mail.outbox) == 1
            assert mail.outbox[0].to == [member.primary_email]

        def it_does_not_welcome_on_an_upgraded_reaction_join(spies):
            member = _linked_member("dcw2", "902")
            guild = GuildFactory(discord_webhook_url=_WEBHOOK)
            GuildOrientationSettingsFactory(guild=guild)
            GuildMembership.objects.record_discord_join(guild, member)
            _join_guild(_interaction(guild.slug), member)
            assert mail.outbox == []

    def describe_welcome_copy():
        def it_uses_the_generic_fallback_when_blank(spies):
            guild = GuildFactory(name="Kiln", discord_webhook_url=_WEBHOOK, discord_welcome_message="")
            member = MemberFactory(discord_user_id="1")
            result = _join_guild(_interaction(guild.slug), member)
            fallback = "Welcome to Kiln! A lead will say hi soon."
            assert spies.post.call_args.args[1].body == fallback
            assert fallback in result["data"]["content"]

    def describe_an_upgraded_reaction_join():
        def it_ensures_the_role_but_does_not_re_welcome(spies):
            # A standing Discord-reaction row is upgraded to an app join (created=False, upgraded=True).
            guild = GuildFactory(discord_webhook_url=_WEBHOOK)
            member = MemberFactory(discord_user_id="1")
            GuildMembership.objects.record_discord_join(guild, member)
            result = _join_guild(_interaction(guild.slug), member)
            spies.role.assert_called_once_with(guild, member, joined=True)
            spies.post.assert_not_called()
            spies.fanout.assert_not_called()
            assert "You now follow" in result["data"]["content"]

    def describe_an_existing_app_member_self_heal():
        def it_re_ensures_the_role_and_says_already_in(spies):
            # An existing app row (created=False, upgraded=False) still re-runs the role add.
            guild = GuildFactory(discord_webhook_url=_WEBHOOK)
            member = MemberFactory(discord_user_id="1")
            GuildMembership.objects.record_app_join(guild, member)
            result = _join_guild(_interaction(guild.slug), member)
            spies.role.assert_called_once_with(guild, member, joined=True)
            spies.post.assert_not_called()
            spies.fanout.assert_not_called()
            assert "already follow" in result["data"]["content"]

    def describe_when_the_welcome_fanout_raises():
        def it_still_returns_the_confirmation_with_the_role_already_assigned(spies):
            spies.fanout.side_effect = RuntimeError("email down")
            guild = GuildFactory(name="Forge", discord_webhook_url=_WEBHOOK)
            member = MemberFactory(discord_user_id="1")
            result = _join_guild(_interaction(guild.slug), member)  # must not raise
            assert "You now follow **Forge**" in result["data"]["content"]
            spies.role.assert_called_once_with(guild, member, joined=True)

    def describe_an_unknown_or_inactive_guild():
        def it_replies_not_found_and_writes_nothing(spies):
            member = MemberFactory(discord_user_id="1")
            result = _join_guild(_interaction("does-not-exist"), member)
            assert "wasn't found" in result["data"]["content"]
            assert result["data"]["flags"] == 64
            assert GuildMembership.objects.count() == 0
            spies.role.assert_not_called()
            spies.post.assert_not_called()
            spies.fanout.assert_not_called()

        def it_treats_an_inactive_guild_as_not_found(spies):
            guild = GuildFactory(is_active=False)
            member = MemberFactory(discord_user_id="1")
            result = _join_guild(_interaction(guild.slug), member)
            assert "wasn't found" in result["data"]["content"]
            assert not GuildMembership.objects.filter(guild=guild).exists()

    def describe_a_guild_that_does_not_post_publicly():
        def it_joins_without_a_public_post_when_posting_is_disabled(spies):
            guild = GuildFactory(discord_webhook_url=_WEBHOOK, discord_post_enabled=False)
            member = MemberFactory(discord_user_id="1")
            result = _join_guild(_interaction(guild.slug), member)
            spies.post.assert_not_called()  # guild_webhook() returns "" when posting is off
            spies.fanout.assert_called_once()  # the fan-out still runs on a brand-new join
            assert "You now follow" in result["data"]["content"]

        def it_joins_without_a_public_post_when_the_webhook_is_blank(spies):
            guild = GuildFactory(discord_webhook_url="")
            member = MemberFactory(discord_user_id="1")
            result = _join_guild(_interaction(guild.slug), member)
            spies.post.assert_not_called()
            spies.fanout.assert_called_once()
            assert "You now follow" in result["data"]["content"]


def describe_join_guild_end_to_end():
    @respx.mock
    def it_issues_the_role_put_and_posts_the_welcome_webhook(settings, monkeypatch):
        from core.models import SiteConfiguration

        settings.DISCORD_BOT_TOKEN = "bot"
        config = SiteConfiguration.load()
        config.discord_server_id = "srv"
        config.save()
        # Keep the fan-out out of the way; it has its own specs. This test proves the two
        # raw Discord REST calls fire with the right payloads.
        monkeypatch.setattr(orientations, "member_joined_guild", mock.MagicMock())
        guild = GuildFactory(
            name="Forge",
            discord_role_ids=["role1"],
            discord_webhook_url=_WEBHOOK,
            discord_post_enabled=True,
            discord_welcome_message="Hi from the lead",
        )
        member = MemberFactory(discord_user_id="555", preferred_name="Robin")
        role_route = respx.put("https://discord.com/api/v10/guilds/srv/members/555/roles/role1").mock(
            return_value=httpx.Response(204)
        )
        webhook_route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))

        result = _join_guild(_interaction(guild.slug), member)

        assert role_route.called
        assert webhook_route.called
        payload = json.loads(webhook_route.calls.last.request.content)
        assert payload["embeds"][0]["title"] == "Welcome Robin to Forge!"
        assert payload["embeds"][0]["description"] == "Hi from the lead"
        # No user ping rides along — neither a raw content mention nor parse:everyone.
        assert "content" not in payload
        assert "allowed_mentions" not in payload
        assert "You now follow **Forge**" in result["data"]["content"]


def describe_guild_choices():
    def it_returns_one_choice_per_active_guild_keyed_by_slug():
        g1 = GuildFactory(name="Alpha")
        g2 = GuildFactory(name="Beta")
        (option,) = _guild_choices()
        assert option["name"] == "guild"
        assert option["type"] == 3
        assert option["required"] is True
        assert {c["value"] for c in option["choices"]} == {g1.slug, g2.slug}
        assert {c["name"] for c in option["choices"]} == {"Alpha", "Beta"}

    def it_orders_choices_by_guild_name():
        GuildFactory(name="Zeta")
        GuildFactory(name="Alpha")
        names = [c["name"] for c in _guild_choices()[0]["choices"]]
        assert names == sorted(names)

    def it_excludes_inactive_guilds():
        active = GuildFactory(name="Active")
        GuildFactory(name="Inactive", is_active=False)
        values = [c["value"] for c in _guild_choices()[0]["choices"]]
        assert values == [active.slug]

    def it_caps_at_25_and_logs_a_warning(caplog):
        for i in range(27):
            GuildFactory(name=f"Guild {i:02d}")
        with caplog.at_level(logging.WARNING):
            option = _guild_choices()[0]
        assert len(option["choices"]) == 25
        assert "dropped from the picker" in caplog.text

    def it_omits_the_choices_key_when_there_are_no_active_guilds():
        option = _guild_choices()[0]
        assert "choices" not in option
        assert option["required"] is True


def describe_welcome_body():
    def it_returns_the_leads_message_stripped_when_set():
        guild = GuildFactory(discord_welcome_message="  Hello, maker!  ")
        assert _welcome_body(guild) == "Hello, maker!"

    def it_falls_back_to_a_generic_welcome_when_blank():
        guild = GuildFactory(name="Forge", discord_welcome_message="")
        assert _welcome_body(guild) == "Welcome to Forge! A lead will say hi soon."


def describe_JOIN_GUILD_command():
    def it_is_registered_and_reachable_by_name():
        from core.events.discord_commands import all_commands

        assert "join-guild" in [c.name for c in all_commands()]

    def it_keeps_the_command_name_but_describes_following():
        # Deliberate: the NAME stays /join-guild (muscle memory); the copy carries the reframe.
        assert JOIN_GUILD.name == "join-guild"
        assert JOIN_GUILD.description == "Follow a Past Lives guild to get its updates."

    def it_requires_a_link_defers_and_is_ephemeral():
        assert JOIN_GUILD.requires_link is True
        assert JOIN_GUILD.defer is True
        assert JOIN_GUILD.ephemeral is True
        assert JOIN_GUILD.scope == "guild"

    def it_builds_its_guild_option_from_the_active_guilds():
        GuildFactory(name="Alpha")
        option = JOIN_GUILD.to_api_dict()["options"][0]
        assert option["name"] == "guild"
        assert option["choices"][0]["name"] == "Alpha"
