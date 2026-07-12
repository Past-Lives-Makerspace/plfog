"""BDD specs for the event-scheduling UI: CommunityEventForm.clean_publish_at + the new
booleans, the lead/admin edit views routing through schedule_or_go_live (no-strand), the
admin "Scheduled — not yet announced" section, the guild "Scheduled for …" badge, and the
edit-page template state (toggles + a class-based reveal, not inline display)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub.forms import CommunityEventForm
from membership.models import CommunityEvent, Member
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory

State = CommunityEvent.ModerationState


def _at(days: float) -> str:
    return (timezone.localtime() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")


def _payload(**overrides: str) -> dict:
    data = {
        "title": "Forge Night",
        "starts_at": _at(30),
        "ends_at": _at(30.1),
        "location": "Main Studio",
        "description": "Come forge.",
        "recurrence": "none",
    }
    data.update(overrides)
    return data


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


@pytest.mark.django_db
def describe_clean_publish_at():
    def it_accepts_a_blank_publish_at():
        form = CommunityEventForm(data=_payload(), as_admin=False)
        assert form.is_valid()
        assert form.cleaned_data["publish_at"] is None

    def it_accepts_a_future_time_before_the_start():
        form = CommunityEventForm(data=_payload(publish_at=_at(20)), as_admin=False)
        assert form.is_valid()
        assert form.cleaned_data["publish_at"] is not None

    def it_rejects_a_time_in_the_past():
        form = CommunityEventForm(data=_payload(publish_at=_at(-1)), as_admin=False)
        assert not form.is_valid()
        assert "publish_at" in form.errors

    def it_rejects_a_time_at_or_after_the_start():
        form = CommunityEventForm(data=_payload(publish_at=_at(30)), as_admin=False)  # == starts_at
        assert not form.is_valid()
        assert "publish_at" in form.errors


@pytest.mark.django_db
def describe_reminder_booleans():
    def it_round_trips_the_four_toggles():
        guild = GuildFactory()
        form = CommunityEventForm(
            data=_payload(
                event_type="guild_meeting",
                guild=str(guild.pk),
                remind_7d="on",
                remind_1d="on",
                notify_happening_now="on",
            ),
            as_admin=True,
        )
        assert form.is_valid(), form.errors
        instance = form.save(commit=False)
        assert instance.remind_7d is True
        assert instance.remind_3d is False
        assert instance.remind_1d is True
        assert instance.notify_happening_now is True


@pytest.mark.django_db
def describe_lead_scheduling_views():
    def it_parks_a_new_event_with_a_future_publish_at(client: Client):
        user = _user_with_role("lead_sched")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="lead_sched", password="pass")
        with patch.object(CommunityEvent, "announce") as mock_announce:
            resp = client.post(reverse("hub_guild_event_add", args=[guild.pk]), data=_payload(publish_at=_at(20)))
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(guild=guild)
        assert event.moderation_state == State.SCHEDULED
        assert event.publish_at is not None
        mock_announce.assert_not_called()

    def it_publishes_a_scheduled_event_when_the_schedule_is_cleared(client: Client):
        user = _user_with_role("lead_clear")
        guild = GuildFactory(guild_lead=user.member)
        event = CommunityEventFactory(
            guild=guild, moderation_state=State.SCHEDULED, publish_at=timezone.now() + timedelta(days=5)
        )
        client.login(username="lead_clear", password="pass")
        with patch.object(CommunityEvent, "announce") as mock_announce:
            resp = client.post(
                reverse("hub_guild_event_edit", args=[guild.pk, event.pk]),
                data=_payload(),  # no publish_at
            )
        assert resp.status_code == 302
        event.refresh_from_db()
        assert event.moderation_state == State.PUBLISHED
        assert event.publish_at is None
        mock_announce.assert_called_once()

    def it_keeps_a_scheduled_event_parked_when_a_future_time_is_kept(client: Client):
        user = _user_with_role("lead_keep")
        guild = GuildFactory(guild_lead=user.member)
        event = CommunityEventFactory(
            guild=guild, moderation_state=State.SCHEDULED, publish_at=timezone.now() + timedelta(days=5)
        )
        client.login(username="lead_keep", password="pass")
        with patch.object(CommunityEvent, "announce") as mock_announce:
            resp = client.post(
                reverse("hub_guild_event_edit", args=[guild.pk, event.pk]), data=_payload(publish_at=_at(20))
            )
        assert resp.status_code == 302
        event.refresh_from_db()
        assert event.moderation_state == State.SCHEDULED
        mock_announce.assert_not_called()

    def it_only_re_pushes_a_published_event_on_edit(client: Client):
        user = _user_with_role("lead_pub")
        guild = GuildFactory(guild_lead=user.member)
        event = CommunityEventFactory(guild=guild)  # PUBLISHED
        client.login(username="lead_pub", password="pass")
        with (
            patch.object(CommunityEvent, "announce") as mock_announce,
            patch.object(CommunityEvent, "push_to_google") as mock_push,
        ):
            resp = client.post(
                reverse("hub_guild_event_edit", args=[guild.pk, event.pk]), data=_payload(title="Renamed")
            )
        assert resp.status_code == 302
        mock_announce.assert_not_called()
        mock_push.assert_called_once()


@pytest.mark.django_db
def describe_scheduled_findability():
    def it_shows_a_site_wide_scheduled_event_to_an_admin(client: Client):
        _user_with_role("cal_admin", fog_role=Member.FogRole.ADMIN)
        scheduled = CommunityEventFactory(
            community=True, moderation_state=State.SCHEDULED, publish_at=timezone.now() + timedelta(days=3)
        )
        client.login(username="cal_admin", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert scheduled in list(resp.context["scheduled_events"])
        # A parked event never leaks into the public upcoming list.
        assert scheduled not in list(resp.context["upcoming_events"])

    def it_hides_the_scheduled_section_from_a_non_admin(client: Client):
        _user_with_role("cal_member")
        CommunityEventFactory(
            community=True, moderation_state=State.SCHEDULED, publish_at=timezone.now() + timedelta(days=3)
        )
        client.login(username="cal_member", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert list(resp.context["scheduled_events"]) == []

    def it_badges_a_scheduled_guild_event_on_the_guild_events_tab(client: Client):
        user = _user_with_role("lead_badge")
        guild = GuildFactory(guild_lead=user.member)
        CommunityEventFactory(
            guild=guild, moderation_state=State.SCHEDULED, publish_at=timezone.now() + timedelta(days=3)
        )
        client.login(username="lead_badge", password="pass")
        resp = client.get(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=events")
        assert b"Scheduled for" in resp.content


@pytest.mark.django_db
def describe_edit_page_template_state():
    def it_renders_toggles_and_a_class_based_reveal(client: Client):
        user = _user_with_role("tmpl_lead")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="tmpl_lead", password="pass")
        resp = client.get(reverse("hub_guild_event_add", args=[guild.pk]))
        content = resp.content
        assert b"Announcements &amp; reminders" in content
        assert b"Schedule the announcement for later" in content
        # Rule 12: the x-show reveal uses a CSS class, never an inline display style.
        assert b'class="pl-reveal"' in content
        assert b'pl-reveal" style="display' not in content
        # Reminders render through the toggle component, not as raw checkboxes.
        assert b"pl-toggle" in content
