"""BDD specs for membership.discord_sync — import, reconcile, and link orchestration.

All Discord HTTP (reactions + roles + OAuth) is mocked with ``respx``. Covers the
provenance/anti-oscillation behavior, the completeness guardrail, and every link outcome.
"""

from __future__ import annotations

from urllib.parse import unquote

import httpx
import pytest
import respx
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core import mail
from django.db.models.signals import post_save
from factory.django import mute_signals

from membership import discord_sync
from membership.discord_sync import LinkOutcome
from membership.models import DiscordLinkNudge, GuildMembership
from tests.membership.factories import DiscordGuildEmojiFactory, GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db

_REACTIONS_RE = r"https://discord\.com/api/v10/channels/.+/messages/.+/reactions/.+"
_ROLE_RE = r"https://discord\.com/api/v10/guilds/.+/members/.+/roles/.+"
_TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
_IDENTITY_URL = "https://discord.com/api/v10/users/@me"
_REDIRECT = "http://pastlives.test:8000/discord/link/callback/"
_DM_CHANNELS_URL = "https://discord.com/api/v10/users/@me/channels"
_DM_MESSAGES_URL = "https://discord.com/api/v10/channels/dm99/messages"


@pytest.fixture
def sync_config(settings):
    from core.models import SiteConfiguration

    settings.DISCORD_BOT_TOKEN = "bot-tok"
    settings.DISCORD_CLIENT_ID = "cid"
    settings.DISCORD_CLIENT_SECRET = "secret"
    config = SiteConfiguration.load()
    config.discord_server_id = "srv"
    config.discord_role_message_channel_id = "chan"
    config.discord_role_message_id = "msg"
    config.save()
    return config


def _mock_reactions(reactors_by_emoji, *, flaky=()):
    def handler(request):
        encoded = request.url.path.rsplit("/", 1)[-1]
        emoji = unquote(encoded)
        if emoji in flaky:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=[{"id": uid} for uid in reactors_by_emoji.get(emoji, [])])

    respx.get(url__regex=_REACTIONS_RE).mock(side_effect=handler)


def _mock_roles():
    respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
    respx.delete(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))


def _mock_dm(message_response: httpx.Response | None = None):
    """Mock the two DM calls (open channel → post message); returns the message route."""
    respx.post(_DM_CHANNELS_URL).mock(return_value=httpx.Response(200, json={"id": "dm99"}))
    return respx.post(_DM_MESSAGES_URL).mock(
        return_value=message_response if message_response is not None else httpx.Response(200, json={"id": "m1"})
    )


def _verified_member(email: str, *, discord_user_id: str = ""):
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u_{email}", email=email)
    member = MemberFactory(discord_user_id=discord_user_id)
    member.user = user
    member.save(update_fields=["user", "discord_user_id"])
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    return member


def describe_import_member_guilds():
    @respx.mock
    def it_creates_discord_rows_for_reacted_guilds(sync_config):
        guild = GuildFactory(name="Glass")
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        member = MemberFactory(discord_user_id="u1")
        _mock_reactions({"🔥": ["u1"]})
        _mock_roles()
        result = discord_sync.import_member_guilds(member)
        assert [g.name for g in result.guilds] == ["Glass"]
        assert result.complete is True
        row = GuildMembership.objects.get(guild=guild, member=member)
        assert row.source == GuildMembership.Source.DISCORD

    @respx.mock
    def it_does_not_fire_a_welcome_email_for_imported_guilds(sync_config):
        guild = GuildFactory()
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        member = MemberFactory(discord_user_id="u1")
        _mock_reactions({"🔥": ["u1"]})
        _mock_roles()
        discord_sync.import_member_guilds(member)
        assert mail.outbox == []  # no per-join welcome storm

    @respx.mock
    def it_pushes_existing_app_guilds_as_roles(sync_config):
        app_guild = GuildFactory(discord_role_ids=["r1"])
        member = MemberFactory(discord_user_id="u1")
        GuildMembership.objects.record_app_join(app_guild, member)
        put = respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        _mock_reactions({})
        discord_sync.import_member_guilds(member)
        assert put.called  # the app guild's role was assigned on Discord

    @respx.mock
    def it_reports_incomplete_when_a_fetch_is_truncated(sync_config):
        g1, g2 = GuildFactory(name="A"), GuildFactory(name="B")
        DiscordGuildEmojiFactory(emoji="🔥", guild=g1)
        DiscordGuildEmojiFactory(emoji="🎨", guild=g2)
        member = MemberFactory(discord_user_id="u1")
        _mock_reactions({"🔥": ["u1"]}, flaky=["🎨"])
        _mock_roles()
        result = discord_sync.import_member_guilds(member)
        assert [g.name for g in result.guilds] == ["A"]
        assert result.complete is False  # the 🎨 fetch was truncated

    @respx.mock
    def it_returns_no_guilds_when_the_member_reacted_to_nothing(sync_config):
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory())
        member = MemberFactory(discord_user_id="u1")
        _mock_reactions({"🔥": ["other"]})
        _mock_roles()
        result = discord_sync.import_member_guilds(member)
        assert result.guilds == []

    def it_no_ops_when_unconfigured(settings):
        settings.DISCORD_BOT_TOKEN = ""
        member = MemberFactory(discord_user_id="u1")
        result = discord_sync.import_member_guilds(member)
        assert result.guilds == []


def describe_reconcile_reactions():
    def it_does_not_run_when_unconfigured(settings):
        settings.DISCORD_BOT_TOKEN = ""
        stats = discord_sync.reconcile_reactions()
        assert stats.ran is False

    @respx.mock
    def it_adds_a_discord_row_for_a_reactor(sync_config):
        guild = GuildFactory()
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        member = MemberFactory(discord_user_id="u1")
        _mock_reactions({"🔥": ["u1"]})
        stats = discord_sync.reconcile_reactions()
        assert stats.added == 1
        assert GuildMembership.objects.filter(
            guild=guild, member=member, source=GuildMembership.Source.DISCORD
        ).exists()

    @respx.mock
    def it_ignores_a_reactor_with_no_linked_member(sync_config):
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory())
        _mock_reactions({"🔥": ["unknown-id"]})
        _mock_dm()  # the unlinked reactor gets the one-time nudge, never a membership
        stats = discord_sync.reconcile_reactions()
        assert stats.added == 0

    @respx.mock
    def it_removes_a_discord_row_when_the_member_stops_reacting(sync_config):
        guild = GuildFactory()
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        member = MemberFactory(discord_user_id="u1")
        GuildMembership.objects.record_discord_join(guild, member)
        _mock_reactions({"🔥": []})  # complete fetch, nobody reacts
        stats = discord_sync.reconcile_reactions()
        assert stats.removed == 1
        assert not GuildMembership.objects.filter(guild=guild, member=member).exists()

    @respx.mock
    def it_never_removes_a_source_app_row(sync_config):
        guild = GuildFactory()
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        member = MemberFactory(discord_user_id="u1")
        GuildMembership.objects.record_app_join(guild, member)  # explicit in-app join
        _mock_reactions({"🔥": []})
        discord_sync.reconcile_reactions()
        assert GuildMembership.objects.filter(guild=guild, member=member, source=GuildMembership.Source.APP).exists()

    @respx.mock
    def it_skips_removals_on_an_incomplete_fetch(sync_config):
        guild = GuildFactory()
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        member = MemberFactory(discord_user_id="u1")
        GuildMembership.objects.record_discord_join(guild, member)
        _mock_reactions({"🔥": []}, flaky=["🔥"])  # 429 → incomplete
        stats = discord_sync.reconcile_reactions()
        assert stats.removed == 0
        assert stats.skipped_guilds == 1
        assert GuildMembership.objects.filter(guild=guild, member=member).exists()  # survives

    @respx.mock
    def it_collapses_two_emojis_onto_one_guild(sync_config):
        glass = GuildFactory(name="Glass")
        DiscordGuildEmojiFactory(emoji="✍️", guild=glass)
        DiscordGuildEmojiFactory(emoji="🔥", guild=glass)
        m1 = MemberFactory(discord_user_id="u1")
        m2 = MemberFactory(discord_user_id="u2")
        _mock_reactions({"✍️": ["u1"], "🔥": ["u2"]})
        stats = discord_sync.reconcile_reactions()
        assert stats.added == 2  # both members joined the one Glass guild
        assert GuildMembership.objects.filter(guild=glass, member=m1).exists()
        assert GuildMembership.objects.filter(guild=glass, member=m2).exists()


def describe_reconcile_nudges_unlinked_reactors():
    @respx.mock
    def it_dms_a_one_time_nudge_with_the_absolute_link_url(sync_config, settings):
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory())
        _mock_reactions({"🔥": ["stranger"]})
        msg = _mock_dm()
        stats = discord_sync.reconcile_reactions()
        assert stats.nudged == 1
        assert msg.called
        body = msg.calls.last.request.read().decode()
        assert "halfway there" in body
        assert f"{settings.MEMBER_BASE_URL}/discord/link/" in body  # absolute, not a bare path
        assert DiscordLinkNudge.objects.filter(discord_user_id="stranger").exists()

    @respx.mock
    def it_does_not_dm_the_same_reactor_twice(sync_config):
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory())
        _mock_reactions({"🔥": ["stranger"]})
        msg = _mock_dm()
        discord_sync.reconcile_reactions()
        stats = discord_sync.reconcile_reactions()  # the next 15-minute tick
        assert stats.nudged == 0
        assert msg.call_count == 1
        assert DiscordLinkNudge.objects.count() == 1

    @respx.mock
    def it_never_nudges_a_linked_reactor(sync_config):
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory())
        MemberFactory(discord_user_id="u1")
        _mock_reactions({"🔥": ["u1"]})
        msg = _mock_dm()
        stats = discord_sync.reconcile_reactions()
        assert stats.nudged == 0
        assert not msg.called
        assert not DiscordLinkNudge.objects.exists()

    @respx.mock
    def it_marks_the_reactor_nudged_when_their_dms_are_closed(sync_config):
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory())
        _mock_reactions({"🔥": ["stranger"]})
        _mock_dm(message_response=httpx.Response(403, json={"code": 50007, "message": "Cannot send messages"}))
        stats = discord_sync.reconcile_reactions()  # swallowed — never raises
        assert stats.nudged == 1
        assert DiscordLinkNudge.objects.filter(discord_user_id="stranger").exists()  # never retried

    @respx.mock
    def it_marks_the_reactor_nudged_on_a_403_with_a_different_error_code(sync_config):
        # A 403 that isn't the 50007 "DMs closed" code (e.g. a blocked bot / no mutual server)
        # used to raise and crash the cron on the same reactor every tick. Any 403 is now a
        # quiet, permanent "undeliverable" — the guard row is written so it's never retried.
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory())
        _mock_reactions({"🔥": ["stranger"]})
        _mock_dm(message_response=httpx.Response(403, json={"message": "Missing Access"}))
        stats = discord_sync.reconcile_reactions()  # must not raise
        assert stats.nudged == 1
        assert DiscordLinkNudge.objects.filter(discord_user_id="stranger").exists()

    @respx.mock
    def it_raises_and_leaves_the_reactor_unmarked_on_any_other_dm_error(sync_config):
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory())
        _mock_reactions({"🔥": ["stranger"]})
        _mock_dm(message_response=httpx.Response(500, text="boom"))
        with pytest.raises(httpx.HTTPStatusError):
            discord_sync.reconcile_reactions()
        assert not DiscordLinkNudge.objects.exists()  # unmarked → retried next tick

    @respx.mock
    def it_does_not_nudge_during_a_members_own_import(sync_config):
        # import_member_guilds only ever looks at the linking member's reactions —
        # other unlinked reactors on the message are the reconcile cron's business.
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory())
        member = MemberFactory(discord_user_id="u1")
        _mock_reactions({"🔥": ["u1", "stranger"]})
        _mock_roles()
        msg = _mock_dm()
        discord_sync.import_member_guilds(member)
        assert not msg.called
        assert not DiscordLinkNudge.objects.exists()


def describe_DiscordLinkNudge():
    def it_renders_the_nudged_discord_user_id():
        assert str(DiscordLinkNudge(discord_user_id="42")) == "Link nudge → 42"


def describe_link_and_import():
    def _mock_oauth(email: str, *, verified: bool = True, discord_user_id: str = "42"):
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
        respx.get(_IDENTITY_URL).mock(
            return_value=httpx.Response(
                200, json={"id": discord_user_id, "username": "jo", "email": email, "verified": verified}
            )
        )

    @respx.mock
    def it_links_and_sends_one_confirmation_on_a_verified_match(sync_config):
        member = _verified_member("jo@example.com")
        guild = GuildFactory()
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        _mock_oauth("jo@example.com")
        _mock_reactions({"🔥": ["42"]})
        _mock_roles()
        result = discord_sync.link_and_import("code", _REDIRECT)
        assert result.outcome is LinkOutcome.LINKED
        assert [g.name for g in result.guilds] == [guild.name]
        member.refresh_from_db()
        assert member.discord_user_id == "42"
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [member.primary_email]

    @respx.mock
    def it_does_not_email_on_an_empty_import(sync_config):
        _verified_member("jo@example.com")
        _mock_oauth("jo@example.com")
        _mock_reactions({})
        _mock_roles()
        result = discord_sync.link_and_import("code", _REDIRECT)
        assert result.outcome is LinkOutcome.LINKED
        assert result.guilds == []
        assert mail.outbox == []

    @respx.mock
    def it_carries_the_incomplete_flag(sync_config):
        _verified_member("jo@example.com")
        g = GuildFactory()
        DiscordGuildEmojiFactory(emoji="🔥", guild=g)
        _mock_oauth("jo@example.com")
        _mock_reactions({"🔥": ["42"]}, flaky=["🔥"])
        _mock_roles()
        result = discord_sync.link_and_import("code", _REDIRECT)
        # 🔥 truncated → the member is never seen, so no guild AND incomplete.
        assert result.complete is False

    @respx.mock
    def it_needs_login_when_the_email_is_unverified(sync_config):
        _verified_member("jo@example.com")
        _mock_oauth("jo@example.com", verified=False)
        result = discord_sync.link_and_import("code", _REDIRECT)
        assert result.outcome is LinkOutcome.NEEDS_LOGIN

    @respx.mock
    def it_needs_login_when_no_account_matches(sync_config):
        _mock_oauth("stranger@example.com")
        result = discord_sync.link_and_import("code", _REDIRECT)
        assert result.outcome is LinkOutcome.NEEDS_LOGIN

    @respx.mock
    def it_refuses_when_the_discord_is_linked_to_another_member(sync_config):
        MemberFactory(discord_user_id="42")  # already owns this Discord id
        _verified_member("jo@example.com")
        _mock_oauth("jo@example.com", discord_user_id="42")
        result = discord_sync.link_and_import("code", _REDIRECT)
        assert result.outcome is LinkOutcome.ALREADY_LINKED_ELSEWHERE

    @respx.mock
    def it_refuses_when_the_member_already_has_a_different_discord(sync_config):
        member = _verified_member("jo@example.com", discord_user_id="99")
        _mock_oauth("jo@example.com", discord_user_id="42")
        result = discord_sync.link_and_import("code", _REDIRECT, member=member)
        assert result.outcome is LinkOutcome.ACCOUNT_HAS_OTHER_DISCORD

    @respx.mock
    def it_re_links_the_same_discord_idempotently(sync_config):
        member = _verified_member("jo@example.com", discord_user_id="42")
        _mock_oauth("jo@example.com", discord_user_id="42")
        _mock_reactions({})
        _mock_roles()
        result = discord_sync.link_and_import("code", _REDIRECT, member=member)
        assert result.outcome is LinkOutcome.LINKED

    @respx.mock
    def it_returns_oauth_failed_on_an_oauth_error(sync_config):
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(400))
        result = discord_sync.link_and_import("code", _REDIRECT)
        assert result.outcome is LinkOutcome.OAUTH_FAILED

    @respx.mock
    def it_links_the_logged_in_member_without_an_email_match(sync_config):
        member = _verified_member("different@example.com")  # email won't match the Discord email
        _mock_oauth("someone-else@example.com", discord_user_id="42")
        _mock_reactions({})
        _mock_roles()
        result = discord_sync.link_and_import("code", _REDIRECT, member=member)
        assert result.outcome is LinkOutcome.LINKED
        member.refresh_from_db()
        assert member.discord_user_id == "42"
