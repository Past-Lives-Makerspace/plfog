"""BDD specs for the signage player views (core.views.signage_player / signage_deck).

The sharpest test is AUTH-PARITY: on .pastlives.space the session cookie means an admin
can arrive at the kiosk authenticated, so the player must render byte-identical PUBLIC
content regardless of request.user — no member names, no PII, no per-user chrome.
"""

from __future__ import annotations

import pytest
from django.test import Client, override_settings

from tests.membership.factories import (
    MembershipPlanFactory,
    SlideshowSlideFactory,
    SlideshowZoneFactory,
)

pytestmark = pytest.mark.django_db

SIGNAGE_HOST = "slideshow.pastlives.space"
MEMBERS_HOST = "members.pastlives.space"


@pytest.fixture(autouse=True)
def _signage_env():
    with override_settings(
        ALLOWED_HOSTS=[SIGNAGE_HOST, MEMBERS_HOST, "testserver"],
        SIGNAGE_HOSTS=[SIGNAGE_HOST],
        SIGNAGE_BASE_URL="https://slideshow.pastlives.space",
        MEMBER_HOST=MEMBERS_HOST,
        PUBLIC_HOSTS=["book.pastlives.space"],
    ):
        yield


def describe_signage_player():
    def describe_auth_parity():
        def it_renders_identical_public_content_authed_and_anon(client, django_user_model):
            MembershipPlanFactory()  # so member-provisioning signals don't error
            SlideshowZoneFactory(slug="woodshop")
            SlideshowSlideFactory(title="A tip about the space", zone=None)

            anon = client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST)

            admin = django_user_model.objects.create_superuser("kioskadmin", "kioskadmin@example.com", "p")
            authed_client = Client()
            authed_client.force_login(admin)
            authed = authed_client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST)

            assert anon.status_code == 200
            assert authed.status_code == 200
            assert anon.content == authed.content
            assert b"kioskadmin@example.com" not in authed.content
            assert b"Admin View" not in authed.content

    def describe_surface_guard():
        def it_404s_the_player_on_the_members_host(client):
            SlideshowZoneFactory(slug="woodshop")
            resp = client.get("/woodshop/", HTTP_HOST=MEMBERS_HOST)
            assert resp.status_code == 404

    def describe_zone_routing():
        def it_renders_an_enabled_zone(client):
            SlideshowZoneFactory(slug="woodshop", is_enabled=True)
            resp = client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST)
            assert resp.status_code == 200

        def it_404s_a_disabled_zone(client):
            SlideshowZoneFactory(slug="woodshop", is_enabled=False)
            resp = client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST)
            assert resp.status_code == 404

        def it_404s_an_unknown_zone(client):
            resp = client.get("/nope/", HTTP_HOST=SIGNAGE_HOST)
            assert resp.status_code == 404

    def describe_empty_state():
        def it_shows_the_branded_holding_slide_when_nothing_is_configured(client):
            from core.models import SiteConfiguration

            config = SiteConfiguration.load()
            config.signage_show_events = False
            config.save()
            SlideshowZoneFactory(slug="woodshop")
            resp = client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST)
            assert resp.status_code == 200
            assert b"Past Lives Makerspace" in resp.content
            assert b"pl-sign-slide--holding" in resp.content

    def describe_emergency_takeover():
        def it_shows_only_the_alert_when_active(client):
            from core.models import SiteConfiguration

            config = SiteConfiguration.load()
            config.signage_alert_active = True
            config.signage_alert_heading = "Building Closed"
            config.signage_alert_message = "We are closed today for maintenance."
            config.save()
            SlideshowZoneFactory(slug="woodshop")
            SlideshowSlideFactory(title="Should Not Show", zone=None)

            resp = client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST)
            body = resp.content.decode()
            assert "Building Closed" in body
            assert "We are closed today for maintenance." in body
            assert "pl-sign-alert" in body
            assert "Should Not Show" not in body  # rotation suppressed

    def describe_kiosk_chrome():
        def it_registers_no_service_worker_no_manifest_no_beta(client):
            SlideshowZoneFactory(slug="woodshop")
            body = client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST).content.decode()
            assert "serviceWorker.register" not in body
            assert 'rel="manifest"' not in body
            assert "BETA" not in body

    def describe_first_paint():
        def it_marks_the_first_rotating_slide_active_server_side(client):
            from core.models import SiteConfiguration

            config = SiteConfiguration.load()
            config.signage_show_events = False
            config.save()
            SlideshowZoneFactory(slug="woodshop")
            SlideshowSlideFactory(title="First", zone=None)
            body = client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST).content.decode()
            assert "is-active" in body  # slide 1 shows before Alpine boots


def describe_signage_deck():
    def it_returns_204_and_skips_the_swap_for_an_unchanged_deck(client):
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.signage_show_events = False
        config.save()
        SlideshowZoneFactory(slug="woodshop")
        SlideshowSlideFactory(title="Tip", zone=None)

        player = client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST)
        current = player.context["deck_hash"]
        resp = client.get(f"/woodshop/deck/?h={current}", HTTP_HOST=SIGNAGE_HOST)
        assert resp.status_code == 204
        assert resp["HX-Reswap"] == "none"

    def it_renders_a_fresh_deck_with_a_new_hash_for_a_stale_poll(client):
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.signage_show_events = False
        config.save()
        SlideshowZoneFactory(slug="woodshop")
        SlideshowSlideFactory(title="Tip", zone=None)

        resp = client.get("/woodshop/deck/?h=STALE-HASH", HTTP_HOST=SIGNAGE_HOST)
        assert resp.status_code == 200
        assert b"data-deck-hash" in resp.content
        assert b"Tip" in resp.content

    def it_404s_the_deck_on_the_members_host(client):
        SlideshowZoneFactory(slug="woodshop")
        resp = client.get("/woodshop/deck/", HTTP_HOST=MEMBERS_HOST)
        assert resp.status_code == 404
