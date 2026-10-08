"""End-to-end: a hub page opened at ``#changelog-<slug>`` shows that entry (#693, #699).

The "it's live" notice links to ``/home/#changelog-<slug>``. Since #699 the hub's modal leads
with the Building with you panel and keeps the full list behind See every update, so the
link has to open the modal, reveal the full list and land on the entry, not scroll to a
hidden one. Waits are on what the page shows. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from django.urls import reverse
from playwright.sync_api import expect

from plfog.version import CHANGELOG
from tests.membership.factories import MembershipPlanFactory

EMAIL = "changelog-deep-link@example.com"


def _deep_entry_slug() -> str:
    """A slugged entry past the panel's three, so reaching it needs the full list and a scroll."""
    return [entry["slug"] for entry in CHANGELOG if entry.get("slug")][5]


def describe_a_changelog_deep_link_on_the_hub():
    def it_opens_the_full_list_at_that_entry(page, live_server, login_via_code):
        MembershipPlanFactory()
        login_via_code(EMAIL)
        slug = _deep_entry_slug()

        # Sign in leaves the browser on /home/; a hash alone would not reload the page, and the
        # notice's link arrives as a fresh load, so start from a blank page.
        page.goto("about:blank")
        page.goto(f"{live_server.url}{reverse('hub_home')}#changelog-{slug}")

        expect(page.locator("#changelog-modal")).to_be_visible()
        expect(page.locator("#changelog-all")).to_be_visible()
        entry = page.locator(f"#changelog-{slug}")
        expect(entry).to_be_visible()
        expect(entry).to_be_in_viewport()
        expect(page.locator("#bwy-see-all")).to_be_hidden()

    def it_keeps_the_list_folded_without_a_deep_link(page, live_server, login_via_code):
        MembershipPlanFactory()
        login_via_code(EMAIL)

        page.goto(f"{live_server.url}{reverse('hub_home')}")
        page.locator("#version-pill").click()

        expect(page.locator("#building-with-you")).to_be_visible()
        expect(page.locator("#changelog-all")).to_be_hidden()
        page.locator("#bwy-see-all").click()
        expect(page.locator("#changelog-all")).to_be_visible()
