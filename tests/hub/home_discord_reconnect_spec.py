"""Home's Needs Attention shows a Reconnect Discord item to a nudged member, and Dismiss closes it (#1239)."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import AnnouncementDraft, DiscordReconnectNudge, Member
from polls.models import QuestionKind, QuestionSpec
from tests.membership.factories import (
    AnnouncementDraftFactory,
    GuildFactory,
    GuildMembershipFactory,
    MembershipPlanFactory,
)
from tests.polls.factories import sent_email_poll

pytestmark = pytest.mark.django_db

HOME = reverse("hub_home")
DISMISS = reverse("hub_discord_reconnect_dismiss")


def _signed_in(client: Client, username: str = "ada") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    member = user.member
    member.discord_user_id = "10"
    member.save()
    client.force_login(user)
    return member


def _block(html: str) -> str:
    """The Needs Attention card's markup."""
    return html.split('data-block="needs-attention"')[1].split("</section>")[0]


def _group(html: str) -> str:
    return _block(html).split("data-needs-attention-discord")[1].split("</ul>")[0]


def describe_the_reconnect_discord_group():
    def it_shows_a_nudged_member_why_and_both_buttons(client: Client):
        member = _signed_in(client)
        DiscordReconnectNudge.objects.create(member=member)
        group = _group(client.get(HOME).content.decode())
        assert 'data-guidance-target="home.discord-reconnect"' in group
        assert "You're not on the Past Lives Discord yet" in group
        assert f'href="{reverse("hub_discord_connect")}"' in group
        assert 'hx-boost="false" data-discord-reconnect-home>Reconnect Discord</a>' in group
        assert f'hx-post="{DISMISS}"' in group
        # The only group in the card: Dismiss takes the whole card with it.
        assert 'hx-target="closest section"' in group

    def it_dismisses_only_its_own_group_beside_another(client: Client):
        member = _signed_in(client)
        DiscordReconnectNudge.objects.create(member=member)
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=member)
        draft = AnnouncementDraftFactory(
            kind=AnnouncementDraft.Kind.POLL, audience=AnnouncementDraft.Audience.GUILD, guild=guild
        )
        sent_email_poll(QuestionSpec("Any zorblax ideas?", QuestionKind.OPEN), now=timezone.now(), draft=draft)
        group = _group(client.get(HOME).content.decode())
        assert 'hx-target="closest .pl-home-group"' in group

    @pytest.mark.parametrize("status", [DiscordReconnectNudge.Status.DISMISSED, DiscordReconnectNudge.Status.DONE])
    def it_is_gone_once_the_nudge_closes(client: Client, status: str):
        member = _signed_in(client)
        DiscordReconnectNudge.objects.create(member=member, status=status)
        response = client.get(HOME)
        assert response.context["discord_reconnect"] is False
        assert b"data-needs-attention-discord" not in response.content

    def it_is_absent_for_a_member_never_nudged(client: Client):
        _signed_in(client)
        response = client.get(HOME)
        assert response.context["discord_reconnect"] is False
        assert b'data-block="needs-attention"' not in response.content


def describe_discord_reconnect_dismiss():
    def it_closes_the_nudge_for_good_with_an_empty_200_and_a_toast(client: Client):
        member = _signed_in(client)
        DiscordReconnectNudge.objects.create(member=member)
        response = client.post(DISMISS)
        # Empty 200, not 204, so the item's outerHTML swap removes it.
        assert response.status_code == 200
        assert response.content == b""
        toast = json.loads(response["HX-Trigger"])["showToast"]
        assert toast == {
            "message": "Got it. You can reconnect Discord any time in Settings, under Notifications.",
            "type": "info",
        }
        nudge = DiscordReconnectNudge.objects.get(member=member)
        assert nudge.status == DiscordReconnectNudge.Status.DISMISSED
        assert b"data-needs-attention-discord" not in client.get(HOME).content

    def it_rejects_get(client: Client):
        _signed_in(client)
        assert client.get(DISMISS).status_code == 405

    def it_requires_login(client: Client):
        response = client.post(DISMISS)
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_is_graceful_for_an_account_with_no_member(client: Client):
        user = User.objects.create_user(username="orphan", password="pass")
        Member.objects.filter(user=user).delete()
        client.force_login(user)
        response = client.post(DISMISS)
        assert response.status_code == 200
        assert response.content == b""
