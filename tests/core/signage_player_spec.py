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

# Every self-building block ships default ON, so a spec that wants a deck of exactly
# what it configured has to switch them all off first.
_GENERATED_FLAGS = (
    "signage_show_events",
    "signage_show_classes",
    "signage_show_guilds",
    "signage_show_calendar",
    "signage_show_voting",
    "signage_show_directory",
    "signage_show_teach",
    "signage_show_tour",
)


def _no_generated_blocks(**overrides):
    """Turn every automatic slide block off (then apply overrides), and return the config."""
    from core.models import SiteConfiguration

    config = SiteConfiguration.load()
    for flag in _GENERATED_FLAGS:
        setattr(config, flag, False)
    for name, value in overrides.items():
        setattr(config, name, value)
    config.save()
    return config


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
            _no_generated_blocks()
            SlideshowZoneFactory(slug="woodshop")
            resp = client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST)
            assert resp.status_code == 200
            assert b"Past Lives Makerspace" in resp.content
            assert b"pl-sign-slide--holding" in resp.content

    def describe_kiosk_chrome():
        def it_registers_no_service_worker_no_manifest_no_beta(client):
            SlideshowZoneFactory(slug="woodshop")
            body = client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST).content.decode()
            assert "serviceWorker.register" not in body
            assert 'rel="manifest"' not in body
            assert "BETA" not in body

    def describe_first_paint():
        def it_marks_the_first_rotating_slide_active_server_side(client):
            _no_generated_blocks()
            SlideshowZoneFactory(slug="woodshop")
            SlideshowSlideFactory(title="First", zone=None)
            body = client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST).content.decode()
            assert "is-active" in body  # slide 1 shows before Alpine boots


def describe_signage_deck():
    def it_returns_204_and_skips_the_swap_for_an_unchanged_deck(client):
        _no_generated_blocks()
        SlideshowZoneFactory(slug="woodshop")
        SlideshowSlideFactory(title="Tip", zone=None)

        player = client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST)
        current = player.context["deck_hash"]
        resp = client.get(f"/woodshop/deck/?h={current}", HTTP_HOST=SIGNAGE_HOST)
        assert resp.status_code == 204
        assert resp["HX-Reswap"] == "none"

    def it_renders_a_fresh_deck_with_a_new_hash_for_a_stale_poll(client):
        _no_generated_blocks()
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


def describe_generated_slide_rendering():
    def it_gives_every_slide_its_kind_class(client):
        _no_generated_blocks(signage_show_directory=True)
        SlideshowZoneFactory(slug="woodshop")
        SlideshowSlideFactory(title="Tip", zone=None)
        body = client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST).content.decode()
        # Per-kind styling is only possible if the class is emitted for EVERY slide.
        assert "pl-sign-slide--custom" in body
        assert "pl-sign-slide--directory" in body

    def it_renders_the_month_grid_on_the_calendar_slide(client):
        from datetime import datetime, time, timedelta

        from django.utils import timezone

        from tests.membership.factories import CommunityEventFactory

        _no_generated_blocks(signage_show_calendar=True)
        SlideshowZoneFactory(slug="woodshop")
        today = timezone.localdate()
        start = timezone.make_aware(datetime.combine(today, time(hour=13)))
        event = CommunityEventFactory(
            community=True,
            title="Potluck And Shop Tour",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
        )

        body = client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST).content.decode()
        assert "pl-sign-calendar" in body
        assert "pl-sign-calendar__dow" in body
        assert "pl-sign-calendar__day--today" in body
        assert "pl-sign-calendar__day--has-events" in body
        assert "pl-sign-calendar__dot" in body
        # Whole weeks: every row is seven cells, padding included.
        cells = body.count('<span class="pl-sign-calendar__day')
        assert cells >= 28 and cells % 7 == 0
        # A dot, never a title — a busy day must not name what is on it. Asserted against
        # the event's REAL title; a generic word here would pass no matter what rendered.
        assert event.title not in body

    def it_bounds_a_six_week_month_so_the_grid_cannot_overflow_the_stage(client, settings):
        # Cells are square, so a sixth week is a whole extra row of height and the stage is
        # overflow:hidden. Only six-week months get the vh cap; five-week months keep their
        # full size. November 2026 is the next six-week month, September 2026 a five-week one.
        import datetime as dt
        from unittest.mock import patch

        _no_generated_blocks(signage_show_calendar=True)
        SlideshowZoneFactory(slug="woodshop")

        def _body_in(moment):
            with patch("django.utils.timezone.now", return_value=moment):
                return client.get("/woodshop/", HTTP_HOST=SIGNAGE_HOST).content.decode()

        six_week = _body_in(dt.datetime(2026, 11, 12, 20, 0, tzinfo=dt.UTC))
        assert "pl-sign-calendar--six-weeks" in six_week
        assert six_week.count('<span class="pl-sign-calendar__day') == 42

        five_week = _body_in(dt.datetime(2026, 9, 12, 20, 0, tzinfo=dt.UTC))
        assert "pl-sign-calendar--six-weeks" not in five_week
        assert five_week.count('<span class="pl-sign-calendar__day') == 35
