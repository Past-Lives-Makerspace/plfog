"""The Spotlight messages ticker at the top of Home (#889, #935): ``hub.home.HomeBanner`` and ``partials/_home_announcements.html``."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.factories import AnnouncementFactory
from core.models import Announcement, SiteConfiguration
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


def _ticker(html: str) -> str:
    start = html.index('<section class="pl-ticker"')
    return html[start : html.index("</section>", start)]


def _list(ticker: str) -> str:
    return ticker.split("data-ticker-list>", 1)[1].split("</ul>", 1)[0]


def _copy(ticker: str) -> str:
    return ticker.split("data-ticker-copy>", 1)[1].split("</ul>", 1)[0]


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


def describe_inline_html():
    def it_keeps_bold_italic_and_links_on_one_line():
        announcement = AnnouncementFactory()
        Announcement.objects.filter(pk=announcement.pk).update(
            body='<p>Zorblax <strong>bold</strong> <em>soft</em></p><p>then <a href="https://example.org/z">a link</a><br>and more</p>'
        )
        announcement.refresh_from_db()

        assert announcement.inline_html == (
            'Zorblax <strong>bold</strong> <em>soft</em> then <a href="https://example.org/z" rel="noopener noreferrer"'
            ' target="_blank">a link</a> and more'
        )

    def it_has_no_block_tags_left():
        announcement = AnnouncementFactory(body="<p>One</p><p>Two</p>")

        assert announcement.inline_html == "One Two"

    def it_drops_tags_outside_the_allowlist():
        announcement = AnnouncementFactory()
        Announcement.objects.filter(pk=announcement.pk).update(body='<p>Zorblax <img src="x.png"><b>hi</b></p>')
        announcement.refresh_from_db()

        assert announcement.inline_html == "Zorblax hi"


def describe_the_ticker_on_home():
    def it_shows_nothing_while_nothing_is_live(member_client: Client):
        AnnouncementFactory(is_active=False)

        html = _home(member_client)

        assert "data-home-announcements" not in html
        assert "pl-ticker" not in html

    def it_comes_first_above_the_welcome_header(member_client: Client):
        AnnouncementFactory(body="<p>Zorblax open house</p>")

        html = _home(member_client).split('id="main-content"', 1)[1]

        assert html.index("data-home-announcements") < html.index("Welcome back, ")

    def it_is_a_labelled_region_with_the_megaphone_and_the_close_button(member_client: Client):
        announcement = AnnouncementFactory()

        ticker = _ticker(_home(member_client))

        assert ticker.startswith('<section class="pl-ticker" aria-label="Spotlight messages" data-home-announcements')
        assert f'data-announcement-signature="{Announcement.signature([announcement])}"' in ticker
        assert '<span class="pl-ticker__icon" aria-hidden="true">' in ticker
        assert 'aria-label="Close the Spotlight messages" data-announcement-close>' in ticker
        assert (
            ticker.index("pl-ticker__icon")
            < ticker.index("data-ticker-viewport")
            < ticker.index("data-announcement-close")
        )

    def it_runs_the_live_ones_in_order_with_a_dot_after_each(member_client: Client):
        now = timezone.now()
        second = AnnouncementFactory(body="<p>Zorblax second</p>", position=1)
        first = AnnouncementFactory(body="<p>Zorblax first <strong>bold</strong></p>", position=0)
        AnnouncementFactory(body="<p>Zorblax off</p>", is_active=False)
        AnnouncementFactory(body="<p>Zorblax later</p>", show_from=now + timedelta(days=1))

        ticker = _ticker(_home(member_client))
        items = _list(ticker)

        assert items.index(f'data-announcement="{first.pk}"') < items.index(f'data-announcement="{second.pk}"')
        assert '<span class="pl-ann-body pl-ticker__text">Zorblax first <strong>bold</strong></span>' in items
        assert items.count('<span class="pl-ticker__sep" aria-hidden="true"></span>') == 2
        assert "Zorblax off" not in ticker
        assert "Zorblax later" not in ticker
        assert 'data-announcement-count="2"' in ticker

    def it_repeats_the_list_once_hidden_from_screen_readers_and_focus(member_client: Client):
        AnnouncementFactory(body='<p>Zorblax <a href="https://example.org/zorblax">link</a></p>', position=0)
        AnnouncementFactory(body="<p>Zorblax two</p>", position=1)

        ticker = _ticker(_home(member_client))

        assert '<ul class="pl-ticker__list pl-ticker__list--copy" aria-hidden="true" inert data-ticker-copy>' in ticker
        copy = _copy(ticker)
        assert copy.count('class="pl-ticker__item"') == 2
        assert "data-announcement=" not in copy
        assert 'href="https://example.org/zorblax"' in copy
        assert ticker.count("Zorblax two") == 2

    def it_shows_to_an_account_with_no_membership(client: Client):
        user = User.objects.create_user(username="zorblax-unlinked")
        Member.objects.filter(user=user).delete()
        client.force_login(user)
        AnnouncementFactory(body="<p>Zorblax for all</p>")

        html = _home(client)

        assert "isn't linked to a membership" in html
        assert "Zorblax for all" in _ticker(html)

    def it_keeps_off_every_other_hub_page(member_client: Client):
        AnnouncementFactory(body="<p>Zorblax home only</p>")

        html = member_client.get(reverse("hub_beta_feedback")).content.decode()

        assert "data-home-announcements" not in html
        assert "Zorblax home only" not in html

    def it_paces_and_closes_from_the_script_after_it(member_client: Client):
        AnnouncementFactory()

        after = _home(member_client).split("data-announcement-close>", 1)[1]

        assert 'var KEY = "pl-home-announcements-closed";' in after
        assert "window.plTicker.pace(ticker);" in after
        assert 'Number(ticker.getAttribute("data-ticker-speed"))' in after
        assert '"--pl-ticker-duration"' in after
        assert '"--pl-ticker-span"' in after
        assert "PIXELS_PER_SECOND" not in after


def describe_the_crawl_speed():
    def it_crawls_at_25_px_a_second_with_nothing_saved(member_client: Client):
        AnnouncementFactory()

        assert SiteConfiguration.load().spotlight_crawl_speed == 25
        assert 'data-ticker-speed="25"' in _ticker(_home(member_client))

    def it_crawls_at_the_saved_speed_for_every_viewer(member_client: Client):
        AnnouncementFactory()
        config = SiteConfiguration.load()
        config.spotlight_crawl_speed = 60
        config.save(update_fields=["spotlight_crawl_speed"])

        assert 'data-ticker-speed="60"' in _ticker(_home(member_client))
        # A viewer with no membership sees the same ticker at the same speed.
        member_client.force_login(User.objects.create_user(username="zorblax-unlinked"))
        assert 'data-ticker-speed="60"' in _ticker(_home(member_client))


def describe_the_query_budget():
    def _announcement_reads(client: Client) -> int:
        with CaptureQueriesContext(connection) as captured:
            assert client.get(HOME).status_code == 200
        return sum('"core_announcement"' in query["sql"] for query in captured.captured_queries)

    def it_reads_the_live_messages_once_on_home(member_client: Client):
        for _ in range(3):
            AnnouncementFactory()

        assert _announcement_reads(member_client) == 1

    def it_reads_them_once_with_none_live(member_client: Client):
        assert _announcement_reads(member_client) == 1

    def it_reads_the_crawl_speed_with_no_query_of_its_own(member_client: Client):
        """The speed rides on the feature_flags processor's load of the row, so the ticker adds no read of it."""
        AnnouncementFactory()
        member_client.get(HOME)  # The row exists from here on, so both runs below read it the same way.

        def _config_reads() -> int:
            with CaptureQueriesContext(connection) as captured:
                assert member_client.get(HOME).status_code == 200
            return sum('"core_siteconfiguration"' in query["sql"] for query in captured.captured_queries)

        with_ticker = _config_reads()
        Announcement.objects.all().delete()
        assert _config_reads() == with_ticker
