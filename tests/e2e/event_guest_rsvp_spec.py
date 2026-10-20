"""End-to-end: an anonymous visitor's Count me in puts them on the list and bumps the count (#831, part 4).

The Django-client specs prove the view; this drives the page a flyer leads to, in a real
browser with htmx boosting the body: the guest form posts, the redirect carries ``?going=1``,
the "You're on the list" state renders and the people count on the page reads the party size.
Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from tests.membership.factories import CommunityEventFactory

# htmx stamps the body once it has processed it; before that a submit is a plain navigation
# and the spec would pass for the wrong reason.
BODY_PROCESSED = "() => !!(document.body['htmx-internal-data'] && document.body['htmx-internal-data'].initHash)"


@pytest.fixture
def browser_context_args(browser_context_args):
    """Block the service worker: it fetches a form navigation itself, past ``page.route``."""
    return {**browser_context_args, "service_workers": "block"}


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before teardown truncates (see class_composer_draft_spec)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def describe_count_me_in():
    def it_adds_an_anonymous_guest_and_bumps_the_count(live_server, page):
        event = CommunityEventFactory(community=True, title="Holiday Bizarre")
        page.goto(live_server.url + reverse("hub_event_detail", args=[event.pk]))
        page.wait_for_function(BODY_PROCESSED)
        expect(page.locator("[data-people-count]")).to_have_attribute("data-people-count", "0")
        form = page.locator("[data-guest-rsvp-form]")
        expect(form).to_be_visible()
        form.locator('input[name="name"]').fill("Lee Organiser")
        form.locator('input[name="email"]').fill("Lee@Example.com")
        form.locator('select[name="party_size"]').select_option("3")
        form.locator("[data-rsvp-count-me-in]").click()

        expect(page.locator("[data-rsvp-going]")).to_be_visible()
        expect(page.locator("[data-people-count]")).to_have_attribute("data-people-count", "3")
        assert "going=1" in page.url
        expect(page.locator("[data-guest-rsvp-form]")).to_have_count(0)
        rsvp = event.rsvps.get()
        assert rsvp.member is None
        assert (rsvp.email, rsvp.party_size) == ("lee@example.com", 3)
