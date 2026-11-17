"""Reconnect Discord nudges (#1239): the record, the command, and the one bell and email.

The server's member list is mocked with ``respx``; everything else runs against real models.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import CommandError, call_command
from django.urls import reverse

from core.models import Notification, SiteConfiguration
from membership import discord_reconnect
from membership.models import DiscordReconnectNudge, Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

MEMBERS_URL = r"https://discord\.com/api/v10/guilds/srv1/members(\?.*)?$"


@pytest.fixture(autouse=True)
def _discord(settings):
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    config = SiteConfiguration.load()
    config.discord_server_id = "srv1"
    config.save()


def _linked(username: str, discord_user_id: str, *, status: str = Member.Status.ACTIVE) -> Member:
    """A member with an account and a linked Discord id."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    member = user.member
    member.full_legal_name = f"{username.title()} Quillmere"
    member.status = status
    member.discord_user_id = discord_user_id
    member.save()
    return member


def _server(*user_ids: str) -> respx.Route:
    """The server's member list: one page holding ``user_ids``."""
    page = [{"user": {"id": user_id, "username": f"user{user_id}"}, "roles": []} for user_id in user_ids]
    return respx.get(url__regex=MEMBERS_URL).mock(return_value=httpx.Response(200, json=page))


def describe_DiscordReconnectNudge():
    def it_names_the_member_and_status():
        nudge = DiscordReconnectNudge.objects.create(member=_linked("ada", "1"))
        assert str(nudge) == "Discord reconnect nudge for Ada Quillmere (Open)"

    def describe_dismiss_for():
        def it_closes_the_open_nudge_as_dismissed():
            member = _linked("ada", "1")
            DiscordReconnectNudge.objects.create(member=member)
            assert DiscordReconnectNudge.objects.dismiss_for(member) == 1
            nudge = DiscordReconnectNudge.objects.get(member=member)
            assert nudge.status == DiscordReconnectNudge.Status.DISMISSED
            assert nudge.closed_at is not None

        def it_leaves_a_closed_nudge_alone():
            member = _linked("ada", "1")
            DiscordReconnectNudge.objects.create(member=member, status=DiscordReconnectNudge.Status.DONE)
            assert DiscordReconnectNudge.objects.dismiss_for(member) == 0
            assert DiscordReconnectNudge.objects.get(member=member).status == DiscordReconnectNudge.Status.DONE

    def describe_close_for():
        def it_closes_the_open_nudge_as_done():
            member = _linked("ada", "1")
            DiscordReconnectNudge.objects.create(member=member)
            assert DiscordReconnectNudge.objects.close_for(member) == 1
            assert DiscordReconnectNudge.objects.get(member=member).status == DiscordReconnectNudge.Status.DONE
            assert not DiscordReconnectNudge.objects.open_for(member).exists()


def describe_server_member_ids():
    @respx.mock
    def it_reads_every_id_on_the_server():
        _server("1", "2")
        assert discord_reconnect.server_member_ids() == {"1", "2"}

    def it_refuses_a_blank_server_id():
        config = SiteConfiguration.load()
        config.discord_server_id = " "
        config.save()
        with pytest.raises(discord_reconnect.ServerListUnavailableError, match="server id is blank"):
            discord_reconnect.server_member_ids()

    def it_refuses_a_blank_bot_token(settings):
        # An empty list here would make every linked member look missing.
        settings.DISCORD_BOT_TOKEN = ""
        with pytest.raises(discord_reconnect.ServerListUnavailableError, match="bot token is blank"):
            discord_reconnect.server_member_ids()

    @respx.mock
    def it_refuses_a_list_it_could_not_read():
        respx.get(url__regex=MEMBERS_URL).mock(return_value=httpx.Response(403, json={}))
        with pytest.raises(discord_reconnect.ServerListUnavailableError, match="Could not read"):
            discord_reconnect.server_member_ids()


def describe_find_missing():
    def it_takes_only_active_linked_members_off_the_server():
        off = _linked("off", "10")
        _linked("on", "11")
        _linked("former", "12", status=Member.Status.FORMER)
        _linked("guest", "13", status=Member.Status.GUEST)
        _linked("suspended", "14", status=Member.Status.SUSPENDED)
        MemberFactory(discord_user_id="")
        run = discord_reconnect.find_missing({"11"})
        assert run.missing == [off]
        assert run.already_nudged == []

    def it_sets_aside_everyone_nudged_before_whatever_the_status():
        fresh = _linked("fresh", "10")
        statuses = [DiscordReconnectNudge.Status.OPEN, DiscordReconnectNudge.Status.DISMISSED]
        earlier = [_linked(f"earlier{n}", f"2{n}") for n in range(3)]
        for member, status in zip(earlier, [*statuses, DiscordReconnectNudge.Status.DONE], strict=True):
            DiscordReconnectNudge.objects.create(member=member, status=status)
        run = discord_reconnect.find_missing(set())
        assert run.missing == [fresh]
        assert run.already_nudged == earlier


def describe_nudge_discord_reconnect():
    @respx.mock
    def it_lists_by_default_and_writes_nothing(capsys, django_capture_on_commit_callbacks):
        off = _linked("off", "10")
        _linked("on", "11")
        _server("11", "99")
        with django_capture_on_commit_callbacks(execute=True):
            call_command("nudge_discord_reconnect")
        out = capsys.readouterr().out
        assert f"Member {off.pk} Off Quillmere (10): would nudge" in out
        assert "1 would nudge, 0 skipped, 2 on the server." in out
        assert not DiscordReconnectNudge.objects.exists()
        assert mail.outbox == []
        assert not Notification.objects.exists()

    @respx.mock
    def it_nudges_each_missing_member_once_with_send(capsys, django_capture_on_commit_callbacks):
        first = _linked("first", "10")
        second = _linked("second", "12")
        _server("11")
        with django_capture_on_commit_callbacks(execute=True):
            call_command("nudge_discord_reconnect", "--send")
        assert "2 nudged, 0 skipped, 1 on the server." in capsys.readouterr().out
        assert set(DiscordReconnectNudge.objects.open().values_list("member", flat=True)) == {first.pk, second.pk}
        assert sorted(message.to[0] for message in mail.outbox) == ["first@example.com", "second@example.com"]
        assert Notification.objects.filter(trigger="membership.discord_reconnect").count() == 2

    @respx.mock
    def it_nudges_nobody_twice(capsys, django_capture_on_commit_callbacks):
        member = _linked("ada", "10")
        _server()
        with django_capture_on_commit_callbacks(execute=True):
            call_command("nudge_discord_reconnect", "--send")
        DiscordReconnectNudge.objects.dismiss_for(member)
        capsys.readouterr()
        with django_capture_on_commit_callbacks(execute=True):
            call_command("nudge_discord_reconnect", "--send")
        out = capsys.readouterr().out
        assert f"Member {member.pk} Ada Quillmere (10): skipped, already nudged (dismissed)" in out
        assert "0 nudged, 1 skipped, 0 on the server." in out
        assert DiscordReconnectNudge.objects.filter(member=member).count() == 1
        assert len(mail.outbox) == 1

    @respx.mock
    def it_stops_and_nudges_nobody_when_the_list_cannot_be_read(django_capture_on_commit_callbacks):
        _linked("ada", "10")
        respx.get(url__regex=MEMBERS_URL).mock(return_value=httpx.Response(500, json={}))
        with django_capture_on_commit_callbacks(execute=True), pytest.raises(CommandError, match="Nobody was nudged"):
            call_command("nudge_discord_reconnect", "--send")
        assert not DiscordReconnectNudge.objects.exists()
        assert mail.outbox == []


def describe_notify_nudged():
    def it_sends_one_bell_and_one_email_that_open_the_connect_flow(settings):
        settings.MEMBER_BASE_URL = "https://members.example.test"
        member = _linked("ada", "10")
        nudge = DiscordReconnectNudge.objects.create(member=member)
        discord_reconnect.notify_nudged.call(nudge.pk)
        discord_reconnect.notify_nudged.call(nudge.pk)
        bell = Notification.objects.get(user=member.user, trigger="membership.discord_reconnect")
        assert bell.url == reverse("hub_discord_connect")
        assert bell.title == "Reconnect Discord to join the Past Lives server"
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == ["ada@example.com"]
        assert message.subject == "Reconnect Discord to join the Past Lives server"
        connect = "https://members.example.test" + reverse("hub_discord_connect")
        assert connect in message.body
        html = message.alternatives[0][0]
        assert f'href="{connect}"' in html
        assert ">Reconnect Discord</a>" in html

    def it_sends_nothing_for_a_nudge_already_closed():
        member = _linked("ada", "10")
        nudge = DiscordReconnectNudge.objects.create(member=member, status=DiscordReconnectNudge.Status.DONE)
        discord_reconnect.notify_nudged.call(nudge.pk)
        assert mail.outbox == []
        assert not Notification.objects.exists()
