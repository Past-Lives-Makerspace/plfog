"""BDD specs for "Publish without announcing to members" on the event composers, and Manage
Events' Not announced pill and Announce now.

The switch is offered to whoever administers events (an admin or a Calendar Administrator)
wherever a save would announce, and to nobody else: a "Can create events" holder or a guild
lead never sees it, and their POST with it set is ignored. Manage Events pills a live event
published without its announcement and offers Announce now, which sends it once.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import EventDelivery, SiteConfiguration
from hub import discord_calendar_posts as dcp
from membership.models import AdminCapability, CommunityEvent, Member
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

States = CommunityEvent.ModerationState
_TOGGLE = 'name="announcement_held"'


def _user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(
        username=username, email=f"{username}@example.com", password="pass", last_login=timezone.now()
    )
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _login_admin(client: Client, username: str = "quiet_admin") -> User:
    user = _user(username, fog_role=Member.FogRole.ADMIN)
    client.login(username=username, password="pass")
    return user


def _login_granted(client: Client, username: str, capability: str) -> User:
    user = _user(username)
    user.member.admin_capabilities.create(capability=capability)
    client.login(username=username, password="pass")
    return user


def _at(days: float) -> str:
    return (timezone.localtime() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")


def _payload(**overrides: str) -> dict:
    data = {
        "title": "Holiday Bazaar",
        "starts_at": _at(40),
        "ends_at": _at(40.2),
        "location": "Main Studio",
        "description": "Makers sell their work.",
        "recurrence": "none",
    }
    data.update(overrides)
    return data


def _bells(event: CommunityEvent) -> int:
    return EventDelivery.objects.filter(channel="in_app", period=f"event:{event.pk}:published").count()


def _messages(response) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def describe_the_composer_switch():
    def it_is_offered_to_an_admin_adding_an_event(client: Client):
        _login_admin(client)
        html = client.get(reverse("hub_event_add")).content.decode()
        assert _TOGGLE in html
        assert "data-quiet-publish" in html
        assert "Announce now on Manage Events" in html  # the help bubble says how to send it later

    def it_is_offered_to_a_calendar_administrator(client: Client):
        _login_granted(client, "quiet_approver", AdminCapability.Capability.EVENTS_APPROVER)
        assert _TOGGLE in client.get(reverse("hub_event_add")).content.decode()

    def it_is_offered_to_an_admin_on_a_guild_composer(client: Client):
        _login_admin(client, "quiet_guild_admin")
        guild = GuildFactory()
        assert _TOGGLE in client.get(reverse("hub_guild_event_add", args=[guild.pk])).content.decode()

    def it_is_hidden_from_a_granted_event_creator(client: Client):
        _login_granted(client, "quiet_creator", AdminCapability.Capability.EVENT_CREATOR)
        response = client.get(reverse("hub_event_add"))
        assert response.status_code == 200
        assert _TOGGLE not in response.content.decode()
        assert response.context["form"].offers_quiet_publish is False

    def it_is_hidden_from_a_guild_lead(client: Client):
        user = _user("quiet_lead")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="quiet_lead", password="pass")
        assert _TOGGLE not in client.get(reverse("hub_guild_event_add", args=[guild.pk])).content.decode()

    def it_is_hidden_on_a_live_event(client: Client):
        _login_admin(client, "quiet_live_admin")
        event = CommunityEventFactory(community=True)
        assert _TOGGLE not in client.get(reverse("hub_event_edit", args=[event.pk])).content.decode()

    def it_is_hidden_on_a_draft_that_was_announced_before(client: Client):
        _login_admin(client, "quiet_republish_admin")
        event = CommunityEventFactory(
            community=True, moderation_state=States.DRAFT, channel_announced_at=timezone.now() - timedelta(days=1)
        )
        assert _TOGGLE not in client.get(reverse("hub_event_edit", args=[event.pk])).content.decode()

    def it_names_the_quiet_save_on_the_button_of_a_held_draft(client: Client):
        _login_admin(client, "quiet_button_admin")
        event = CommunityEventFactory(community=True, moderation_state=States.DRAFT, announcement_held=True)
        html = client.get(reverse("hub_event_edit", args=[event.pk])).content.decode()
        assert "checked" in html.split(_TOGGLE)[1].split(">")[0]
        assert ">Save and publish</button>" in html


def describe_saving_from_the_composer():
    def it_publishes_quietly_when_an_admin_ticks_it(client: Client):
        _login_admin(client, "quiet_saver")
        response = client.post(reverse("hub_event_add"), data=_payload(announcement_held="on"))
        assert response.status_code == 302
        event = CommunityEvent.objects.get(title="Holiday Bazaar")
        assert event.moderation_state == States.PUBLISHED
        assert event.announcement_held is True
        assert event.announcement_owed_since is None
        assert event.sync_state == CommunityEvent.SyncState.PENDING  # pushed live all the same
        assert _bells(event) == 0

    def it_announces_as_today_when_an_admin_leaves_it_off(client: Client):
        _login_admin(client, "loud_saver")
        client.post(reverse("hub_event_add"), data=_payload())
        event = CommunityEvent.objects.get(title="Holiday Bazaar")
        assert event.announcement_held is False
        assert _bells(event) >= 1

    def it_announces_a_held_scheduled_event_once_an_admin_unticks_it(client: Client):
        _login_admin(client, "untick_admin")
        event = CommunityEventFactory(
            community=True,
            title="Holiday Bazaar",
            moderation_state=States.SCHEDULED,
            publish_at=timezone.now() + timedelta(days=2),
            starts_at=timezone.now() + timedelta(days=40),
            ends_at=timezone.now() + timedelta(days=40, hours=4),
            announcement_held=True,
        )
        client.post(reverse("hub_event_edit", args=[event.pk]), data=_payload(publish_at=""))
        event.refresh_from_db()
        assert event.moderation_state == States.PUBLISHED
        assert event.announcement_held is False
        assert _bells(event) >= 1

    def it_ignores_the_field_from_a_granted_event_creator(client: Client):
        _login_granted(client, "sneaky_creator", AdminCapability.Capability.EVENT_CREATOR)
        client.post(reverse("hub_event_add"), data=_payload(announcement_held="on"))
        event = CommunityEvent.objects.get(title="Holiday Bazaar")
        assert event.moderation_state == States.PENDING
        assert event.announcement_held is False

    def it_ignores_the_field_from_a_guild_lead(client: Client):
        user = _user("sneaky_lead")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="sneaky_lead", password="pass")
        client.post(reverse("hub_guild_event_add", args=[guild.pk]), data=_payload(announcement_held="on"))
        event = CommunityEvent.objects.get(title="Holiday Bazaar")
        assert event.moderation_state == States.PUBLISHED
        assert event.announcement_held is False
        assert event.announcement_owed_since is None  # the inline send already finished

    def it_announces_a_creators_event_when_a_reviewer_approves_it(client: Client):
        creator = _login_granted(client, "reviewed_creator", AdminCapability.Capability.EVENT_CREATOR)
        client.post(reverse("hub_event_add"), data=_payload(announcement_held="on"))
        event = CommunityEvent.objects.get(title="Holiday Bazaar")
        reviewer = _user("approving_admin", fog_role=Member.FogRole.ADMIN)
        event.approve(reviewer=reviewer)
        event.refresh_from_db()
        assert event.submitted_by == creator
        assert (event.moderation_state, event.announcement_held) == (States.PUBLISHED, False)
        assert _bells(event) >= 1


def describe_manage_events():
    def _held(**overrides: object) -> CommunityEvent:
        return CommunityEventFactory(community=True, announcement_held=True, **overrides)

    def describe_the_not_announced_pill():
        def it_marks_a_live_held_event_in_the_list_and_the_header(client: Client):
            _login_admin(client, "pill_admin")
            event = _held(title="Quiet Bazaar")
            listing = client.get(reverse("hub_admin_events")).content.decode()
            assert listing.count("data-not-announced") == 1
            assert "data-not-announced" in client.get(reverse("hub_admin_event", args=[event.pk])).content.decode()

        def it_leaves_announced_and_parked_events_alone(client: Client):
            _login_admin(client, "nopill_admin")
            CommunityEventFactory(community=True)
            _held(moderation_state=States.SCHEDULED, publish_at=timezone.now() + timedelta(days=1))
            assert "data-not-announced" not in client.get(reverse("hub_admin_events")).content.decode()

    def describe_announce_now():
        def it_offers_the_button_and_its_confirm_only_on_a_live_held_event(client: Client):
            _login_admin(client, "button_admin")
            held = _held()
            loud = CommunityEventFactory(community=True)
            held_html = client.get(reverse("hub_admin_event", args=[held.pk])).content.decode()
            loud_html = client.get(reverse("hub_admin_event", args=[loud.pk])).content.decode()
            assert 'data-action="announce-now"' in held_html
            assert reverse("hub_admin_event_announce_now", args=[held.pk]) in held_html
            assert 'data-action="announce-now"' not in loud_html

        def it_announces_once_however_many_times_it_is_pressed(client: Client):
            _login_admin(client, "press_admin")
            event = _held()
            url = reverse("hub_admin_event_announce_now", args=[event.pk])
            first = client.post(url)
            second = client.post(url)
            assert first["Location"] == reverse("hub_admin_event", args=[event.pk])
            assert any("Announcing it now" in m for m in _messages(first))
            assert any("already announced" in m for m in _messages(second))
            event.refresh_from_db()
            assert event.announcement_held is False
            assert _bells(event) == 1
            assert 'data-action="announce-now"' not in client.get(first["Location"]).content.decode()

        def it_refuses_an_event_that_is_not_live(client: Client):
            _login_admin(client, "draft_press_admin")
            event = _held(moderation_state=States.DRAFT)
            response = client.post(reverse("hub_admin_event_announce_now", args=[event.pk]))
            assert any("Only a published event" in m for m in _messages(response))
            event.refresh_from_db()
            assert event.announcement_held is True

        def it_is_gated_like_the_other_actions(client: Client):
            _user("plain_presser")
            client.login(username="plain_presser", password="pass")
            event = _held()
            assert client.post(reverse("hub_admin_event_announce_now", args=[event.pk])).status_code == 403
            event.refresh_from_db()
            assert event.announcement_held is True

        def it_admits_a_calendar_administrator(client: Client):
            _login_granted(client, "approver_presser", AdminCapability.Capability.EVENTS_APPROVER)
            event = _held()
            client.post(reverse("hub_admin_event_announce_now", args=[event.pk]))
            event.refresh_from_db()
            assert event.announcement_held is False

        def it_refuses_a_get(client: Client):
            _login_admin(client, "get_presser")
            event = _held()
            assert client.get(reverse("hub_admin_event_announce_now", args=[event.pk])).status_code == 405


def describe_the_calendar_announcer():
    @respx.mock
    def it_skips_a_held_event_until_it_is_announced(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        channel = "1309624926893768854"
        route = respx.post(f"https://discord.com/api/v10/channels/{channel}/messages").mock(
            return_value=httpx.Response(200, json={"id": "msg-9"})
        )
        config = SiteConfiguration.load()
        config.discord_calendar_posts_enabled = True
        config.discord_calendar_channel_id = channel
        config.save(update_fields=["discord_calendar_posts_enabled", "discord_calendar_channel_id"])
        event = CommunityEventFactory(community=True, announcement_held=True)
        assert dcp.announce_new_events() == 0
        assert not route.called
        CommunityEvent.objects.filter(pk=event.pk).update(announcement_held=False)
        assert dcp.announce_new_events() == 1
        event.refresh_from_db()
        assert event.discord_announce_message_id == "msg-9"
