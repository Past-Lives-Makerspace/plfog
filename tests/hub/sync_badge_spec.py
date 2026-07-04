"""BDD specs for the Google Calendar sync-state badge (Screen C / E) and the
``google_sync_enabled`` context flag that gates it.

The badge is admin-facing on the member-readable Community Calendar Events tab and
appears only when BOTH sync gates are on (env master switch AND the Site-Settings
toggle). It renders per ``sync_state``, surfaces the failure reason as visible inline
text (never a tooltip), and offers an admin-only "Retry sync now" on a FAILED row.

Both gates are set inside each test — the env master via the pytest-django ``settings``
fixture (an ``@override_settings`` on a ``describe_*`` block does NOT reach the nested
tests), the runtime toggle via the SiteConfiguration singleton.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration
from membership.models import CommunityEvent, Member
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory


def _admin(username: str = "adm") -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _both_gates_on(settings) -> None:
    """Turn on both sync gates: the env master switch + the runtime SiteConfig toggle."""
    settings.GOOGLE_CALENDAR_SYNC_ENABLED = True
    config = SiteConfiguration.load()
    config.google_calendar_sync_enabled = True
    config.save(update_fields=["google_calendar_sync_enabled"])


def _calendar(client: Client) -> bytes:
    resp = client.get(reverse("hub_community_calendar"))
    assert resp.status_code == 200
    return resp.content


@pytest.mark.django_db
def describe_badge_per_state():
    def it_shows_a_synced_badge_for_a_synced_event(client: Client, settings):
        _admin()
        _both_gates_on(settings)
        CommunityEventFactory(community=True, sync_state=CommunityEvent.SyncState.SYNCED)
        client.login(username="adm", password="pass")
        assert b"Synced to Google" in _calendar(client)

    def it_shows_a_pending_badge_with_the_config_gap_reason(client: Client, settings):
        _admin()
        _both_gates_on(settings)
        CommunityEventFactory(
            community=True,
            sync_state=CommunityEvent.SyncState.PENDING,
            sync_error="No Google Calendar linked for this event yet.",
        )
        client.login(username="adm", password="pass")
        body = _calendar(client)
        assert b"Sync pending" in body
        # The config-gap reason is visible inline text, not tooltip-only.
        assert b"No Google Calendar linked for this event yet." in body

    def it_shows_a_failed_badge_with_a_visible_reason_and_retry_button(client: Client, settings):
        _admin()
        _both_gates_on(settings)
        event = CommunityEventFactory(
            community=True,
            sync_state=CommunityEvent.SyncState.FAILED,
            sync_error="Calendar not shared with the service account",
        )
        client.login(username="adm", password="pass")
        body = _calendar(client)
        assert b"Sync failed" in body
        # Reason rendered as visible inline text (its own line), never only a title tooltip.
        assert b"Calendar not shared with the service account" in body
        assert b"pl-sync-badge__reason" in body
        assert b'title="Calendar not shared' not in body
        assert reverse("hub_event_retry_sync", args=[event.pk]).encode() in body
        assert b"Retry sync now" in body

    def it_shows_no_badge_for_an_idle_event(client: Client, settings):
        _admin()
        _both_gates_on(settings)
        CommunityEventFactory(community=True, sync_state=CommunityEvent.SyncState.IDLE)
        client.login(username="adm", password="pass")
        assert b"pl-sync-badge" not in _calendar(client)


@pytest.mark.django_db
def describe_badge_gating():
    def it_hides_the_badge_when_the_site_toggle_is_off(client: Client, settings):
        _admin()
        settings.GOOGLE_CALENDAR_SYNC_ENABLED = True  # env on, runtime toggle left off
        CommunityEventFactory(community=True, sync_state=CommunityEvent.SyncState.SYNCED)
        client.login(username="adm", password="pass")
        assert b"Synced to Google" not in _calendar(client)

    def it_hides_the_badge_when_the_env_master_switch_is_off(client: Client, settings):
        _admin()
        settings.GOOGLE_CALENDAR_SYNC_ENABLED = False
        config = SiteConfiguration.load()  # toggle on, env off
        config.google_calendar_sync_enabled = True
        config.save(update_fields=["google_calendar_sync_enabled"])
        CommunityEventFactory(community=True, sync_state=CommunityEvent.SyncState.SYNCED)
        client.login(username="adm", password="pass")
        assert b"Synced to Google" not in _calendar(client)

    def it_sets_the_context_flag_true_only_when_both_gates_are_on(client: Client, settings):
        _admin()
        _both_gates_on(settings)
        client.login(username="adm", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert resp.context["google_sync_enabled"] is True

    def it_sets_the_context_flag_false_when_the_toggle_is_off(client: Client, settings):
        _admin()
        settings.GOOGLE_CALENDAR_SYNC_ENABLED = True
        client.login(username="adm", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert resp.context["google_sync_enabled"] is False

    def it_hides_the_badge_from_a_plain_member(client: Client, settings):
        """The Events list is member-readable, but sync internals are staff/admin-only."""
        MembershipPlanFactory()
        User.objects.create_user(username="pm", email="pm@example.com", password="pass")
        _both_gates_on(settings)
        CommunityEventFactory(community=True, sync_state=CommunityEvent.SyncState.SYNCED)
        client.login(username="pm", password="pass")
        assert b"Synced to Google" not in _calendar(client)


@pytest.mark.django_db
def describe_badge_on_other_surfaces():
    def it_shows_on_the_guild_edit_events_section(client: Client, settings):
        user = _admin()
        guild = GuildFactory(guild_lead=user.member)
        _both_gates_on(settings)
        CommunityEventFactory(guild=guild, sync_state=CommunityEvent.SyncState.SYNCED)
        client.login(username="adm", password="pass")
        resp = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert resp.status_code == 200
        assert b"Synced to Google" in resp.content

    def it_shows_on_the_shared_event_edit_page_for_a_published_event(client: Client, settings):
        _admin()
        _both_gates_on(settings)
        event = CommunityEventFactory(community=True, sync_state=CommunityEvent.SyncState.FAILED, sync_error="boom")
        client.login(username="adm", password="pass")
        resp = client.get(reverse("hub_event_edit", args=[event.pk]))
        assert resp.status_code == 200
        assert b"Sync failed" in resp.content
        assert b"Saving also updates this event on the linked Google Calendar." in resp.content
