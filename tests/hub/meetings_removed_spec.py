"""The Meetings page and the meeting workspace are gone (#1171).

Old meeting URLs answer 404 for everyone, and a council meeting with its own calendar event (the
shape of production meeting 18 and event 37) keeps both rows exactly as they were, whatever is
posted at its old URLs. The Meeting tables stay until a separate follow-up drops them.
"""

from __future__ import annotations

from datetime import time, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import CommunityEvent, Meeting, Member
from tests.membership.factories import MeetingFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _admin(client: Client) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username="meetings_gone", password="pass", last_login=timezone.now())
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    client.login(username="meetings_gone", password="pass")
    return user


def _council_meeting_with_its_event(by: User) -> tuple[Meeting, CommunityEvent]:
    meeting = MeetingFactory(
        guild=None, scheduled_date=timezone.localdate() + timedelta(days=7), scheduled_time=time(18, 30)
    )
    with patch.object(CommunityEvent, "push_live"):
        event = meeting.create_calendar_event(by=by)
    meeting.refresh_from_db()
    return meeting, event


def _old_urls(pk: int) -> list[str]:
    return [
        "/meetings/",
        "/meetings/new/",
        f"/meetings/{pk}/",
        f"/meetings/{pk}/event/",
        f"/meetings/{pk}/event/unlink/",
        f"/meetings/{pk}/save/",
        f"/meetings/{pk}/delete/",
    ]


def describe_the_old_meeting_urls():
    def it_answers_404_to_a_get_and_a_post_from_an_admin(client: Client):
        meeting, _event = _council_meeting_with_its_event(_admin(client))
        for url in _old_urls(meeting.pk):
            assert client.get(url).status_code == 404, url
            assert client.post(url, {"field": "scheduled_time", "value": "19:00"}).status_code == 404, url

    def it_leaves_a_council_meeting_and_its_calendar_event_as_they_were(client: Client):
        meeting, event = _council_meeting_with_its_event(_admin(client))
        starts_at = event.starts_at
        with (
            patch.object(CommunityEvent, "remove_from_google") as from_google,
            patch.object(CommunityEvent, "remove_from_discord") as from_discord,
        ):
            for url in _old_urls(meeting.pk):
                client.post(url, {"field": "scheduled_time", "value": "19:00"})
        from_google.assert_not_called()
        from_discord.assert_not_called()
        meeting.refresh_from_db()
        event.refresh_from_db()
        assert meeting.event_id == event.pk
        assert meeting.owns_event is True
        assert meeting.scheduled_time == time(18, 30)
        assert event.starts_at == starts_at


def describe_the_sidebar():
    def it_has_no_meetings_entry(client: Client):
        _admin(client)
        response = client.get(reverse("hub_home"))
        assert response.status_code == 200
        html = response.content.decode()
        assert 'href="/meetings/"' not in html
