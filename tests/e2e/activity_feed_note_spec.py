"""An Activity Feed note clamped to two lines offers More exactly when the clamp hides text, in a real browser.

The server cannot know where a note wraps, so More is measured in the page (a ResizeObserver on the
note). On a 390px phone a note of about 100 characters runs past two lines and must get More; a
short note that fits must not. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from core.models import SiteActivity
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

EMAIL = "activity-note@example.com"
LONG_NOTE = "Brought my own safety glasses and gloves; please check the vent fan before the session starts again."
SHORT_NOTE = "Glaze bucket"


def _sign_in_as_admin(login_via_code) -> None:
    MembershipPlanFactory()
    login_via_code(EMAIL)
    user = get_user_model().objects.get(username=EMAIL)
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])
    member = Member.objects.get(user=user)
    member.welcome_dismissed_at = timezone.now()
    member.guided_tours_enabled = False
    member.save(update_fields=["welcome_dismissed_at", "guided_tours_enabled"])


def describe_activity_feed_note_on_a_phone():
    @pytest.fixture
    def browser_context_args(browser_context_args):
        return {**browser_context_args, "viewport": {"width": 390, "height": 844}}

    def it_offers_more_on_a_clamped_note_and_opens_it_in_place(page, live_server, login_via_code):
        assert 90 <= len(LONG_NOTE) <= 110
        _sign_in_as_admin(login_via_code)
        SiteActivity.log(SiteActivity.Kind.TAB_ENTRY_ADDED, payload={"note": SHORT_NOTE})
        SiteActivity.log(SiteActivity.Kind.TAB_ENTRY_ADDED, payload={"note": LONG_NOTE})
        page.goto(f"{live_server.url}{reverse('manage_activity')}")

        long_note = page.locator('[data-testid="activity-note"]', has_text="safety glasses")
        short_note = page.locator('[data-testid="activity-note"]', has_text=SHORT_NOTE)
        more = long_note.locator("[data-activity-note-more]")
        expect(more).to_be_visible()
        expect(more).to_have_text("More")
        expect(short_note.locator("[data-activity-note-more]")).to_be_hidden()

        text = long_note.locator("[data-activity-note-text]")
        clamped_height = text.evaluate("el => el.clientHeight")
        more.click()

        expect(more).to_have_text("Less")
        expect(more).to_have_attribute("aria-expanded", "true")
        expect(text).to_have_class("hub-text-muted pl-activity-note__text pl-activity-note__text--full")
        assert text.evaluate("el => el.clientHeight") > clamped_height
        assert text.evaluate("el => el.scrollHeight <= el.clientHeight + 1")
