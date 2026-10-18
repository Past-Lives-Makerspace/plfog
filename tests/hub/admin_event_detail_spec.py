"""BDD specs for one event's admin screen on Manage Events (#831, part 2): the gate, the header
and tab slots, the actions each state allows, the decisions posting `next`, Duplicate,
Unpublish, Delete with `next`, and the places a draft must never show."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.files.base import ContentFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core.integrations import discord_events as de
from core.integrations.google_calendar import GoogleCalendarClient
from core.models import SiteConfiguration
from membership.models import AdminCapability, CommunityEvent, Member
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory, tiny_png_bytes

pytestmark = pytest.mark.django_db

States = CommunityEvent.ModerationState
Sync = CommunityEvent.SyncState


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _admin(client: Client, username: str = "detail_admin") -> User:
    user = _user_with_role(username, fog_role=Member.FogRole.ADMIN)
    client.login(username=username, password="pass")
    return user


def _approver(client: Client, username: str = "detail_approver") -> User:
    user = _user_with_role(username)
    user.member.admin_capabilities.create(capability=AdminCapability.Capability.EVENTS_APPROVER)
    client.login(username=username, password="pass")
    return user


def _url(event: CommunityEvent) -> str:
    return reverse("hub_admin_event", args=[event.pk])


def _actions(body: str) -> set[str]:
    return {
        name
        for name in ("approve", "changes", "decline", "retry-sync", "duplicate", "unpublish", "delete")
        if f'data-action="{name}"' in body
    }


def _enable_google_sync() -> None:
    config = SiteConfiguration.load()
    config.google_calendar_sync_enabled = True
    config.public_google_calendar_id = "cal@group.calendar.google.com"
    config.save(update_fields=["google_calendar_sync_enabled", "public_google_calendar_id"])


def describe_the_gate():
    def it_refuses_a_plain_member(client: Client):
        _user_with_role("plain_detail")
        client.login(username="plain_detail", password="pass")
        assert client.get(_url(CommunityEventFactory(community=True))).status_code == 403

    def it_opens_for_a_calendar_administrator(client: Client):
        _approver(client)
        assert client.get(_url(CommunityEventFactory(community=True))).status_code == 200

    def it_404s_studio_hours(client: Client):
        _admin(client)
        assert client.get(_url(CommunityEventFactory(studio_hours=True))).status_code == 404


def describe_the_header():
    def it_shows_edit_and_view_page_for_a_live_site_wide_event(client: Client):
        _approver(client)
        event = CommunityEventFactory(community=True, title="Potluck")
        body = client.get(_url(event)).content.decode()
        assert f'href="{reverse("hub_event_edit", args=[event.pk])}" data-event-edit' in body
        assert f'href="{reverse("hub_event_detail", args=[event.pk])}" data-event-view' in body
        assert 'data-event-status="published"' in body
        assert "pl-qr-share" in body
        assert 'aria-label="This event"' in body
        assert "data-eventbrite-tab" not in body

    def it_links_a_calendar_administrator_to_the_calendars_composer_on_a_guild_event(client: Client):
        # They may edit every event, but the guild's own composer admits only its leadership.
        _approver(client)
        guild = GuildFactory()
        event = CommunityEventFactory(guild=guild)
        body = client.get(_url(event)).content.decode()
        assert f'href="{reverse("hub_event_edit", args=[event.pk])}" data-event-edit' in body
        assert reverse("hub_guild_event_edit", args=[guild.pk, event.pk]) not in body

    def it_links_an_admin_to_the_guilds_own_composer_on_a_guild_event(client: Client):
        # The same rule the public event page applies: whoever passes can_edit_guild gets the guild's composer.
        _admin(client)
        guild = GuildFactory()
        event = CommunityEventFactory(guild=guild)
        body = client.get(_url(event)).content.decode()
        assert f'href="{reverse("hub_guild_event_edit", args=[guild.pk, event.pk])}" data-event-edit' in body

    def it_offers_no_edit_to_the_lead_of_another_guild(client: Client):
        # The screen itself refuses them, and the one edit rule shows them no Edit on the event page either.
        user = _user_with_role("other_lead")
        GuildFactory(guild_lead=user.member)
        client.login(username="other_lead", password="pass")
        event = CommunityEventFactory(guild=GuildFactory())
        assert client.get(_url(event)).status_code == 403
        page = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert reverse("hub_event_edit", args=[event.pk]) not in page
        assert reverse("hub_guild_event_edit", args=[event.guild.pk, event.pk]) not in page

    def it_hides_view_page_and_the_qr_card_until_published(client: Client):
        _admin(client)
        body = client.get(_url(CommunityEventFactory(community=True, pending=True))).content.decode()
        assert "data-event-view" not in body
        assert "pl-qr-share" not in body
        assert 'data-event-status="pending"' in body

    def it_reserves_the_eventbrite_tab_for_a_listed_event(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        with patch.object(CommunityEvent, "eventbrite_listed", new_callable=PropertyMock, return_value=True):
            body = client.get(_url(event)).content.decode()
        assert "data-eventbrite-tab" in body


def describe_the_actions_by_state():
    def it_offers_the_three_decisions_on_a_pending_proposal(client: Client):
        _admin(client)
        body = client.get(_url(CommunityEventFactory(community=True, pending=True))).content.decode()
        assert _actions(body) == {"approve", "changes", "decline", "duplicate", "delete"}

    def it_drops_request_changes_once_changes_were_requested(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True, pending=True, moderation_state=States.CHANGES_REQUESTED)
        assert _actions(client.get(_url(event)).content.decode()) == {"approve", "decline", "duplicate", "delete"}

    def it_offers_unpublish_on_a_live_event(client: Client):
        _admin(client)
        body = client.get(_url(CommunityEventFactory(community=True))).content.decode()
        assert _actions(body) == {"duplicate", "unpublish", "delete"}

    def it_offers_only_duplicate_and_delete_on_a_draft(client: Client):
        _admin(client)
        body = client.get(_url(CommunityEventFactory(community=True, moderation_state=States.DRAFT))).content.decode()
        assert _actions(body) == {"duplicate", "delete"}
        assert 'data-event-status="draft"' in body

    @override_settings(GOOGLE_CALENDAR_SYNC_ENABLED=True)
    def it_offers_retry_sync_while_the_google_push_is_failed_or_pending(client: Client):
        _admin(client)
        _enable_google_sync()
        failed = CommunityEventFactory(community=True, sync_state=Sync.FAILED, sync_error="boom")
        synced = CommunityEventFactory(community=True, pushed=True)
        assert "retry-sync" in _actions(client.get(_url(failed)).content.decode())
        assert "retry-sync" not in _actions(client.get(_url(synced)).content.decode())

    def it_hides_retry_sync_while_google_sync_is_off(client: Client):
        _admin(client)
        failed = CommunityEventFactory(community=True, sync_state=Sync.FAILED)
        assert "retry-sync" not in _actions(client.get(_url(failed)).content.decode())


def describe_a_decision_with_next():
    def it_approves_and_lands_back_on_the_screen(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True, pending=True)
        with patch.object(CommunityEvent, "announce"):
            resp = client.post(
                reverse("hub_event_review_decision", args=[event.pk]) + "?decision=approve",
                data={"next": _url(event)},
            )
        assert resp.status_code == 302
        assert resp["Location"] == _url(event)
        event.refresh_from_db()
        assert event.moderation_state == States.PUBLISHED

    def it_ignores_an_unsafe_next(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True, pending=True)
        resp = client.post(
            reverse("hub_event_review_decision", args=[event.pk]),
            data={"decision": "decline", "notes": "No.", "next": "https://evil.example/"},
        )
        assert resp["Location"] == reverse("hub_event_review_queue")
        event.refresh_from_db()
        assert event.moderation_state == States.DECLINED

    def it_returns_a_missing_note_to_the_screen_as_a_message(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True, pending=True)
        resp = client.post(
            reverse("hub_event_review_decision", args=[event.pk]),
            data={"decision": "decline", "notes": "", "next": _url(event)},
        )
        assert resp.status_code == 302
        assert resp["Location"] == _url(event)
        assert [m.message for m in get_messages(resp.wsgi_request)] == ["Add a note so the proposer knows why."]
        event.refresh_from_db()
        assert event.moderation_state == States.PENDING

    def it_posts_next_from_the_screens_own_forms(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True, pending=True)
        body = client.get(_url(event)).content.decode()
        assert body.count(f'name="next" value="{_url(event)}"') >= 3


def describe_duplicate():
    def it_copies_the_event_and_opens_the_copy_in_the_composer(client: Client):
        user = _admin(client)
        source = CommunityEventFactory(community=True, pushed=True, title="Potluck")
        source.photo.save("flyer.png", ContentFile(tiny_png_bytes()), save=True)
        resp = client.post(reverse("hub_admin_event_duplicate", args=[source.pk]))
        copy = CommunityEvent.objects.get(title="Potluck (copy)")
        assert resp.status_code == 302
        assert resp["Location"] == reverse("hub_event_edit", args=[copy.pk])
        assert copy.moderation_state == States.DRAFT
        assert copy.created_by == user
        assert (copy.google_event_id, copy.google_ical_uid, copy.sync_state) == ("", "", Sync.IDLE)
        assert copy.photo.name != source.photo.name

    def it_rejects_a_get(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        assert client.get(reverse("hub_admin_event_duplicate", args=[event.pk])).status_code == 405

    def it_refuses_a_plain_member(client: Client):
        _user_with_role("plain_dup")
        client.login(username="plain_dup", password="pass")
        event = CommunityEventFactory(community=True)
        assert client.post(reverse("hub_admin_event_duplicate", args=[event.pk])).status_code == 403
        assert CommunityEvent.objects.count() == 1


def describe_unpublish():
    def _clients() -> tuple[MagicMock, MagicMock]:
        google = MagicMock(spec=GoogleCalendarClient)
        google.enabled = True
        google.delete_event = MagicMock()
        discord = MagicMock(enabled=True, server_id="srv")
        discord.delete_event = MagicMock()
        return google, discord

    def it_takes_the_event_off_google_discord_and_the_announcement_buttons(client: Client):
        _admin(client)
        event = CommunityEventFactory(
            community=True,
            pushed=True,
            discord_event_id="disc-9",
            discord_sync_state=Sync.SYNCED,
            channel_announced_at=timezone.now() - timedelta(days=1),
            discord_announce_channel_id="chan-9",
            discord_announce_message_id="msg-9",
        )
        google, discord = _clients()
        with (
            patch.object(GoogleCalendarClient, "from_settings", return_value=google),
            patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=discord),
            patch("core.integrations.discord_channel.edit_channel_message") as edit_message,
        ):
            resp = client.post(reverse("hub_admin_event_unpublish", args=[event.pk]))
        assert resp.status_code == 302
        assert resp["Location"] == _url(event)
        google.delete_event.assert_called_once_with("cal@group.calendar.google.com", event.google_event_id)
        discord.delete_event.assert_called_once_with("srv", "disc-9")
        assert edit_message.call_args.args[:2] == ("chan-9", "msg-9")
        assert edit_message.call_args.kwargs["components"] == []
        event.refresh_from_db()
        assert event.moderation_state == States.DRAFT
        assert (event.google_event_id, event.discord_event_id) == ("", "")
        assert (event.sync_state, event.discord_sync_state) == (Sync.IDLE, Sync.IDLE)
        assert event.channel_announced_at is not None

    def it_refuses_an_event_that_is_not_published(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True, pending=True)
        with patch.object(CommunityEvent, "remove_from_google") as google:
            resp = client.post(reverse("hub_admin_event_unpublish", args=[event.pk]))
        google.assert_not_called()
        assert resp["Location"] == _url(event)
        assert [m.message for m in get_messages(resp.wsgi_request)] == ["Only a published event can be unpublished."]
        event.refresh_from_db()
        assert event.moderation_state == States.PENDING

    def it_rejects_a_get(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        assert client.get(reverse("hub_admin_event_unpublish", args=[event.pk])).status_code == 405


def describe_delete_with_next():
    def it_lands_on_the_list_once_the_row_is_gone(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        body = client.get(_url(event)).content.decode()
        assert f'name="next" value="{reverse("hub_admin_events")}"' in body
        resp = client.post(reverse("hub_event_delete", args=[event.pk]), data={"next": reverse("hub_admin_events")})
        assert resp["Location"] == reverse("hub_admin_events")
        assert not CommunityEvent.objects.filter(pk=event.pk).exists()


def describe_a_draft_is_invisible():
    def it_is_off_home_the_calendars_proposals_and_the_public_page(client: Client):
        from hub.home import _upcoming_items

        user = _admin(client)
        live = CommunityEventFactory(community=True, title="Live Potluck")
        draft = CommunityEventFactory(
            community=True, title="Draft Potluck", moderation_state=States.DRAFT, submitted_by=user
        )
        assert [item.title for item in _upcoming_items(user.member)] == ["Live Potluck"]
        assert list(client.get(reverse("hub_community_calendar")).context["my_proposals"]) == []
        assert client.get(reverse("hub_event_detail", args=[draft.pk])).status_code == 404
        assert client.get(reverse("hub_event_detail", args=[live.pk])).status_code == 200

    def it_wears_a_draft_pill_on_the_guild_events_tab(client: Client):
        _admin(client)
        guild = GuildFactory()
        CommunityEventFactory(guild=guild, moderation_state=States.DRAFT)
        body = client.get(reverse("hub_guild_edit", args=[guild.pk])).content.decode()
        assert "data-event-draft" in body

    def it_opens_in_the_full_composer_as_save_and_announce(client: Client):
        _admin(client)
        draft = CommunityEventFactory(community=True, moderation_state=States.DRAFT)
        resp = client.get(reverse("hub_event_edit", args=[draft.pk]))
        assert resp.status_code == 200
        form = resp.context["form"]
        assert form.saves_announce is True
        assert form.in_review is False
        assert form.review_first is False
