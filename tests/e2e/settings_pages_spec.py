"""End-to-end: the pages split from Site Settings (#922) in a real browser.

What a Django client cannot prove: an old Site Settings tab link lands on the new page after the
redirect, Admin Tools' cards open the pages, and a Manage Features Save posts the cards and the
switches together from the rendered form and comes back saying so.

Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from tests.membership.factories import MembershipPlanFactory

ADMIN_EMAIL = "settings-pages-admin@example.com"


def _sign_in_as_admin(login_via_code) -> None:
    MembershipPlanFactory()  # the login signal auto-creates the member only when a plan exists
    login_via_code(ADMIN_EMAIL)
    user = get_user_model().objects.get(username=ADMIN_EMAIL)
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])


def describe_the_pages_split_from_site_settings():
    def it_follows_an_old_tab_link_and_the_admin_tools_cards(live_server, page, login_via_code):
        _sign_in_as_admin(login_via_code)

        page.goto(f"{live_server.url}{reverse('hub_admin_site_settings')}?tab=legacy-cms")
        expect(page).to_have_url(re.compile(re.escape(reverse("hub_admin_cms")) + "$"))
        expect(page.get_by_role("heading", name="Manage CMS", level=1)).to_be_visible()

        page.goto(f"{live_server.url}{reverse('hub_admin_tools')}")
        for title, url_name in (
            ("Member Agreement", "hub_admin_member_agreement"),
            ("Manage Features", "hub_admin_features"),
            ("Automations", "hub_admin_automations"),
        ):
            page.locator(".pl-tool-card", has_text=title).first.click()
            expect(page).to_have_url(re.compile(re.escape(reverse(url_name)) + "$"))
            expect(page.get_by_role("heading", name=title, level=1)).to_be_visible()
            page.go_back()

    def it_saves_a_feature_state_and_a_switch_with_one_save(live_server, page, login_via_code):
        _sign_in_as_admin(login_via_code)
        page.goto(f"{live_server.url}{reverse('hub_admin_features')}")

        wiki = page.locator(".pl-automation-card", has_text="Member Wiki").first
        wiki.get_by_label("Hidden").check()
        help_toggle = page.locator(".pl-toggle-row", has_text="Show Help in the sidebar")
        help_toggle.locator(".pl-toggle__slider").click()
        page.get_by_role("button", name="Save", exact=True).click()

        expect(page.get_by_text("Features saved.")).to_be_visible()
        expect(page).to_have_url(re.compile(re.escape(reverse("hub_admin_features")) + "$"))
        from core.models import FeatureSwitch, SiteConfiguration

        assert FeatureSwitch.objects.get(feature_key="wiki").state == "hidden"
        assert SiteConfiguration.load().help_page_enabled is False
