"""BDD specs for #596: a scheduled meeting gets its own calendar event with a draft announcement.

Covers the quiet go-live (Google + Discord pushes, no announcement of any kind), the draft
announcement it leaves in the composer, the "gains a date and a time" trigger, and the
Guild leads announcement audience (who it reaches, by the ``all_guild_leads`` rule).
"""

from __future__ import annotations

from datetime import date, time, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.utils import timezone

from core.models import EventDelivery, Notification, NotificationPreference
from membership.models import AnnouncementDraft, CommunityEvent, GuildAnnouncement, Member
from tests.membership.factories import CommunityEventFactory, GuildFactory, MeetingFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _user(username: str) -> User:
    """A signed-in user with an auto-provisioned linked Member (the leads resolver needs ``last_login``)."""
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x", last_login=timezone.now()
    )


def _lead(username: str) -> User:
    user = _user(username)
    GuildFactory(guild_lead=user.member)
    return user


def _officer(username: str) -> User:
    user = _user(username)
    Member.objects.filter(pk=user.member.pk).update(fog_role=Member.FogRole.GUILD_OFFICER)
    return user


def _reached(channel: str) -> set[str]:
    return set(
        EventDelivery.objects.filter(
            event_key="site_announcement", channel=channel, status=EventDelivery.Status.SENT
        ).values_list("target_ref", flat=True)
    )


def describe_Meeting_create_calendar_event():
    def it_pushes_the_event_to_google_and_discord_and_announces_nothing():
        by = _user("quiet")
        meeting = MeetingFactory(guild=None, scheduled_time=time(18, 30))
        with (
            patch.object(CommunityEvent, "push_to_google") as google,
            patch.object(CommunityEvent, "push_to_discord") as discord,
            patch.object(CommunityEvent, "announce") as announce,
        ):
            event = meeting.create_calendar_event(by=by)
        google.assert_called_once_with(actor=by)
        discord.assert_called_once_with(actor=by)
        announce.assert_not_called()
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED
        assert event.sync_state == CommunityEvent.SyncState.PENDING
        assert event.discord_sync_state == CommunityEvent.SyncState.PENDING

    def it_sends_no_notification_email_or_discord_post_on_the_real_rails():
        _lead("lead-real")
        by = _user("real")
        mail.outbox.clear()
        MeetingFactory(guild=None, scheduled_time=time(18, 30)).create_calendar_event(by=by)
        MeetingFactory(guild=GuildFactory(), scheduled_time=time(19, 0)).create_calendar_event(by=by)
        assert EventDelivery.objects.count() == 0
        assert Notification.objects.count() == 0
        assert mail.outbox == []

    def describe_the_draft_announcement():
        def it_drafts_a_council_meeting_to_the_guild_leads_with_no_discord_channel():
            by = _user("council")
            meeting = MeetingFactory(guild=None, scheduled_time=time(18, 30), video_call_url="https://meet.example/lx")
            with patch.object(CommunityEvent, "push_live"):
                event = meeting.create_calendar_event(by=by)
            draft = AnnouncementDraft.objects.resumable().get()
            assert draft.audience == AnnouncementDraft.Audience.LEADS
            assert draft.guild is None
            assert draft.discord_channel == GuildAnnouncement.DiscordChannel.NONE
            assert draft.discord_on is False
            assert draft.author == by
            assert draft.title == "Guild Leads Announcement"
            assert draft.body == (
                f"<p><strong>Council — Monthly Meeting</strong></p><p>{event.when_display}</p>"
                "<p>https://meet.example/lx</p>"
            )

        def it_drafts_a_guild_meeting_to_that_guild_and_its_channel():
            guild = GuildFactory(name="Forge", meeting_location="Studio B")
            meeting = MeetingFactory(guild=guild, scheduled_time=time(19, 0))
            with patch.object(CommunityEvent, "push_live"):
                event = meeting.create_calendar_event(by=_user("guildy"))
            draft = AnnouncementDraft.objects.resumable().get()
            assert draft.audience == AnnouncementDraft.Audience.GUILD
            assert draft.guild == guild
            assert draft.discord_channel == GuildAnnouncement.DiscordChannel.GUILD
            assert draft.title == "Forge Announcement"
            assert draft.body == (
                f"<p><strong>Forge — Monthly Meeting</strong></p><p>{event.when_display}</p><p>Studio B</p>"
            )

        def it_leaves_the_location_line_out_when_there_is_none():
            meeting = MeetingFactory(guild=None, scheduled_time=time(18, 0))
            with patch.object(CommunityEvent, "push_live"):
                event = meeting.create_calendar_event(by=_user("noloc"))
            assert AnnouncementDraft.objects.get().body == (
                f"<p><strong>Council — Monthly Meeting</strong></p><p>{event.when_display}</p>"
            )

        def it_escapes_the_location():
            guild = GuildFactory(meeting_location="<b>Bay</b> & yard")
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))
            with patch.object(CommunityEvent, "push_live"):
                meeting.create_calendar_event(by=_user("esc"))
            assert "<p>&lt;b&gt;Bay&lt;/b&gt; &amp; yard</p>" in AnnouncementDraft.objects.get().body

    def describe_after_an_unlink():
        def it_builds_a_fresh_event_and_draft_without_announcing():
            by = _user("relink")
            meeting = MeetingFactory(guild=None, scheduled_time=time(18, 0))
            with patch.object(CommunityEvent, "push_live"):
                first = meeting.create_calendar_event(by=by)
            meeting.unlink_event(by=by)
            with (
                patch.object(CommunityEvent, "push_live") as push,
                patch.object(CommunityEvent, "announce") as announce,
            ):
                second = meeting.create_calendar_event(by=by)
            push.assert_called_once_with(actor=by)
            announce.assert_not_called()
            assert second.pk != first.pk
            assert AnnouncementDraft.objects.resumable().count() == 2


def describe_Meeting_add_to_calendar_if_scheduled():
    def it_creates_the_event_once_both_date_and_time_are_set():
        meeting = MeetingFactory(guild=None, scheduled_time=time(18, 0))
        with patch.object(CommunityEvent, "push_live"):
            event = meeting.add_to_calendar_if_scheduled(by=_user("ready"))
        assert event is not None
        meeting.refresh_from_db()
        assert meeting.event == event
        assert meeting.owns_event is True

    def it_creates_the_event_for_a_meeting_today():
        meeting = MeetingFactory(guild=None, scheduled_date=timezone.localdate(), scheduled_time=time(23, 30))
        with patch.object(CommunityEvent, "push_live"):
            assert meeting.add_to_calendar_if_scheduled(by=_user("today")) is not None

    def it_does_nothing_for_a_past_date():
        meeting = MeetingFactory(
            guild=None, scheduled_date=timezone.localdate() - timedelta(days=1), scheduled_time=time(18, 0)
        )
        assert meeting.add_to_calendar_if_scheduled(by=_user("past")) is None
        assert CommunityEvent.objects.count() == 0
        assert AnnouncementDraft.objects.count() == 0

    def it_does_nothing_for_a_partly_typed_year():
        meeting = MeetingFactory(guild=None, scheduled_date=date(2, 10, 6), scheduled_time=time(18, 0))
        assert meeting.add_to_calendar_if_scheduled(by=_user("year2")) is None
        assert CommunityEvent.objects.count() == 0
        assert AnnouncementDraft.objects.count() == 0

    def it_does_nothing_without_a_time():
        meeting = MeetingFactory(scheduled_time=None)
        assert meeting.add_to_calendar_if_scheduled(by=_user("notime")) is None
        assert CommunityEvent.objects.count() == 0

    def it_does_nothing_without_a_date():
        meeting = MeetingFactory(scheduled_date=None, scheduled_time=time(18, 0))
        assert meeting.add_to_calendar_if_scheduled(by=_user("nodate")) is None
        assert CommunityEvent.objects.count() == 0

    def it_leaves_an_already_linked_meeting_alone():
        by = _user("linked")
        meeting = MeetingFactory(guild=None, scheduled_time=time(18, 0))
        with patch.object(CommunityEvent, "push_live"):
            meeting.create_calendar_event(by=by)
        with patch.object(CommunityEvent, "push_live") as push:
            assert meeting.add_to_calendar_if_scheduled(by=by) is None
        push.assert_not_called()
        assert CommunityEvent.objects.count() == 1
        assert AnnouncementDraft.objects.count() == 1


def describe_CommunityEvent_publish():
    def it_still_announces_before_pushing_live():
        event = CommunityEventFactory()
        calls: list[str] = []
        with (
            patch.object(CommunityEvent, "announce", side_effect=lambda **_: calls.append("announce")),
            patch.object(CommunityEvent, "push_live", side_effect=lambda **_: calls.append("push_live")),
        ):
            event.publish(actor=None)
        assert calls == ["announce", "push_live"]


def describe_AnnouncementDraft_leads_audience():
    def it_names_the_audience_and_the_title():
        draft = AnnouncementDraft(audience=AnnouncementDraft.Audience.LEADS)
        assert draft.audience_value == "leads"
        assert draft.audience_label == "Guild leads"
        assert draft.announcement_category == "Guild Leads Announcement"

    def it_counts_exactly_the_all_guild_leads_resolver():
        _lead("lead-a")
        _officer("officer-a")
        _user("plain-a")
        assert AnnouncementDraft(audience=AnnouncementDraft.Audience.LEADS).recipient_count() == 2

    def describe_send():
        @pytest.fixture
        def people(db):
            return {
                "lead": _lead("lead-s"),
                "officer": _officer("officer-s"),
                "plain": _user("plain-s"),
                "sender": _user("sender-s"),
            }

        def it_reaches_every_guild_lead_and_nobody_else(people):
            draft = AnnouncementDraft.objects.create(
                author=people["sender"],
                audience=AnnouncementDraft.Audience.LEADS,
                title="Guild Leads Announcement",
                body="<p>Council on Tuesday.</p>",
            )
            emailed, total = draft.send()
            assert (emailed, total) == (2, 2)
            leads = {f"user:{people['lead'].pk}", f"user:{people['officer'].pk}"}
            assert _reached("in_app") == leads
            assert _reached("email") == leads
            draft.refresh_from_db()
            assert draft.sent_at is not None
            assert draft.delivery_period == f"announce:{draft.pk}"

        def it_respects_a_leads_makerspace_announcement_email_opt_out(people):
            NotificationPreference.objects.create(
                user=people["lead"], event_key="site_announcement", channel="email", enabled=False
            )
            draft = AnnouncementDraft.objects.create(
                author=people["sender"],
                audience=AnnouncementDraft.Audience.LEADS,
                title="Guild Leads Announcement",
                body="<p>Council on Tuesday.</p>",
            )
            draft.send()
            assert f"user:{people['lead'].pk}" in _reached("in_app")
            assert _reached("email") == {f"user:{people['officer'].pk}"}

        def it_posts_nothing_to_discord_with_no_channel(people):
            draft = AnnouncementDraft.objects.create(
                author=people["sender"],
                audience=AnnouncementDraft.Audience.LEADS,
                title="Guild Leads Announcement",
                body="<p>Council on Tuesday.</p>",
                discord_channel=GuildAnnouncement.DiscordChannel.NONE,
            )
            draft.send()
            assert not EventDelivery.objects.filter(channel="discord").exists()

        def it_resolves_the_chosen_central_channel(people):
            draft = AnnouncementDraft.objects.create(
                author=people["sender"],
                audience=AnnouncementDraft.Audience.LEADS,
                title="Guild Leads Announcement",
                body="<p>Council on Tuesday.</p>",
                discord_channel=GuildAnnouncement.DiscordChannel.LEADERSHIP,
            )
            with patch("membership.models.resolve_channel_webhook", return_value="") as webhook:
                draft.send()
            webhook.assert_called_once_with(GuildAnnouncement.DiscordChannel.LEADERSHIP, None)
