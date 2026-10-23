"""The one-time cleanup list command (#898). Discord HTTP is mocked with ``respx``."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from django.core.management import CommandError, call_command

from core.models import AutomationAction, ScheduledTaskRun, SiteConfiguration
from core.scheduled_jobs import Trigger
from membership.models import DISCORD_REMOVAL_TASK_KEY, DiscordRemoval

pytestmark = pytest.mark.django_db

LIST = "https://discord.com/api/v10/guilds/srv1/members"
DM_OPEN = "https://discord.com/api/v10/users/@me/channels"
DM_POST = "https://discord.com/api/v10/channels/dm1/messages"


@pytest.fixture(autouse=True)
def _discord(settings):
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    config = SiteConfiguration.load()
    config.discord_server_id = "srv1"
    config.save()


def _server(*names: str) -> None:
    members = [{"user": {"id": str(100 + i), "username": name}} for i, name in enumerate(names)]
    respx.get(LIST).mock(return_value=httpx.Response(200, json=members))


def describe_discord_cleanup_list():
    def it_refuses_on_staging(settings):
        settings.IS_STAGING = True
        with pytest.raises(CommandError, match="nothing was sent"):
            call_command("discord_cleanup_list", "ghost")

    def it_refuses_without_a_server_id():
        config = SiteConfiguration.load()
        config.discord_server_id = ""
        config.save()
        with pytest.raises(CommandError, match="No Discord server id"):
            call_command("discord_cleanup_list", "ghost")

    @respx.mock
    def it_only_reports_on_a_dry_run(capsys):
        _server("Ghost")
        call_command("discord_cleanup_list", "@Ghost", "ghost", "nobody", "--dry-run")
        out = capsys.readouterr().out
        assert out.splitlines() == [
            "ghost: would be warned and queued",
            "nobody: nobody on the server has this username",
        ]
        assert not DiscordRemoval.objects.exists()
        assert not ScheduledTaskRun.objects.exists()

    @respx.mock
    def it_dms_with_felixs_mention_and_records_the_run(capsys):
        _server("Ghost")
        respx.post(DM_OPEN).mock(return_value=httpx.Response(200, json={"id": "dm1"}))
        post = respx.post(DM_POST).mock(return_value=httpx.Response(200, json={}))
        call_command("discord_cleanup_list", "ghost")
        body = json.loads(post.calls.last.request.read())
        assert "<@150063478210691081>" in body["content"]
        assert body["allowed_mentions"] == {"parse": [], "users": ["150063478210691081"]}
        run = ScheduledTaskRun.objects.get()
        assert (run.task_key, run.trigger, run.status) == (DISCORD_REMOVAL_TASK_KEY, Trigger.COMMAND, "ok")
        assert AutomationAction.objects.filter(run=run, kind="discord_dm").count() == 1
        assert capsys.readouterr().out.startswith("ghost: warned, removal due ")

    @respx.mock
    def it_fails_the_run_when_a_dm_fails(capsys):
        _server("ghost")
        respx.post(DM_OPEN).mock(return_value=httpx.Response(503))
        with pytest.raises(CommandError, match="1 DM"):
            call_command("discord_cleanup_list", "ghost")
        assert ScheduledTaskRun.objects.get().status == ScheduledTaskRun.Status.FAILED
        assert "ghost: DM failed" in capsys.readouterr().out
