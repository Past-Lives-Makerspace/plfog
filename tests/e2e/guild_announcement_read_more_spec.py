"""A guild announcement's Read more fold in a real browser (#952).

The server folds any body that might be long (``GuildAnnouncement.body_is_long``); the card's
script measures the fold and drops the toggle when the body fits after all, including when the
card first shows from a hidden tab. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import re

from django.urls import reverse
from playwright.sync_api import expect

from tests.membership.factories import GuildAnnouncementFactory, GuildFactory

CARD = "[data-guild-announcement]"
BODY = f"{CARD} [data-announcement-body]"
MORE = f"{CARD} [data-announcement-more]"

LONG_BODY = "Hello Writers!\n\n" + "Zq a line about the next meetup and what to bring along.\n\n" * 14 + "- Penina"
# Over the line limit, so the server folds it, but short enough to fit inside the fold.
FITS_BODY = "Zq a\nZq b\nZq c\nZq d\nZq e\nZq f\nZq g"


def _overflows(page) -> bool:
    return page.locator(BODY).evaluate("el => el.scrollHeight > el.clientHeight + 1")


def _open(page, live_server, body: str, *, query: str = "", width: int = 390) -> None:
    guild = GuildFactory()
    GuildAnnouncementFactory(guild=guild, body=body)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}{query}")


def describe_read_more():
    def it_folds_a_long_body_and_opens_it_whole(live_server, page):
        _open(page, live_server, LONG_BODY)

        expect(page.locator(MORE)).to_have_text("Read more")
        assert _overflows(page)

        page.locator(MORE).click()

        expect(page.locator(MORE)).to_have_text("Show less")
        expect(page.locator(MORE)).to_have_attribute("aria-expanded", "true")
        expect(page.locator(BODY)).to_contain_text("- Penina")
        assert not _overflows(page)

        page.locator(MORE).click()

        expect(page.locator(MORE)).to_have_text("Read more")
        assert _overflows(page)

    def it_hides_the_toggle_when_the_folded_body_fits(live_server, page):
        _open(page, live_server, FITS_BODY, width=1280)

        expect(page.locator(BODY)).to_have_class(re.compile(r"pl-guild-announcement__body--fits"))
        expect(page.locator(MORE)).to_be_hidden()

    def it_measures_the_fold_again_when_its_tab_shows_after_being_hidden(live_server, page):
        _open(page, live_server, LONG_BODY, width=1280)
        tabs = page.locator("nav.pl-tabs")

        tabs.get_by_role("button", name="Guild Calendar").click()
        expect(page.locator(CARD)).to_be_hidden()
        tabs.get_by_role("button", name="Overview").click()

        expect(page.locator(MORE)).to_be_visible()
        expect(page.locator(MORE)).to_have_text("Read more")
        assert _overflows(page)
