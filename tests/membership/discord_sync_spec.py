"""BDD specs for membership.discord_sync: import, reconcile, roster roles and link orchestration.

All Discord HTTP (reactions + roles + OAuth) is mocked with ``respx``. Covers provenance, the
add only reaction mirror and roster sync (#1270), the import's completeness flag, and every link
outcome.
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
from membership.models import DiscordLinkNudge, DiscordReconnectNudge, GuildInvite, GuildMembership, Member
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
    def it_pushes_a_guild_joined_by_an_accepted_invite_as_a_role(sync_config):
        # #1137: a source="invite" row is an in-app join, so linking Discord gives its role too.
        invite_guild = GuildFactory(discord_role_ids=["r9"])
        member = MemberFactory(discord_user_id="u1")
        GuildMembership.objects.record_app_join(invite_guild, member, GuildMembership.Source.INVITE)
        put = respx.put(url__regex=r"https://discord\.com/api/v10/guilds/.+/members/u1/roles/r9").mock(
            return_value=httpx.Response(204)
        )
        _mock_reactions({})
        discord_sync.import_member_guilds(member)
        assert put.called

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
    def it_closes_an_open_guild_invite_when_a_reaction_joins_the_member(sync_config):
        # #1137: the reaction sync is a join path too, so the invite banner never offers a guild they are in.
        guild = GuildFactory()
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        member = MemberFactory(discord_user_id="u1")
        invite = GuildInvite.objects.create(guild=guild, member=member)
        _mock_reactions({"🔥": ["u1"]})
        discord_sync.reconcile_reactions()
        invite.refresh_from_db()
        assert not invite.is_open

    @respx.mock
    def it_ignores_a_reactor_with_no_linked_member(sync_config):
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory())
        _mock_reactions({"🔥": ["unknown-id"]})
        _mock_dm()  # the unlinked reactor gets the one-time nudge, never a membership
        stats = discord_sync.reconcile_reactions()
        assert stats.added == 0

    @respx.mock
    def it_keeps_a_discord_row_when_the_member_stops_reacting(sync_config):
        guild = GuildFactory()
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        member = MemberFactory(discord_user_id="u1")
        GuildMembership.objects.record_discord_join(guild, member)
        _mock_reactions({"🔥": []})  # complete fetch, nobody reacts
        discord_sync.reconcile_reactions()
        assert GuildMembership.objects.filter(guild=guild, member=member).exists()

    @respx.mock
    def it_marks_a_reaction_row_whose_member_stopped_reacting_and_takes_the_role(sync_config):
        guild = GuildFactory(discord_role_ids=["71"])
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        member = MemberFactory(discord_user_id="u1")
        GuildMembership.objects.record_discord_join(guild, member)
        _mock_reactions({"🔥": []})
        delete = respx.delete(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        stats = discord_sync.reconcile_reactions()
        row = GuildMembership.objects.get(guild=guild, member=member)
        assert row.no_discord_role is True
        assert (stats.marked, stats.taken) == (1, 1)
        assert _put_roles(delete.calls) == [("u1", "71")]

    @respx.mock
    def it_leaves_a_portal_join_and_a_truncated_guild_unmarked(sync_config):
        portal, truncated = GuildFactory(discord_role_ids=["71"]), GuildFactory(discord_role_ids=["72"])
        DiscordGuildEmojiFactory(emoji="🔥", guild=portal)
        DiscordGuildEmojiFactory(emoji="🪓", guild=truncated)
        member = MemberFactory(discord_user_id="u1")
        GuildMembership.objects.record_app_join(portal, member)
        GuildMembership.objects.record_discord_join(truncated, member)
        _mock_reactions({"🔥": []}, flaky=("🪓",))
        delete = respx.delete(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        stats = discord_sync.reconcile_reactions()
        assert not GuildMembership.objects.filter(member=member, no_discord_role=True).exists()
        assert (stats.marked, stats.taken) == (0, 0)
        assert not delete.called

    @respx.mock
    def it_counts_no_role_taken_when_discord_refuses(sync_config, monkeypatch):
        from core.events import discord_roles

        monkeypatch.setattr(discord_roles, "_sleep", lambda seconds: None)
        guild = GuildFactory(discord_role_ids=["71"])
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        GuildMembership.objects.record_discord_join(guild, MemberFactory(discord_user_id="u1"))
        _mock_reactions({"🔥": []})
        respx.delete(url__regex=_ROLE_RE).mock(return_value=httpx.Response(403))
        stats = discord_sync.reconcile_reactions()
        assert (stats.marked, stats.taken) == (1, 0)

    @respx.mock
    def it_skips_the_daily_role_for_a_member_who_un_reacted(sync_config):
        guild = GuildFactory(discord_role_ids=["71"])
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        GuildMembership.objects.record_discord_join(guild, MemberFactory(discord_user_id="u1"))
        _mock_reactions({"🔥": []})
        respx.delete(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        discord_sync.reconcile_reactions()
        _mock_server({"u1": []})
        put = respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        discord_sync.sync_roster_roles()
        assert not put.called

    @respx.mock
    def it_clears_the_no_discord_role_mark_when_the_member_reacts_again(sync_config):
        guild, other = GuildFactory(), GuildFactory()
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        member = MemberFactory(discord_user_id="u1")
        GuildMembership.objects.record_app_join(guild, member)
        GuildMembership.objects.record_app_join(other, member)
        GuildMembership.objects.filter(member=member).update(no_discord_role=True)
        _mock_reactions({"🔥": ["u1"]})
        stats = discord_sync.reconcile_reactions()
        assert stats.unmarked == 1
        marks = dict(GuildMembership.objects.filter(member=member).values_list("guild_id", "no_discord_role"))
        assert marks == {guild.pk: False, other.pk: True}

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


def _mock_reactions_by_message(reactors, *, flaky=()):
    """Mock reactions per ``(message_id, emoji)``; ``flaky`` pairs answer 429 (a truncated read)."""

    def handler(request):
        parts = request.url.path.split("/")
        message_id, emoji = parts[-3], unquote(parts[-1])
        if (message_id, emoji) in flaky:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=[{"id": uid} for uid in reactors.get((message_id, emoji), [])])

    return respx.get(url__regex=_REACTIONS_RE).mock(side_effect=handler)


def describe_reconcile_across_role_messages():
    @pytest.fixture
    def two_messages(sync_config):
        sync_config.discord_role_message_extra_ids = "msg2"
        sync_config.save()
        return sync_config

    @respx.mock
    def it_joins_a_guild_from_a_reaction_on_the_extra_message(two_messages):
        music = GuildFactory(name="Music")
        DiscordGuildEmojiFactory(emoji="🎵", guild=music)
        member = MemberFactory(discord_user_id="u1")
        _mock_reactions_by_message({("msg2", "🎵"): ["u1"]})
        stats = discord_sync.reconcile_reactions()
        assert stats.added == 1
        assert GuildMembership.objects.filter(guild=music, member=member).exists()

    @respx.mock
    def it_reads_every_emoji_on_every_message(two_messages):
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory())
        DiscordGuildEmojiFactory(emoji="🎵", guild=GuildFactory())
        route = _mock_reactions_by_message({})
        discord_sync.reconcile_reactions()
        read = {(c.request.url.path.split("/")[-3], unquote(c.request.url.path.split("/")[-1])) for c in route.calls}
        assert read == {("msg", "🔥"), ("msg", "🎵"), ("msg2", "🔥"), ("msg2", "🎵")}

    @respx.mock
    def it_keeps_a_member_whose_reaction_is_only_on_the_other_message(two_messages):
        glass = GuildFactory(name="Glass")
        DiscordGuildEmojiFactory(emoji="🔥", guild=glass)
        member = MemberFactory(discord_user_id="u1")
        GuildMembership.objects.record_discord_join(glass, member)
        _mock_reactions_by_message({("msg2", "🔥"): ["u1"]})  # gone from msg, still on msg2
        discord_sync.reconcile_reactions()
        assert GuildMembership.objects.filter(guild=glass, member=member).exists()

    @respx.mock
    def it_unions_reactors_across_messages(two_messages):
        glass = GuildFactory(name="Glass")
        DiscordGuildEmojiFactory(emoji="🔥", guild=glass)
        m1 = MemberFactory(discord_user_id="u1")
        m2 = MemberFactory(discord_user_id="u2")
        gone = MemberFactory(discord_user_id="u3")
        GuildMembership.objects.record_discord_join(glass, gone)
        _mock_reactions_by_message({("msg", "🔥"): ["u1"], ("msg2", "🔥"): ["u2"]})
        stats = discord_sync.reconcile_reactions()
        assert stats.added == 2
        members = set(GuildMembership.objects.filter(guild=glass).values_list("member_id", flat=True))
        assert members == {m1.pk, m2.pk, gone.pk}  # un-reacting removes nobody (#1270)

    @respx.mock
    def it_nudges_an_unlinked_reactor_once_when_they_react_on_both_messages(two_messages):
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory())
        DiscordGuildEmojiFactory(emoji="🎵", guild=GuildFactory())
        _mock_reactions_by_message({("msg", "🔥"): ["stranger"], ("msg2", "🎵"): ["stranger"]})
        msg = _mock_dm()
        stats = discord_sync.reconcile_reactions()
        assert stats.nudged == 1
        assert msg.call_count == 1
        assert DiscordLinkNudge.objects.filter(discord_user_id="stranger").count() == 1

    @respx.mock
    def it_imports_a_members_guilds_from_both_messages(two_messages):
        glass, music = GuildFactory(name="Glass"), GuildFactory(name="Music")
        DiscordGuildEmojiFactory(emoji="🔥", guild=glass)
        DiscordGuildEmojiFactory(emoji="🎵", guild=music)
        member = MemberFactory(discord_user_id="u1")
        _mock_reactions_by_message({("msg", "🔥"): ["u1"], ("msg2", "🎵"): ["u1"]})
        _mock_roles()
        result = discord_sync.import_member_guilds(member)
        assert [g.name for g in result.guilds] == ["Glass", "Music"]
        assert result.complete is True

    @respx.mock
    def it_marks_an_import_incomplete_when_the_extra_message_is_truncated(two_messages):
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory(name="Glass"))
        member = MemberFactory(discord_user_id="u1")
        _mock_reactions_by_message({("msg", "🔥"): ["u1"]}, flaky=[("msg2", "🔥")])
        _mock_roles()
        result = discord_sync.import_member_guilds(member)
        assert [g.name for g in result.guilds] == ["Glass"]
        assert result.complete is False

    @respx.mock
    def it_reads_only_the_main_message_when_the_extra_ids_are_blank(sync_config):
        glass = GuildFactory(name="Glass")
        DiscordGuildEmojiFactory(emoji="🔥", guild=glass)
        member = MemberFactory(discord_user_id="u1")
        GuildMembership.objects.record_discord_join(glass, member)
        route = _mock_reactions_by_message({})
        discord_sync.reconcile_reactions()
        assert {c.request.url.path.split("/")[-3] for c in route.calls} == {"msg"}

    def it_does_not_run_on_extra_ids_alone(sync_config):
        sync_config.discord_role_message_id = ""
        sync_config.discord_role_message_extra_ids = "msg2"
        sync_config.save()
        assert discord_sync.reconcile_reactions().ran is False


_MEMBERS_URL = "https://discord.com/api/v10/guilds/srv/members"


def _mock_server(roles_by_user):
    """The server member list: each Discord user id on the server and the role ids they hold."""
    return respx.get(_MEMBERS_URL).mock(
        return_value=httpx.Response(200, json=[{"user": {"id": u}, "roles": r} for u, r in roles_by_user.items()])
    )


def _put_roles(calls):
    return sorted(tuple(c.request.url.path.split("/")[-3::2]) for c in calls)


def describe_reconcile_gives_reactors_their_roles():
    @respx.mock
    def it_gives_every_reactor_linked_or_not_each_guild_role_they_lack(sync_config):
        glass = GuildFactory(name="Glass", discord_role_ids=["71", "72"])
        DiscordGuildEmojiFactory(emoji="🔥", guild=glass)
        MemberFactory(discord_user_id="u1")
        _mock_reactions({"🔥": ["u1", "stranger", "gone"]})
        _mock_dm()
        _mock_server({"u1": ["71"], "stranger": []})  # "gone" left the server
        put = respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        delete = respx.delete(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        stats = discord_sync.reconcile_reactions()
        assert _put_roles(put.calls) == [("stranger", "71"), ("stranger", "72"), ("u1", "72")]
        assert stats.roles == discord_sync.RoleGrants(given=3, failed=0)
        assert not delete.called

    @respx.mock
    def it_reads_no_member_list_when_no_reacted_guild_has_a_role(sync_config):
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory())
        MemberFactory(discord_user_id="u1")
        _mock_reactions({"🔥": ["u1"]})
        members = _mock_server({})
        assert discord_sync.reconcile_reactions().roles == discord_sync.RoleGrants()
        assert not members.called

    @respx.mock
    def it_counts_a_refused_or_failed_grant_and_an_unreadable_member_list(sync_config, monkeypatch):
        from core.events import discord_roles

        monkeypatch.setattr(discord_roles, "_sleep", lambda seconds: None)
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory(discord_role_ids=["71", "72"]))
        MemberFactory(discord_user_id="u1")
        _mock_reactions({"🔥": ["u1"]})
        _mock_server({"u1": []})
        respx.put(url__regex=_ROLE_RE).mock(side_effect=[httpx.Response(403), httpx.Response(500)])
        assert discord_sync.reconcile_reactions().roles == discord_sync.RoleGrants(given=0, failed=2)
        respx.get(_MEMBERS_URL).mock(return_value=httpx.Response(500))
        assert discord_sync.reconcile_reactions().roles == discord_sync.RoleGrants(failed=1)

    @respx.mock
    def it_leaves_alone_a_grant_discord_calls_benign(sync_config):
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory(discord_role_ids=["71"]))
        MemberFactory(discord_user_id="u1")
        _mock_reactions({"🔥": ["u1"]})
        _mock_server({"u1": []})
        respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(404))  # left mid run
        assert discord_sync.reconcile_reactions().roles == discord_sync.RoleGrants()


def describe_sync_roster_roles():
    @respx.mock
    def it_gives_each_linked_roster_member_their_guilds_roles_and_takes_nothing(sync_config):
        wood = GuildFactory(name="Wood", discord_role_ids=["61"])
        glass = GuildFactory(name="Glass", discord_role_ids=["71", "72"])
        hidden = GuildFactory(name="Hidden", is_active=False, discord_role_ids=["81"])
        gone = GuildFactory(name="Gone", discord_role_ids=["91"])
        gone.soft_delete()
        plain = GuildFactory(name="No role")
        amy = MemberFactory(discord_user_id="u1")
        bo = MemberFactory(discord_user_id="u2")
        unlinked = MemberFactory(discord_user_id="")
        for guild in (wood, glass, hidden, gone, plain):
            GuildMembership.objects.record_app_join(guild, amy)
        GuildMembership.objects.record_discord_join(glass, bo)
        GuildMembership.objects.record_app_join(wood, bo)
        GuildMembership.objects.filter(guild=wood, member=bo).update(no_discord_role=True)  # un-reacted
        GuildMembership.objects.record_app_join(wood, unlinked)
        _mock_server({"u1": ["61", "999"], "u2": ["72"]})
        put = respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        delete = respx.delete(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))

        grants = discord_sync.sync_roster_roles()

        assert _put_roles(put.calls) == [("u1", "71"), ("u1", "72"), ("u1", "81"), ("u2", "71")]
        assert grants == discord_sync.RoleGrants(given=4, failed=0)
        assert not delete.called

    def it_does_nothing_with_a_blank_bot_token_or_server_id(sync_config, settings):
        sync_config.discord_server_id = " "
        sync_config.save()
        assert discord_sync.sync_roster_roles() is None
        settings.DISCORD_BOT_TOKEN = ""
        assert discord_sync.sync_roster_roles() is None


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
    def _mock_oauth(email: str, *, verified: bool = True, discord_user_id: str = "42") -> respx.Route:
        """Mock the code exchange and identity; returns the server join route for that account."""
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
        respx.get(_IDENTITY_URL).mock(
            return_value=httpx.Response(
                200, json={"id": discord_user_id, "username": "jo", "email": email, "verified": verified}
            )
        )
        return respx.put(f"https://discord.com/api/v10/guilds/srv/members/{discord_user_id}").mock(
            return_value=httpx.Response(201, json={})
        )

    @respx.mock
    def it_links_and_sends_one_confirmation_on_a_verified_match(sync_config, django_capture_on_commit_callbacks):
        member = _verified_member("jo@example.com")
        guild = GuildFactory()
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        _mock_oauth("jo@example.com")
        _mock_reactions({"🔥": ["42"]})
        _mock_roles()
        with django_capture_on_commit_callbacks(execute=True):
            result = discord_sync.link_and_import("code", _REDIRECT)
        assert result.outcome is LinkOutcome.LINKED
        assert result.member == member
        assert GuildMembership.objects.filter(guild=guild, member=member).exists()
        member.refresh_from_db()
        assert member.discord_user_id == "42"
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [member.primary_email]

    @respx.mock
    def it_queues_the_import_and_reads_no_reactions_itself(sync_config, django_capture_on_commit_callbacks):
        # Feedback request 15: reading every reaction inline ran the callback past 30 seconds.
        member = _verified_member("jo@example.com")
        DiscordGuildEmojiFactory(emoji="🔥", guild=GuildFactory())
        _mock_oauth("jo@example.com")
        reactions = respx.get(url__regex=_REACTIONS_RE).mock(return_value=httpx.Response(200, json=[{"id": "42"}]))
        _mock_roles()
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            result = discord_sync.link_and_import("code", _REDIRECT)
        assert result.outcome is LinkOutcome.LINKED
        assert not reactions.called
        assert not GuildMembership.objects.filter(member=member).exists()
        assert mail.outbox == []
        for callback in callbacks:  # the commit: the queued import runs now
            callback()
        assert reactions.called
        assert len(mail.outbox) == 1

    @respx.mock
    def it_closes_an_open_reconnect_nudge_once_they_are_on_the_server(sync_config):
        # #1239: a member linked before #1138 reconnects from the Home item.
        member = _verified_member("jo@example.com", discord_user_id="42")
        DiscordReconnectNudge.objects.create(member=member)
        _mock_oauth("jo@example.com")
        _mock_reactions({})
        _mock_roles()
        result = discord_sync.link_and_import("code", _REDIRECT, member=member)
        assert result.outcome is LinkOutcome.LINKED
        nudge = DiscordReconnectNudge.objects.get(member=member)
        assert nudge.status == DiscordReconnectNudge.Status.DONE
        assert nudge.closed_at is not None

    @respx.mock
    def it_keeps_the_reconnect_nudge_open_when_discord_refuses_the_join(sync_config):
        member = _verified_member("jo@example.com", discord_user_id="42")
        DiscordReconnectNudge.objects.create(member=member)
        _mock_oauth("jo@example.com").mock(return_value=httpx.Response(403, json={}))
        _mock_reactions({})
        _mock_roles()
        result = discord_sync.link_and_import("code", _REDIRECT, member=member)
        assert result.outcome is LinkOutcome.LINKED
        assert DiscordReconnectNudge.objects.open_for(member).exists()

    @respx.mock
    def it_puts_a_member_the_bot_removed_back_on_the_server(sync_config):
        from datetime import date

        from django.utils import timezone

        from membership.models import DiscordRemoval

        member = _verified_member("jo@example.com", discord_user_id="42")
        removal = DiscordRemoval.objects.create(
            member=member,
            discord_user_id="42",
            source=DiscordRemoval.Source.FORMER,
            status=DiscordRemoval.Status.KICKED,
            warn_on=date(2026, 10, 1),
            kicked_at=timezone.now(),
        )
        join = _mock_oauth("jo@example.com")
        _mock_reactions({})
        _mock_roles()
        assert discord_sync.link_and_import("code", _REDIRECT).outcome is LinkOutcome.LINKED
        assert join.calls.last.request.read() == b'{"access_token":"tok"}'
        removal.refresh_from_db()
        assert removal.rejoined_at is not None
        from core.models import AutomationAction

        assert AutomationAction.objects.filter(kind="discord_rejoin", result="ok").exists()

    @respx.mock
    def it_puts_a_member_who_is_not_active_on_the_server(sync_config):
        member = _verified_member("jo@example.com")
        Member.objects.filter(pk=member.pk).update(status=Member.Status.FORMER)
        member.refresh_from_db()
        join = _mock_oauth("jo@example.com")
        _mock_reactions({})
        _mock_roles()
        assert discord_sync.link_and_import("code", _REDIRECT, member=member).outcome is LinkOutcome.LINKED
        assert join.called

    @respx.mock
    def it_puts_someone_never_on_the_server_onto_it(sync_config):
        _verified_member("jo@example.com")
        join = _mock_oauth("jo@example.com")
        _mock_reactions({})
        _mock_roles()
        assert discord_sync.link_and_import("code", _REDIRECT).outcome is LinkOutcome.LINKED
        assert join.calls.last.request.read() == b'{"access_token":"tok"}'

    @respx.mock
    def it_does_not_email_on_an_empty_import(sync_config, django_capture_on_commit_callbacks):
        _verified_member("jo@example.com")
        _mock_oauth("jo@example.com")
        _mock_reactions({})
        _mock_roles()
        with django_capture_on_commit_callbacks(execute=True):
            result = discord_sync.link_and_import("code", _REDIRECT)
        assert result.outcome is LinkOutcome.LINKED
        assert mail.outbox == []

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


def describe_import_discord_guilds():
    @respx.mock
    def it_imports_the_guilds_and_sends_the_confirmation(sync_config, django_capture_on_commit_callbacks):
        from core.tasks import enqueue_on_commit

        member = MemberFactory(discord_user_id="42")
        guild = GuildFactory(name="Glassworks")
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        _mock_reactions({"🔥": ["42"]})
        _mock_roles()
        with django_capture_on_commit_callbacks(execute=True):
            enqueue_on_commit(discord_sync.import_discord_guilds, member.pk)
        assert GuildMembership.objects.filter(guild=guild, member=member, source="discord").exists()
        assert len(mail.outbox) == 1
        assert "Glassworks" in mail.outbox[0].body

    @respx.mock
    def it_sends_one_email_when_run_twice(sync_config, django_capture_on_commit_callbacks):
        from core.tasks import enqueue_on_commit

        member = MemberFactory(discord_user_id="42")
        member.link_discord("42")
        guild = GuildFactory()
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        _mock_reactions({"🔥": ["42"]})
        _mock_roles()
        with django_capture_on_commit_callbacks(execute=True):
            enqueue_on_commit(discord_sync.import_discord_guilds, member.pk)
            enqueue_on_commit(discord_sync.import_discord_guilds, member.pk)
        assert GuildMembership.objects.filter(guild=guild, member=member).count() == 1
        assert len(mail.outbox) == 1
