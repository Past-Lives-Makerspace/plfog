"""End-to-end: Draft announcement on the Voting page opens the month's results in the composer.

The banner's Draft announcement is a boosted POST form, so the arrival at the composer is an
htmx body swap that has to leave Quill mounted with the pre-filled prose. Preview & send then
fires the email preview, whose one HTMX response also swaps the Discord preview card out of
band. The Python suite renders each piece; only a browser proves the click lands, the editor
holds the message, the iframe shows the chart and the card shows the bars. Run with
``pytest -m e2e``.
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from core.models import SiteConfiguration
from tests.membership.factories import FundingSnapshotFactory, MembershipPlanFactory

ADMIN_EMAIL = "results-admin@example.com"
TITLE = "September 2026 Voting Results"
EDITOR = '.pl-rte[data-rte-for="id_body"] .ql-editor'


def _seed_snapshot():
    # A plan must exist so the login signal auto-creates the admin's member.
    MembershipPlanFactory()
    # #general-chat must be configured for the composer to offer it, as it is in production.
    config = SiteConfiguration.load()
    config.discord_general_webhook_url = "https://discord.test/api/webhooks/general"
    config.save()
    return FundingSnapshotFactory(
        cycle_label="September 2026",
        funding_pool=Decimal("1000.00"),
        results={
            "votes_cast": 12,
            "results": [
                {"guild_name": "Metal Guild", "funding": "600.00", "share_pct": 60.0},
                {"guild_name": "Fiber Arts", "funding": "400.00", "share_pct": 40.0},
            ],
        },
    )


def _sign_in_as_admin(login_via_code) -> None:
    login_via_code(ADMIN_EMAIL)
    user = get_user_model().objects.get(username=ADMIN_EMAIL)
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])


def describe_the_results_announcement():
    def it_opens_the_prefilled_draft_and_previews_the_chart_and_the_discord_bars(live_server, page, login_via_code):
        snapshot = _seed_snapshot()
        _sign_in_as_admin(login_via_code)

        # The Overview banner offers the results as a draft announcement.
        page.goto(f"{live_server.url}{reverse('hub_admin_voting_overview')}")
        banner = page.locator(f'[data-results-banner="{snapshot.pk}"]')
        expect(banner).to_be_visible()
        banner.locator("form[data-results-draft-form] button[type=submit]").click()

        # The composer opens on the new draft, with the prose already in the Quill editor.
        page.wait_for_url(re.compile(r"/announcements/compose/\d+/$"))
        editor = page.locator(EDITOR)
        expect(editor).to_contain_text("The votes for September 2026 are in.")
        expect(editor).to_contain_text("12 members voted on how the $1,000.00 guild funding pool is split.")
        expect(editor.locator("a")).to_have_text("voting results page")
        # The pre-filled phone line survives the composer's body-to-push auto-fill.
        expect(page.locator("#id_push_message")).to_have_value(
            "September 2026 voting results are in. See how the guild funding was split."
        )

        # Preview & send: the push title, the email preview with its chart, the Discord card.
        page.get_by_role("tab", name="2. Preview & send").click()
        expect(page.locator("#pl-push-preview-title")).to_have_text(TITLE)
        expect(page.locator("#compose-preview .pl-email-preview__subject")).to_have_text(TITLE)
        email = page.frame_locator("#compose-preview iframe").locator("body")
        expect(email).to_contain_text("How the $1,000.00 funding pool was split")
        expect(email).to_contain_text("Metal Guild")
        expect(email).to_contain_text("$600.00")

        expect(page.locator("[data-discord-preview-title]")).to_have_text(TITLE)
        card = page.locator("[data-discord-preview-description]")
        expect(card).to_contain_text("How the $1,000.00 funding pool was split")
        expect(card).to_contain_text("Metal Guild: $600.00 (60.0%)")
        bars = card.locator("code.pl-discord-preview__code")
        expect(bars).to_have_count(2)
        expect(bars.first).to_have_text("█" * 12)
        expect(bars.nth(1)).to_have_text("█" * 8 + "░" * 4)
