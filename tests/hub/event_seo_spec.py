"""What search engines read off an event page and a guild page (#1259)."""

from __future__ import annotations

import json
import re

import pytest
from django.test import Client

from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory, GuildFactory

pytestmark = pytest.mark.django_db

MEMBERS = "members.pastlives.space"
GUILDS = "guilds.pastlives.space"


@pytest.fixture(autouse=True)
def _hosts(settings) -> None:
    settings.ALLOWED_HOSTS = [MEMBERS, GUILDS, "testserver"]
    settings.GUILDS_HOSTS = [GUILDS]
    settings.GUILDS_BASE_URL = f"https://{GUILDS}"
    settings.MEMBER_BASE_URL = f"https://{MEMBERS}"


def _event_page(client: Client, event: CommunityEvent, query: str = "") -> str:
    response = client.get(f"/events/{event.pk}/{query}", secure=True)
    assert response.status_code == 200
    return response.content.decode()


def _json_ld(html: str) -> dict:
    return json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)[1])  # type: ignore[index]


def describe_an_event_page():
    def it_has_a_description_open_graph_and_one_canonical(client: Client):
        event = CommunityEventFactory(
            community=True, title="Open Forge Night", description="<p>Bring a <b>project</b>.</p>"
        )

        html = _event_page(client, event, "?date=2026-12-01")

        assert '<meta name="description" content="Bring a project.">' in html
        assert "<title>Open Forge Night - Past Lives</title>" in html
        assert 'property="og:title" content="Open Forge Night"' in html
        assert re.findall(r'<link rel="canonical" href="([^"]*)">', html) == [f"https://testserver/events/{event.pk}/"]

    def it_carries_event_json_ld_at_its_place(client: Client):
        event = CommunityEventFactory(community=True, title="Open Forge Night", location="Blacksmith shop")

        data = _json_ld(_event_page(client, event))

        assert data["@type"] == "Event"
        assert data["name"] == "Open Forge Night"
        assert data["startDate"] == event.starts_at.isoformat()
        assert data["endDate"] == event.ends_at.isoformat()
        assert data["location"]["name"] == "Blacksmith shop"
        assert data["location"]["address"]["postalCode"] == "97202"
        assert data["url"] == f"https://{MEMBERS}/events/{event.pk}/"

    def it_names_the_makerspace_without_a_location_and_describes_a_bare_event():
        event = CommunityEventFactory(community=True, title="Swap Meet", location="", description="")

        data = event.event_json_ld()

        assert data["location"]["name"] == "Past Lives Makerspace"
        assert event.seo_description == "Swap Meet at Past Lives Makerspace in Portland, OR."
        assert "image" not in data

    def it_gives_a_photo_its_host(monkeypatch):
        event = CommunityEventFactory(community=True)
        monkeypatch.setattr(CommunityEvent, "hero_image_url", property(lambda self: "/media/events/a.jpg"))
        assert event.event_json_ld()["image"] == [f"https://{MEMBERS}/media/events/a.jpg"]

        monkeypatch.setattr(CommunityEvent, "hero_image_url", property(lambda self: "https://cdn.example/a.jpg"))
        assert event.event_json_ld()["image"] == ["https://cdn.example/a.jpg"]

    def it_shows_the_photo_as_its_share_image(client: Client, monkeypatch):
        event = CommunityEventFactory(community=True)
        monkeypatch.setattr(CommunityEvent, "hero_image_url", property(lambda self: "/media/events/a.jpg"))

        assert 'property="og:image" content="https://testserver/media/events/a.jpg"' in _event_page(client, event)


def describe_a_guild_page_on_the_guilds_host():
    def it_has_one_description_and_one_canonical_there(client: Client):
        guild = GuildFactory(name="Wood Guild", slug="wood", about="We make chairs.")

        html = client.get("/guilds/wood/", HTTP_HOST=GUILDS).content.decode()

        assert re.findall(r'<meta name="description" content="([^"]*)">', html) == ["We make chairs."]
        assert re.findall(r'<link rel="canonical" href="([^"]*)">', html) == [f"https://{GUILDS}/guilds/{guild.slug}/"]

    def it_keeps_its_own_address_on_the_members_host(client: Client):
        GuildFactory(name="Wood Guild", slug="wood")

        html = client.get("/guilds/wood/").content.decode()

        assert re.findall(r'<link rel="canonical" href="([^"]*)">', html) == ["http://testserver/guilds/wood/"]
