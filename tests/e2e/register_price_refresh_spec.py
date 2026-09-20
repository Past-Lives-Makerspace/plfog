"""End-to-end: the registration page quotes the price it will charge, live, in a real browser.

#369 items 2 and 3. The quote (the summary total and the submit label) is re-fetched over
HTMX whenever the email, the member-discount toggle or the code box changes, and that wiring
(``hx-include`` ordering, the checkbox's hidden twin, ``hx-select-oob``) only proves itself
in a browser. Modeled on ``login_and_book_spec.py``: the real login-by-code flow, then the
public register page of a paid class.

Run with ``pytest -m e2e`` (deselected from the default suite).
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering

MEMBER_EMAIL = "member@example.com"
GUEST_EMAIL = "guest@example.com"
LABEL = "#reg-submit-label"
TOGGLE = '#reg-price-summary input[type="checkbox"][name="apply_member_discount"]'


def _open_paid_class(live_server, page, login_via_code) -> None:
    """Sign the member in and land on the register page of a $100 class with a 10% member discount."""
    offering = ClassOfferingFactory(
        title="Forge Basics",
        slug="forge-basics",
        status=ClassOffering.Status.PUBLISHED,
        is_private=False,
        price_cents=10000,
        member_discount_pct=10,
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=7),
        ends_at=timezone.now() + timedelta(days=7, hours=2),
    )
    login_via_code(MEMBER_EMAIL)
    expect(page).not_to_have_url(re.compile(r"/accounts/"))
    page.goto(f"{live_server.url}{reverse('classes:register', kwargs={'slug': offering.slug})}")


def describe_the_live_price_quote():
    def it_quotes_the_member_price_on_first_paint(live_server, page, login_via_code):
        _open_paid_class(live_server, page, login_via_code)
        # Server-rendered for the logged-in member: no click, no refresh, the number is there.
        expect(page.locator(LABEL)).to_have_text("$90")
        expect(page.locator(TOGGLE)).to_be_checked()

    def it_swaps_the_label_as_the_toggle_is_turned_off_and_on(live_server, page, login_via_code):
        _open_paid_class(live_server, page, login_via_code)
        expect(page.locator(LABEL)).to_have_text("$90")

        page.uncheck(TOGGLE)
        expect(page.locator(LABEL)).to_have_text("$100")
        expect(page.locator(TOGGLE)).not_to_be_checked()

        page.check(TOGGLE)
        expect(page.locator(LABEL)).to_have_text("$90")
        expect(page.locator(TOGGLE)).to_be_checked()

    def it_follows_the_email_out_of_membership_and_back(live_server, page, login_via_code):
        _open_paid_class(live_server, page, login_via_code)
        expect(page.locator(LABEL)).to_have_text("$90")
        email = page.locator('input[name="email"]')

        email.fill(GUEST_EMAIL)
        email.press("Tab")
        expect(page.locator(LABEL)).to_have_text("$100")
        expect(page.locator(TOGGLE)).to_have_count(0)

        email.fill(MEMBER_EMAIL)
        email.press("Tab")
        expect(page.locator(LABEL)).to_have_text("$90")
        expect(page.locator(TOGGLE)).to_have_count(1)
        expect(page.locator(TOGGLE)).to_be_checked()
