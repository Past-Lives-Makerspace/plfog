"""An announcement posted on Discord as its sender (#730): the webhook carries their Discord name and picture.

The Discord user lookup and the webhook post are both real ``httpx`` calls mocked with ``respx``, so
each spec reads the JSON the webhook actually receives. The sender is the draft's author, also when
the queued send job sends it later, with no request in sight.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command

from core.models import SiteConfiguration
from membership.models import AnnouncementDraft, GuildAnnouncement
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

_SITE = AnnouncementDraft.Audience.SITE
_GUILD = AnnouncementDraft.Audience.GUILD
_LEADS = AnnouncementDraft.Audience.LEADS
_CHANNEL = GuildAnnouncement.DiscordChannel
_GENERAL_HOOK = "https://discord.com/api/webhooks/1/general"
_LEADERSHIP_HOOK = "https://discord.com/api/webhooks/2/leadership"
_GUILD_HOOK = "https://discord.com/api/webhooks/3/guild"
_DISCORD_ID = "777000"
_USER_URL = f"https://discord.com/api/v10/users/{_DISCORD_ID}"
_AVATAR = f"https://cdn.discordapp.com/avatars/{_DISCORD_ID}/hash1.png"


@pytest.fixture(autouse=True)
def _discord_configured(settings) -> Iterator[None]:
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    settings.IS_STAGING = False
    config = SiteConfiguration.load()
    config.discord_general_webhook_url = _GENERAL_HOOK
    config.discord_leadership_webhook_url = _LEADERSHIP_HOOK
    config.save()
    cache.clear()
    yield
    cache.clear()


def _sender(username: str = "lead", *, discord_user_id: str = _DISCORD_ID) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="pw")
    user.member.discord_user_id = discord_user_id
    user.member.save(update_fields=["discord_user_id"])
    return user


def _draft(author: User, **fields) -> AnnouncementDraft:
    values = {
        "author": author,
        "audience": _SITE,
        "title": "T",
        "body": "<p>Kiln is open.</p>",
        "discord_channel": _CHANNEL.GENERAL,
        "discord_post_as_me": True,
    }
    values.update(fields)
    return AnnouncementDraft.objects.create(**values)


def _lookup(**user_json) -> respx.Route:
    body = {"id": _DISCORD_ID, "global_name": "Felix", "username": "covo", "avatar": "hash1"}
    body.update(user_json)
    return respx.get(_USER_URL).mock(return_value=httpx.Response(200, json=body))


def _posted(route: respx.Route) -> dict:
    assert route.call_count == 1
    return json.loads(route.calls.last.request.content)


def describe_post_on_discord_as_me():
    @respx.mock
    def it_posts_a_site_announcement_under_the_senders_discord_name_and_picture():
        lookup = _lookup()
        hook = respx.post(_GENERAL_HOOK).mock(return_value=httpx.Response(204))
        _draft(_sender()).send()
        payload = _posted(hook)
        assert payload["username"] == "Felix"
        assert payload["avatar_url"] == _AVATAR
        assert payload["embeds"][0]["description"] == "Kiln is open."
        assert lookup.call_count == 1

    @respx.mock
    def it_posts_a_leads_announcement_under_the_senders_name():
        _lookup()
        hook = respx.post(_LEADERSHIP_HOOK).mock(return_value=httpx.Response(204))
        _draft(_sender(), audience=_LEADS, discord_channel=_CHANNEL.LEADERSHIP).send()
        assert _posted(hook)["username"] == "Felix"

    @respx.mock
    def it_posts_a_guild_announcement_under_the_senders_name():
        _lookup()
        hook = respx.post(_GUILD_HOOK).mock(return_value=httpx.Response(204))
        guild = GuildFactory(discord_webhook_url=_GUILD_HOOK, discord_post_enabled=True)
        _draft(_sender(), audience=_GUILD, guild=guild, discord_channel=_CHANNEL.GUILD).send()
        payload = _posted(hook)
        assert payload["username"] == "Felix"
        assert payload["avatar_url"] == _AVATAR

    @respx.mock
    def it_leaves_the_picture_out_when_the_sender_has_the_default_avatar():
        _lookup(avatar=None)
        hook = respx.post(_GENERAL_HOOK).mock(return_value=httpx.Response(204))
        _draft(_sender()).send()
        payload = _posted(hook)
        assert payload["username"] == "Felix"
        assert "avatar_url" not in payload

    def it_posts_unchanged_when_the_switch_is_off():
        with respx.mock(assert_all_called=False) as router:
            lookup = router.get(_USER_URL)
            hook = router.post(_GENERAL_HOOK).mock(return_value=httpx.Response(204))
            _draft(_sender(), discord_post_as_me=False).send()
            payload = _posted(hook)
            assert "username" not in payload
            assert "avatar_url" not in payload
            assert not lookup.called

    @respx.mock
    def it_posts_under_the_default_name_and_logs_when_the_lookup_fails(caplog):
        respx.get(_USER_URL).mock(return_value=httpx.Response(500, text="boom"))
        hook = respx.post(_GENERAL_HOOK).mock(return_value=httpx.Response(204))
        draft = _draft(_sender())
        with caplog.at_level(logging.WARNING, logger="core.events.discord_identity"):
            draft.send()
        payload = _posted(hook)
        assert "username" not in payload
        assert "avatar_url" not in payload
        assert "sender name lookup failed" in caplog.text
        draft.refresh_from_db()
        assert draft.sent_at is not None

    @respx.mock
    def it_posts_under_the_default_name_when_discord_would_refuse_the_name():
        _lookup(global_name="Discord Dan")
        hook = respx.post(_GENERAL_HOOK).mock(return_value=httpx.Response(204))
        _draft(_sender()).send()
        payload = _posted(hook)
        assert "username" not in payload
        assert "avatar_url" not in payload

    def it_asks_nothing_when_the_sender_has_no_linked_discord():
        with respx.mock(assert_all_called=False) as router:
            lookup = router.get(_USER_URL)
            hook = router.post(_GENERAL_HOOK).mock(return_value=httpx.Response(204))
            _draft(_sender(discord_user_id="")).send()
            assert "username" not in _posted(hook)
            assert not lookup.called

    def it_asks_nothing_when_no_discord_channel_is_chosen():
        with respx.mock(assert_all_called=False) as router:
            lookup = router.get(_USER_URL)
            _draft(_sender(), discord_channel=_CHANNEL.NONE).send()
            assert not lookup.called

    def it_asks_nothing_when_discord_is_switched_off():
        with respx.mock(assert_all_called=False) as router:
            lookup = router.get(_USER_URL)
            _draft(_sender(), discord_enabled=False).send()
            assert not lookup.called

    @respx.mock
    def it_sends_a_queued_announcement_as_the_sender_who_queued_it():
        # The job runs with no request: the name comes from the draft's author, not from whoever runs it.
        lookup = _lookup(global_name="Queued Sender")
        hook = respx.post(_GENERAL_HOOK).mock(return_value=httpx.Response(204))
        _sender("bystander", discord_user_id="999")
        draft = _draft(_sender())
        draft.queue_send()
        call_command("send_queued_announcements")
        assert _posted(hook)["username"] == "Queued Sender"
        assert lookup.calls.last.request.url == _USER_URL
        draft.refresh_from_db()
        assert draft.sent_at is not None


def describe_discord_sender_identity():
    def it_is_none_without_an_author():
        draft = AnnouncementDraft(author=None, discord_post_as_me=True)
        assert draft.discord_sender_identity() is None

    @respx.mock
    def it_uses_the_remembered_lookup_when_cached():
        lookup = _lookup()
        draft = _draft(_sender())
        assert draft.discord_sender_identity(cached=True) == draft.discord_sender_identity(cached=True)
        assert lookup.call_count == 1
