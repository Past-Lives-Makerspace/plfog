"""BDD specs for #596: a meeting's calendar event brings a draft announcement, and the composer
offers admins a Guild leads audience.

Covers the draft in the Announcements Drafts tab and the composer's Guild leads choice, gate and
inline send. The meeting page that scheduled meetings is gone (#1171); its specs went with it.
"""

from __future__ import annotations

from datetime import time
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub.forms import discord_channel_choices, split_audience
from membership.models import AnnouncementDraft, CommunityEvent, GuildAnnouncement, Member
from tests.membership.factories import GuildFactory, MeetingFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(
        username=username, email=f"{username}@example.com", password="pass", last_login=timezone.now()
    )
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _admin(client: Client) -> User:
    return _login(client, "admin596", fog_role=Member.FogRole.ADMIN)


def _lead(client: Client, guild) -> User:
    user = _login(client, f"lead596-{guild.pk}")
    guild.guild_lead = user.member
    guild.save(update_fields=["guild_lead"])
    return user


def describe_a_meeting_calendar_event():
    def it_lists_the_draft_in_the_announcements_drafts_tab(client: Client):
        by = _admin(client)
        meeting = MeetingFactory(guild=None, scheduled_time=time(18, 0))
        with patch.object(CommunityEvent, "push_live"):
            meeting.create_calendar_event(by=by)
        draft = AnnouncementDraft.objects.get()
        content = client.get(reverse("hub_announcements")).content.decode()
        assert reverse("hub_compose_resume", args=[draft.pk]) in content
        assert '<td data-label="Audience">Guild leads</td>' in content


def describe_the_guild_leads_audience():
    def it_splits_the_leads_value():
        assert split_audience("leads") == ("leads", None, None)

    def it_offers_only_the_shared_discord_channels():
        values = [value for value, _label in discord_channel_choices("leads")]
        assert GuildAnnouncement.DiscordChannel.GUILD not in values
        assert GuildAnnouncement.DiscordChannel.NONE in values

    def it_is_offered_to_an_admin(client: Client):
        _admin(client)
        assert '<option value="leads"' in client.get(reverse("hub_compose")).content.decode()

    def it_is_not_offered_to_a_guild_lead(client: Client):
        _lead(client, GuildFactory())
        assert '<option value="leads"' not in client.get(reverse("hub_compose")).content.decode()

    def it_refuses_a_guild_lead_who_posts_it(client: Client):
        _lead(client, GuildFactory())
        resp = client.post(
            reverse("hub_compose_send"),
            {"audience": "leads", "body": "<p>Hi</p>", "discord_channel": "none", "mention": "none", "draft_pk": ""},
        )
        assert resp.status_code == 403
        assert AnnouncementDraft.objects.count() == 0

    def it_sends_inline_for_an_admin(client: Client):
        _admin(client)
        client.post(
            reverse("hub_compose_send"),
            {
                "audience": "leads",
                "body": "<p>Council Tuesday.</p>",
                "send_email": "on",
                "discord_channel": "none",
                "mention": "none",
                "draft_pk": "",
            },
        )
        draft = AnnouncementDraft.objects.get()
        assert draft.audience == AnnouncementDraft.Audience.LEADS
        assert draft.sent_at is not None
        assert draft.send_requested_at is None
