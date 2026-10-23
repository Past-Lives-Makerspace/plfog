"""The announcements banner at the top of Home (#889): ``hub.home.HomeBanner`` and ``partials/_home_announcements.html``."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.factories import AnnouncementFactory
from core.models import Announcement
from hub.home import HomeBanner
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

HOME = reverse("hub_home")


@pytest.fixture
def member_client(client: Client) -> Client:
    """Signed in as a User whose auto-created Member is linked (it needs a plan first)."""
    MembershipPlanFactory()
    client.force_login(User.objects.create_user(username="zorblax-member"))
    return client


def _home(client: Client) -> str:
    return client.get(HOME).content.decode()


def _banner(html: str) -> str:
    start = html.index('<section class="pl-home-ann')
    return html[start : html.index("</section>", start)]


def describe_HomeBanner():
    def it_is_none_while_nothing_is_live():
        now = timezone.now()
        AnnouncementFactory(is_active=False)
        AnnouncementFactory(show_from=now + timedelta(hours=1))
        AnnouncementFactory(show_until=now - timedelta(hours=1))

        assert HomeBanner.load(now) is None

    def it_holds_the_live_ones_in_order_and_their_signature():
        now = timezone.now()
        second = AnnouncementFactory(position=1)
        first = AnnouncementFactory(position=0)
        AnnouncementFactory(is_active=False, position=0)

        banner = HomeBanner.load(now)

        assert banner is not None
        assert banner.items == [first, second]
        assert banner.signature == Announcement.signature([first, second])

    def it_reads_in_one_query(django_assert_num_queries):
        for _ in range(3):
            AnnouncementFactory()

        with django_assert_num_queries(1):
            banner = HomeBanner.load(timezone.now())

        assert banner is not None and len(banner.items) == 3


def describe_the_banner_on_home():
    def it_shows_nothing_while_nothing_is_live(member_client: Client):
        AnnouncementFactory(is_active=False)

        assert "data-home-announcements" not in _home(member_client)

    def it_comes_first_above_the_welcome_header(member_client: Client):
        AnnouncementFactory(body="<p>Zorblax open house</p>")

        html = _home(member_client)

        assert html.index("data-home-announcements") < html.index("data-spotlight-home")
        assert html.index("data-home-announcements") < html.index("Welcome back, ")

    def it_shows_only_the_live_ones_in_order(member_client: Client):
        now = timezone.now()
        AnnouncementFactory(body="<p>Zorblax second</p>", position=1)
        AnnouncementFactory(body="<p>Zorblax first <strong>bold</strong></p>", position=0)
        AnnouncementFactory(body="<p>Zorblax off</p>", is_active=False)
        AnnouncementFactory(body="<p>Zorblax later</p>", show_from=now + timedelta(days=1))

        banner = _banner(_home(member_client))

        assert banner.index("Zorblax first <strong>bold</strong>") < banner.index("Zorblax second")
        assert "Zorblax off" not in banner
        assert "Zorblax later" not in banner

    def it_shows_to_an_account_with_no_membership(client: Client):
        user = User.objects.create_user(username="zorblax-unlinked")
        Member.objects.filter(user=user).delete()
        client.force_login(user)
        AnnouncementFactory(body="<p>Zorblax for all</p>")

        html = _home(client)

        assert "isn't linked to a membership" in html
        assert "Zorblax for all" in _banner(html)

    def it_carries_the_signature_and_a_close_button(member_client: Client):
        announcement = AnnouncementFactory()

        banner = _banner(_home(member_client))

        assert f'data-announcement-signature="{Announcement.signature([announcement])}"' in banner
        assert 'aria-label="Close the announcements" data-announcement-close>' in banner

    def it_renders_the_sanitized_text(member_client: Client):
        announcement = AnnouncementFactory()
        Announcement.objects.filter(pk=announcement.pk).update(body='<p>Zorblax <img src="x.png"><b>hi</b></p>')

        banner = _banner(_home(member_client))

        assert '<div class="pl-ann-body pl-home-ann__body"><p>Zorblax hi</p></div>' in banner


def describe_one_and_many():
    def it_shows_one_with_no_controls_and_no_carousel(member_client: Client):
        AnnouncementFactory()

        banner = _banner(_home(member_client))

        assert 'class="pl-home-ann pl-rotator"' in banner
        assert "aria-roledescription" not in banner
        assert "data-rotator-dot" not in banner
        assert "data-rotator-pause" not in banner
        assert "data-rotator-next" not in banner

    def it_shows_many_as_a_carousel_with_its_controls(member_client: Client):
        for _ in range(3):
            AnnouncementFactory()

        banner = _banner(_home(member_client))

        assert 'class="pl-home-ann pl-rotator pl-home-ann--many"' in banner
        assert 'data-rotator-interval="8000" aria-roledescription="carousel" aria-label="Announcements"' in banner
        assert banner.count("data-rotator-dot") == 3
        assert 'aria-label="Show announcement 2 of 3"' in banner
        assert 'aria-label="Next announcement"' in banner
        assert "data-rotator-pause" in banner
        assert 'role="group" aria-roledescription="slide" aria-label="3 of 3"' in banner
        assert "aria-live" not in banner


def describe_the_query_budget():
    def _count(client: Client) -> int:
        with CaptureQueriesContext(connection) as captured:
            assert client.get(HOME).status_code == 200
        return len(captured)

    def it_costs_home_one_query_however_many_are_live(member_client: Client):
        member_client.get(HOME)
        without_banner_read = None
        with patch("hub.home.HomeBanner.load", return_value=None):
            without_banner_read = _count(member_client)
        none_live = _count(member_client)
        for _ in range(3):
            AnnouncementFactory()
        three_live = _count(member_client)

        assert none_live == without_banner_read + 1
        assert three_live == none_live
