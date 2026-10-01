"""End-to-end: the registration page quotes the price it will charge, live, in a real browser.

The quote (the summary total and the submit label) is re-fetched over HTMX whenever the
email or the code box changes, and that wiring (``hx-include``, ``hx-select-oob``) only
proves itself in a browser. A member's email changes nothing: the price is the price, and
a typed discount code is the one thing that moves it. Modeled on ``login_and_book_spec.py``:
the real login-by-code flow, then the public register page of a paid class.

Run with ``pytest -m e2e`` (deselected from the default suite).
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from classes.factories import ClassOfferingFactory, ClassSessionFactory, DiscountCodeFactory
from classes.models import ClassOffering

MEMBER_EMAIL = "member@example.com"
LABEL = "#reg-submit-label"
CODE_BOX = '#reg-form input[name="discount_code"]'


def _open_paid_class(live_server, page, login_via_code) -> None:
    """Sign the member in and land on the register page of a $100 class with a 10 percent global code."""
    offering = ClassOfferingFactory(
        title="Forge Basics",
        slug="forge-basics",
        status=ClassOffering.Status.PUBLISHED,
        is_private=False,
        price_cents=10000,
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=7),
        ends_at=timezone.now() + timedelta(days=7, hours=2),
    )
    DiscountCodeFactory(code="TENOFF", discount_pct=10, class_offering=None)
    login_via_code(MEMBER_EMAIL)
    expect(page).not_to_have_url(re.compile(r"/accounts/"))
    page.goto(f"{live_server.url}{reverse('classes:register', kwargs={'slug': offering.slug})}")


def describe_the_live_price_quote():
    def it_quotes_the_full_price_to_a_member_on_first_paint(live_server, page, login_via_code):
        _open_paid_class(live_server, page, login_via_code)
        # Server-rendered for the logged-in member: no click, no refresh, the number is there.
        expect(page.locator(LABEL)).to_have_text("$100")

    def it_swaps_the_label_as_a_code_is_typed_and_cleared(live_server, page, login_via_code):
        _open_paid_class(live_server, page, login_via_code)
        expect(page.locator(LABEL)).to_have_text("$100")
        code = page.locator(CODE_BOX)

        code.fill("TENOFF")
        code.press("Tab")
        expect(page.locator(LABEL)).to_have_text("$90")

        code.fill("")
        code.press("Tab")
        expect(page.locator(LABEL)).to_have_text("$100")
