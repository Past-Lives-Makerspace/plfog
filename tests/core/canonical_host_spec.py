"""A public page read on the wrong host 301s to the host it belongs to (#1259, CanonicalHostMiddleware)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

CLASSES = "classes.pastlives.space"
MEMBERS = "members.pastlives.space"
GUILDS = "guilds.pastlives.space"


@pytest.fixture(autouse=True)
def _hosts(settings) -> None:
    settings.ALLOWED_HOSTS = [CLASSES, MEMBERS, GUILDS, "testserver"]
    settings.PUBLIC_HOSTS = [CLASSES]
    settings.GUILDS_HOSTS = [GUILDS]
    settings.MEMBER_HOST = MEMBERS
    settings.MEMBER_BASE_URL = f"https://{MEMBERS}"
    settings.GUILDS_BASE_URL = f"https://{GUILDS}"


def describe_the_classes_host():
    def it_sends_an_event_page_to_the_members_host_with_its_query(client: Client):
        event = CommunityEventFactory(community=True)

        response = client.get(f"/events/{event.pk}/?date=2026-12-01", HTTP_HOST=CLASSES)

        assert response.status_code == 301
        assert response["Location"] == f"https://{MEMBERS}/events/{event.pk}/?date=2026-12-01"

    @pytest.mark.parametrize("path", ["/calendar/", "/help/", "/help/search/?q=kiln"])
    def it_sends_the_calendar_and_help_to_the_members_host(client: Client, path: str):
        response = client.get(path, HTTP_HOST=CLASSES)

        assert response.status_code == 301
        assert response["Location"] == f"https://{MEMBERS}{path}"

    def it_keeps_its_own_class_pages(client: Client):
        assert client.get("/classes/", HTTP_HOST=CLASSES).status_code == 200

    def it_serves_a_form_post_where_it_lands(client: Client):
        event = CommunityEventFactory(community=True)

        response = client.post(f"/events/{event.pk}/", HTTP_HOST=CLASSES)

        assert response.status_code != 301

    def it_leaves_an_unknown_path_to_the_404(client: Client):
        assert client.get("/no-such-page/at-all/", HTTP_HOST=CLASSES).status_code == 404


def describe_the_members_host():
    def it_sends_a_signed_out_visitor_on_a_guild_page_to_the_guilds_host(client: Client):
        GuildFactory(name="Wood Guild", slug="wood")

        response = client.get("/guilds/wood/?tab=about", HTTP_HOST=MEMBERS)

        assert response.status_code == 301
        assert response["Location"] == f"https://{GUILDS}/guilds/wood/?tab=about"

    def it_keeps_that_redirect_out_of_the_browser_cache(client: Client):
        response = client.get("/guilds/", HTTP_HOST=MEMBERS)

        assert response.status_code == 301
        assert "no-store" in response["Cache-Control"]

    def it_keeps_a_signed_in_member_on_their_guild_page(client: Client):
        GuildFactory(name="Wood Guild", slug="wood")
        MembershipPlanFactory()
        client.force_login(User.objects.create_user(username="maker", password="pw", email="maker@example.com"))

        response = client.get("/guilds/wood/", HTTP_HOST=MEMBERS)

        assert response.status_code == 200

    def it_keeps_event_pages_on_the_members_host(client: Client):
        event = CommunityEventFactory(community=True)

        assert client.get(f"/events/{event.pk}/", HTTP_HOST=MEMBERS).status_code == 200

    def it_leaves_other_hosts_on_the_member_surface_alone(client: Client):
        GuildFactory(name="Wood Guild", slug="wood")

        assert client.get("/guilds/wood/").status_code == 200
