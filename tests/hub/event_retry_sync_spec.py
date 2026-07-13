"""BDD specs for ``event_retry_sync`` — the admin-only "Retry sync now" button that
re-pushes a single event to Google immediately instead of waiting for the 15-min cron.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from membership.models import CommunityEvent, Member
from tests.membership.factories import CommunityEventFactory, MembershipPlanFactory


def _user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _messages(response) -> list[str]:
    return [m.message for m in get_messages(response.wsgi_request)]


@pytest.mark.django_db
def describe_event_retry_sync():
    def it_lets_an_admin_repush_a_failed_event(client: Client):
        _user("adm", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True, sync_state=CommunityEvent.SyncState.FAILED, sync_error="boom")
        client.login(username="adm", password="pass")

        def _fake_push(self, *, actor=None):
            self.sync_state = CommunityEvent.SyncState.SYNCED
            self.sync_error = ""

        with patch.object(CommunityEvent, "push_to_google", autospec=True, side_effect=_fake_push) as mock_push:
            resp = client.post(reverse("hub_event_retry_sync", args=[event.pk]))

        mock_push.assert_called_once()
        assert resp.status_code == 302
        assert "Event synced to Google Calendar." in _messages(resp)

    def it_reports_a_still_failing_push(client: Client):
        _user("adm2", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True, sync_state=CommunityEvent.SyncState.FAILED)
        client.login(username="adm2", password="pass")

        def _fake_fail(self, *, actor=None):
            self.sync_state = CommunityEvent.SyncState.FAILED
            self.sync_error = "still broken"

        with patch.object(CommunityEvent, "push_to_google", autospec=True, side_effect=_fake_fail):
            resp = client.post(reverse("hub_event_retry_sync", args=[event.pk]))

        assert any("still broken" in m for m in _messages(resp))

    def it_reports_a_pending_config_gap(client: Client):
        _user("adm5", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True, sync_state=CommunityEvent.SyncState.FAILED)
        client.login(username="adm5", password="pass")

        def _fake_pending(self, *, actor=None):
            self.sync_state = CommunityEvent.SyncState.PENDING
            self.sync_error = "No Google Calendar linked for this event yet."

        with patch.object(CommunityEvent, "push_to_google", autospec=True, side_effect=_fake_pending):
            resp = client.post(reverse("hub_event_retry_sync", args=[event.pk]))
        assert any("No Google Calendar linked" in m for m in _messages(resp))

    def it_redirects_to_a_safe_next(client: Client):
        _user("adm3", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True, sync_state=CommunityEvent.SyncState.FAILED)
        client.login(username="adm3", password="pass")
        with patch.object(CommunityEvent, "push_to_google", autospec=True):
            resp = client.post(
                reverse("hub_event_retry_sync", args=[event.pk]),
                {"next": "/hub/guilds/"},
            )
        assert resp.status_code == 302
        assert resp["Location"] == "/hub/guilds/"

    def it_403s_a_non_admin(client: Client):
        _user("pm")
        event = CommunityEventFactory(community=True, sync_state=CommunityEvent.SyncState.FAILED)
        client.login(username="pm", password="pass")
        with patch.object(CommunityEvent, "push_to_google", autospec=True) as mock_push:
            resp = client.post(reverse("hub_event_retry_sync", args=[event.pk]))
        assert resp.status_code == 403
        mock_push.assert_not_called()

    def it_rejects_a_get(client: Client):
        _user("adm4", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True, sync_state=CommunityEvent.SyncState.FAILED)
        client.login(username="adm4", password="pass")
        assert client.get(reverse("hub_event_retry_sync", args=[event.pk])).status_code == 405
