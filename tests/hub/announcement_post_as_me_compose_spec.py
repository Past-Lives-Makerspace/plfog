"""The composer's "Post on Discord as me" switch (#730): who sees it, what it saves, what the preview shows.

The switch is offered only to a sender whose account has a verified Discord link. The Discord
Preview card shows the sender's Discord name and picture while it is on, through a lookup kept
for a few minutes, so refreshing the preview is not a Discord call each time.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import AnnouncementDraft
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

_DISCORD_ID = "555123"
_USER_URL = f"https://discord.com/api/v10/users/{_DISCORD_ID}"
_AVATAR = f"https://cdn.discordapp.com/avatars/{_DISCORD_ID}/face9.png"
_GUILD_HOOK = "https://discord.com/api/webhooks/9/guild"


@pytest.fixture(autouse=True)
def _bot_token_and_clean_cache(settings) -> Iterator[None]:
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    settings.IS_STAGING = False
    cache.clear()
    yield
    cache.clear()


def _login_lead(client: Client, guild, *, discord_user_id: str = _DISCORD_ID, username: str = "lead") -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    member = user.member
    member.discord_user_id = discord_user_id
    member.save(update_fields=["discord_user_id"])
    guild.guild_lead = member
    guild.save(update_fields=["guild_lead"])
    client.login(username=username, password="p")
    return user


def _guild():
    return GuildFactory(discord_webhook_url=_GUILD_HOOK, discord_post_enabled=True)


def _data(guild, **overrides) -> dict:
    data = {
        "audience": f"guild:{guild.pk}",
        "body": "<p>Glaze day Saturday.</p>",
        "discord_enabled": "on",
        "discord_channel": "guild",
        "mention": "none",
        "draft_pk": "",
    }
    data.update(overrides)
    return data


def _toggle_input(content: str) -> str:
    match = re.search(r'<input[^>]*name="discord_post_as_me"[^>]*>', content)
    assert match is not None
    return match.group(0)


def _mock_lookup(router: respx.MockRouter) -> respx.Route:
    return router.get(_USER_URL).mock(
        return_value=httpx.Response(200, json={"id": _DISCORD_ID, "global_name": "Felix Plaza", "avatar": "face9"})
    )


def describe_the_switch():
    def it_shows_for_a_sender_with_a_linked_discord(client: Client):
        guild = _guild()
        _login_lead(client, guild)
        content = client.get(reverse("hub_compose"), {"audience": f"guild:{guild.pk}"}).content.decode()
        assert "data-compose-post-as-me" in content
        toggle = _toggle_input(content)
        assert "checked" not in toggle  # off by default
        assert "refreshPreview" in toggle  # the preview card follows the switch

    def it_shows_only_while_a_discord_channel_is_chosen(client: Client):
        guild = _guild()
        _login_lead(client, guild)
        content = client.get(reverse("hub_compose"), {"audience": f"guild:{guild.pk}"}).content.decode()
        assert "<div x-show=\"discordChannel !== 'none'\" x-cloak data-compose-post-as-me>" in content

    def it_is_hidden_from_a_sender_without_a_linked_discord(client: Client):
        guild = _guild()
        _login_lead(client, guild, discord_user_id="")
        content = client.get(reverse("hub_compose"), {"audience": f"guild:{guild.pk}"}).content.decode()
        assert "data-compose-post-as-me" not in content
        assert 'name="discord_post_as_me"' not in content


def describe_saving_the_switch():
    def it_saves_the_switch_for_a_linked_sender(client: Client):
        guild = _guild()
        lead = _login_lead(client, guild)
        response = client.post(reverse("hub_compose_save_draft"), _data(guild, discord_post_as_me="on"))
        assert response.status_code == 200
        assert AnnouncementDraft.objects.get(author=lead).discord_post_as_me is True

    def it_saves_it_off_for_a_sender_without_a_link_even_when_posted(client: Client):
        guild = _guild()
        lead = _login_lead(client, guild, discord_user_id="")
        client.post(reverse("hub_compose_save_draft"), _data(guild, discord_post_as_me="on"))
        assert AnnouncementDraft.objects.get(author=lead).discord_post_as_me is False

    def it_resumes_a_draft_with_the_switch_on(client: Client):
        guild = _guild()
        lead = _login_lead(client, guild)
        draft = AnnouncementDraft.objects.create(
            author=lead,
            audience=AnnouncementDraft.Audience.GUILD,
            guild=guild,
            title="T",
            body="<p>x</p>",
            discord_channel="guild",
            discord_post_as_me=True,
        )
        content = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert "checked" in _toggle_input(content)


def describe_the_discord_preview():
    def it_shows_the_senders_discord_name_and_picture_while_the_switch_is_on(client: Client):
        guild = _guild()
        _login_lead(client, guild)
        with respx.mock as router:
            lookup = _mock_lookup(router)
            first = client.post(reverse("hub_compose_preview"), _data(guild, discord_post_as_me="on"))
            second = client.post(reverse("hub_compose_preview"), _data(guild, discord_post_as_me="on"))
        content = first.content.decode()
        assert '<div class="pl-push-preview__app" data-discord-preview-sender>Felix Plaza</div>' in content
        assert f'src="{_AVATAR}"' in content
        assert second.content == first.content
        assert lookup.call_count == 1  # a refresh reads the remembered lookup, not Discord

    def it_shows_the_app_name_and_icon_while_the_switch_is_off(client: Client):
        guild = _guild()
        _login_lead(client, guild)
        with respx.mock(assert_all_called=False) as router:
            lookup = _mock_lookup(router)
            content = client.post(reverse("hub_compose_preview"), _data(guild)).content.decode()
            assert not lookup.called
        assert '<div class="pl-push-preview__app" data-discord-preview-sender>Past Lives Makerspace</div>' in content
        assert "data-discord-preview-avatar" not in content

    def it_shows_the_app_name_when_the_lookup_fails(client: Client):
        guild = _guild()
        _login_lead(client, guild)
        with respx.mock as router:
            router.get(_USER_URL).mock(return_value=httpx.Response(503))
            content = client.post(
                reverse("hub_compose_preview"), _data(guild, discord_post_as_me="on")
            ).content.decode()
        assert '<div class="pl-push-preview__app" data-discord-preview-sender>Past Lives Makerspace</div>' in content
        assert "data-discord-preview-avatar" not in content

    def it_shows_the_sender_on_the_sent_record(client: Client):
        guild = _guild()
        lead = _login_lead(client, guild)
        draft = AnnouncementDraft.objects.create(
            author=lead,
            audience=AnnouncementDraft.Audience.GUILD,
            guild=guild,
            title="T",
            body="<p>x</p>",
            discord_channel="guild",
            discord_post_as_me=True,
            sent_at=timezone.now(),
        )
        with respx.mock as router:
            _mock_lookup(router)
            content = client.get(reverse("hub_announcement_sent", args=[draft.pk])).content.decode()
        assert "data-discord-preview-sender>Felix Plaza</div>" in content
