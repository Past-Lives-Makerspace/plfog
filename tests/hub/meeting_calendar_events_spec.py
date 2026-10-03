"""BDD specs for #596 through the views: scheduling a meeting puts it on the calendar with a
draft announcement, and the composer offers admins a Guild leads audience.

Covers the meeting create and autosave triggers (and what they leave alone), the draft in the
Announcements Drafts tab, and the composer's Guild leads choice, gate and inline send.
"""

from __future__ import annotations

from datetime import time, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub.forms import discord_channel_choices, split_audience
from membership.models import AnnouncementDraft, CommunityEvent, GuildAnnouncement, Meeting, Member
from tests.membership.factories import CommunityEventFactory, GuildFactory, MeetingFactory, MembershipPlanFactory

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


def _autosave(client: Client, meeting: Meeting, field: str, value: str):
    return client.post(reverse("hub_meeting_save", args=[meeting.pk]), {"field": field, "value": value})


def describe_scheduling_a_meeting():
    def describe_by_autosave():
        def it_gives_a_guild_meeting_its_event_and_a_guild_draft_when_the_time_lands(client: Client):
            guild = GuildFactory()
            by = _lead(client, guild)
            meeting = MeetingFactory(guild=guild, scheduled_time=None)
            with (
                patch.object(CommunityEvent, "push_live") as push,
                patch.object(CommunityEvent, "announce") as announce,
            ):
                resp = _autosave(client, meeting, "scheduled_time", "18:00")
            assert resp.status_code == 204
            push.assert_called_once_with(actor=by)
            announce.assert_not_called()
            meeting.refresh_from_db()
            assert meeting.owns_event is True
            assert meeting.event.event_type == CommunityEvent.EventType.GUILD_MEETING
            assert meeting.event.google_calendar_target == CommunityEvent.GoogleCalendarTarget.PUBLIC
            draft = AnnouncementDraft.objects.resumable().get()
            assert (draft.audience, draft.guild) == (AnnouncementDraft.Audience.GUILD, guild)

        def it_gives_a_council_meeting_a_lead_meeting_and_a_leads_draft_when_the_date_lands(client: Client):
            _admin(client)
            meeting = MeetingFactory(guild=None, scheduled_date=None, scheduled_time=time(18, 30))
            day = timezone.localdate() + timedelta(days=3)
            with patch.object(CommunityEvent, "push_live"):
                resp = _autosave(client, meeting, "scheduled_date", day.isoformat())
            assert resp.status_code == 204
            meeting.refresh_from_db()
            assert meeting.event.event_type == CommunityEvent.EventType.LEAD_MEETING
            assert meeting.event.google_calendar_target == CommunityEvent.GoogleCalendarTarget.MEMBER
            assert AnnouncementDraft.objects.resumable().get().audience == AnnouncementDraft.Audience.LEADS

        def it_waits_out_a_year_being_typed_then_sets_up_one_event_and_draft(client: Client):
            _admin(client)
            meeting = MeetingFactory(guild=None, scheduled_date=None, scheduled_time=time(18, 30))
            target = timezone.localdate() + timedelta(days=5)
            with patch.object(CommunityEvent, "push_live"):
                for year in ("0002", "0020", "0202"):
                    resp = _autosave(client, meeting, "scheduled_date", f"{year}-{target:%m-%d}")
                    assert resp.status_code == 204
                    assert CommunityEvent.objects.count() == 0
                    assert AnnouncementDraft.objects.count() == 0
                _autosave(client, meeting, "scheduled_date", target.isoformat())
            meeting.refresh_from_db()
            event = CommunityEvent.objects.get()
            assert meeting.event == event
            assert timezone.localtime(event.starts_at).date() == target
            draft = AnnouncementDraft.objects.get()
            assert event.when_display in draft.body

        def it_sets_nothing_up_for_a_past_date_then_does_once_it_moves_to_the_future(client: Client):
            guild = GuildFactory()
            _lead(client, guild)
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))
            past = timezone.localdate() - timedelta(days=2)
            future = timezone.localdate() + timedelta(days=8)
            with patch.object(CommunityEvent, "push_live"):
                _autosave(client, meeting, "scheduled_date", past.isoformat())
                assert CommunityEvent.objects.count() == 0
                _autosave(client, meeting, "scheduled_date", future.isoformat())
            assert timezone.localtime(CommunityEvent.objects.get().starts_at).date() == future
            assert AnnouncementDraft.objects.count() == 1

        def it_does_nothing_while_the_time_is_still_missing(client: Client):
            guild = GuildFactory()
            _lead(client, guild)
            meeting = MeetingFactory(guild=guild, scheduled_date=None, scheduled_time=None)
            _autosave(client, meeting, "scheduled_date", (timezone.localdate() + timedelta(days=3)).isoformat())
            assert CommunityEvent.objects.count() == 0
            assert AnnouncementDraft.objects.count() == 0

        def it_moves_an_owned_event_on_a_time_edit_without_a_second_event_or_draft(client: Client):
            guild = GuildFactory()
            by = _lead(client, guild)
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))
            with patch.object(CommunityEvent, "push_live"):
                event = meeting.create_calendar_event(by=by)
            with (
                patch.object(CommunityEvent, "push_to_google"),
                patch.object(CommunityEvent, "push_to_discord"),
                patch.object(CommunityEvent, "announce") as announce,
            ):
                _autosave(client, meeting, "scheduled_time", "19:30")
            announce.assert_not_called()
            event.refresh_from_db()
            assert timezone.localtime(event.starts_at).time() == time(19, 30)
            assert CommunityEvent.objects.count() == 1
            assert AnnouncementDraft.objects.count() == 1

        def it_leaves_a_merely_linked_event_alone(client: Client):
            guild = GuildFactory()
            by = _lead(client, guild)
            event = CommunityEventFactory(guild=guild, title="Their event")
            meeting = MeetingFactory(
                guild=guild, scheduled_date=timezone.localdate(event.starts_at), scheduled_time=None
            )
            meeting.link_event(event, timezone.localdate(event.starts_at), by=by)
            with patch.object(CommunityEvent, "push_live") as push:
                _autosave(client, meeting, "scheduled_time", "18:00")
            push.assert_not_called()
            event.refresh_from_db()
            assert event.title == "Their event"
            assert CommunityEvent.objects.count() == 1
            assert AnnouncementDraft.objects.count() == 0

        def it_does_not_respawn_an_event_for_an_unlinked_meeting_on_a_reschedule(client: Client):
            guild = GuildFactory()
            _lead(client, guild)
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))  # already scheduled, unlinked
            _autosave(client, meeting, "scheduled_date", (timezone.localdate() + timedelta(days=9)).isoformat())
            assert CommunityEvent.objects.count() == 0

    def describe_by_creation():
        def it_puts_a_council_meeting_created_with_a_date_and_time_on_the_calendar(client: Client):
            _admin(client)
            day = timezone.localdate() + timedelta(days=4)
            with patch.object(CommunityEvent, "push_live"):
                resp = client.post(
                    reverse("hub_meeting_create"),
                    {"scope": "council", "kind": "monthly", "date": day.isoformat(), "time": "18:30"},
                )
            meeting = Meeting.objects.get()
            assert resp.status_code == 302
            assert meeting.owns_event is True
            assert meeting.event.event_type == CommunityEvent.EventType.LEAD_MEETING
            assert AnnouncementDraft.objects.resumable().get().audience == AnnouncementDraft.Audience.LEADS

        def it_creates_no_event_for_a_meeting_created_with_a_past_date(client: Client):
            _admin(client)
            day = timezone.localdate() - timedelta(days=1)
            client.post(
                reverse("hub_meeting_create"),
                {"scope": "council", "kind": "monthly", "date": day.isoformat(), "time": "18:30"},
            )
            assert Meeting.objects.count() == 1
            assert CommunityEvent.objects.count() == 0
            assert AnnouncementDraft.objects.count() == 0

        def it_creates_no_event_for_a_meeting_created_without_a_time(client: Client):
            _admin(client)
            day = timezone.localdate() + timedelta(days=4)
            client.post(reverse("hub_meeting_create"), {"scope": "council", "kind": "monthly", "date": day.isoformat()})
            assert CommunityEvent.objects.count() == 0

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
