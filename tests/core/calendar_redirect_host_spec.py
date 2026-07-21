"""BDD specs for the calendar.pastlives.space vanity host: every path 302s to the
community calendar on the members domain."""

from __future__ import annotations

import pytest
from django.test import Client, override_settings

pytestmark = pytest.mark.django_db

CAL_SETTINGS = dict(
    ALLOWED_HOSTS=["calendar.pastlives.space", "members.pastlives.space", "testserver"],
    CALENDAR_REDIRECT_HOSTS=["calendar.pastlives.space"],
    MEMBER_HOST="members.pastlives.space",
)


def describe_calendar_redirect_host():
    @override_settings(**CAL_SETTINGS)
    def it_redirects_the_root_to_the_community_calendar(client: Client):
        response = client.get("/", HTTP_HOST="calendar.pastlives.space")
        assert response.status_code == 302
        assert response["Location"] == "https://members.pastlives.space/calendar/?public=1"

    @override_settings(**CAL_SETTINGS)
    def it_redirects_any_path_the_same_way(client: Client):
        response = client.get("/some/deep/path/", HTTP_HOST="calendar.pastlives.space")
        assert response.status_code == 302
        assert response["Location"] == "https://members.pastlives.space/calendar/?public=1"

    @override_settings(**CAL_SETTINGS)
    def it_does_not_touch_the_members_host(client: Client):
        response = client.get("/calendar/", HTTP_HOST="members.pastlives.space")
        assert response.status_code == 200
