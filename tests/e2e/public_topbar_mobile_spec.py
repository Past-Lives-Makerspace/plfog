"""End-to-end: the shared public top bar survives phone width, and the token
review page carries a header with no navigation at all.

Two separate things are under test here and they fail for different reasons.

The **shared bar** (``.pl-public-topbar`` in ``hub/base.html``) is the fallback
chrome for every template that extends that base without overriding
``public_topbar`` — 70-odd of them, including ``404.html`` and the QR-code event
page. It had no phone breakpoint of any kind: the authenticated five-item row
needs roughly 790px laid out, so on a 390px phone the brand wrapped to three
lines and spilled out of its own 56px header. That is the bug the ticket
reported, photographed on an iPhone.

The **review page** additionally should not be offering a public navbar at all.
It tells the reader "token review, no login needed" and then shows them Sign up
and Log in, so it overrides the block with a brand-only minimal variant.

Three things about these assertions are deliberate and were each arrived at by
measuring ``main`` in a real browser before writing them:

- **Brand height against 1.5x its line-height**, not "brand box inside header
  box". At 480px on the unfixed CSS the brand is 51.2px inside a 56px header, so
  a visible two-line wrap passes the containment check. Nor can the lines be
  counted with ``getClientRects()``: it returns 1 even at three visual lines,
  because the label is a blockified flex item.
- **320px carries the horizontal-overflow check.** At 390px
  ``scrollWidth === clientWidth`` passes on the unfixed CSS (390 === 390) — the
  brand shrinks to min-content rather than pushing the document wider — so 390
  alone proves nothing. At 320 it genuinely fails (354 vs 320).
- **The 404 case is authenticated as a member persona.** ``__ext`` and
  ``__pill`` render only for ``persona == "member"``, which needs an ACTIVE
  member with an ``airtable_record_id``. Without that fixture the row under test
  is the anonymous three-item one, which nearly fits already, and the whole
  suite would pass on a build with no CSS changes in it.

Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest
from django.urls import reverse

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering

# 320 is the width the horizontal-overflow assertion is real at; 390 is the
# reporter's iPhone and the width the clipping was photographed at; 768 is iPad
# portrait, inside the 641-796px band a 640px breakpoint would have left broken.
WIDTHS = [320, 390, 768]
VIEWPORT_HEIGHT = 844

NO_H_SCROLL = "() => document.documentElement.scrollWidth === document.documentElement.clientWidth"

# The brand's own box height against its own computed line-height. One line
# passes; the three-line wrap the ticket photographed (76.8px against a 25.6px
# line-height) does not.
BRAND_FITS_ONE_LINE = """
() => {
    const el = document.querySelector('.pl-public-topbar__brand');
    if (!el) { return {found: false}; }
    const lineHeight = parseFloat(getComputedStyle(el).lineHeight);
    return {found: true, height: el.getBoundingClientRect().height, lineHeight: lineHeight};
}
"""

# The truncation guard must be a guard, not the mechanism. If the label is
# actually ellipsised at a supported width then the brand gave way instead of
# the nav, which is the failure this fix reverses.
LABEL_NOT_ELLIPSISED = """
() => {
    const el = document.querySelector('.pl-public-topbar__label');
    if (!el) { return {found: false}; }
    return {found: true, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth};
}
"""

MEMBER_EMAIL = "public-topbar-member@example.com"

# Markup that must not appear in the review page's minimal header. Each of these
# is a real element in the shared bar's authenticated or anonymous branch.
NAV_MARKERS = [
    "pl-public-topbar__nav",
    "pl-public-topbar__link",
    "pl-public-topbar__signup",
    "pl-public-topbar__login",
    "pl-public-topbar__logout",
    "pl-public-topbar__ext",
    "pl-public-topbar__pill",
]


def _review_token() -> str:
    """A class sitting in review, and the token its approval row carries."""
    offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
    (row,) = offering.submit_for_review()
    return row.token


def _assert_brand_fits_one_line(page, label: str, width: int) -> None:
    result = page.evaluate(BRAND_FITS_ONE_LINE)
    assert result["found"], f"{label}: no .pl-public-topbar__brand rendered at {width}px"
    assert result["height"] < 1.5 * result["lineHeight"], (
        f"{label}: brand wraps at {width}px — box is {result['height']}px "
        f"against a {result['lineHeight']}px line-height"
    )


def _assert_label_not_ellipsised(page, label: str, width: int) -> None:
    result = page.evaluate(LABEL_NOT_ELLIPSISED)
    assert result["found"], f"{label}: no .pl-public-topbar__label rendered at {width}px"
    assert result["scrollWidth"] <= result["clientWidth"], (
        f"{label}: brand label is truncated at {width}px — "
        f"{result['scrollWidth']}px of content in a {result['clientWidth']}px box"
    )


def describe_public_topbar_on_a_phone():
    def describe_the_token_review_page():
        @pytest.mark.parametrize("width", WIDTHS)
        def it_fits_its_header_on_a_phone(live_server, page, db, width):
            token = _review_token()
            page.set_viewport_size({"width": width, "height": VIEWPORT_HEIGHT})
            page.goto(f"{live_server.url}{reverse('classes:class_review', kwargs={'token': token})}")

            _assert_brand_fits_one_line(page, "review page", width)
            _assert_label_not_ellipsised(page, "review page", width)
            assert page.evaluate(NO_H_SCROLL), f"review page scrolls sideways at {width}px"

        def it_carries_a_brand_only_header_with_no_navigation(live_server, page, db):
            token = _review_token()
            page.set_viewport_size({"width": 390, "height": VIEWPORT_HEIGHT})
            page.goto(f"{live_server.url}{reverse('classes:class_review', kwargs={'token': token})}")

            assert page.locator(".pl-public-topbar--minimal").count() == 1
            for marker in NAV_MARKERS:
                assert page.locator(f".{marker}").count() == 0, f"review header still renders .{marker}"

        def it_carries_the_same_header_on_an_unknown_token(live_server, page, db):
            page.set_viewport_size({"width": 390, "height": VIEWPORT_HEIGHT})
            page.goto(f"{live_server.url}{reverse('classes:class_review', kwargs={'token': 'not-a-real-token'})}")

            assert page.locator(".pl-public-topbar--minimal").count() == 1
            for marker in NAV_MARKERS:
                assert page.locator(f".{marker}").count() == 0, f"unknown-token header still renders .{marker}"
            _assert_brand_fits_one_line(page, "unknown-token page", 390)

    def describe_the_shared_fallback_bar():
        """The 404 page, which overrides nothing, so it renders the shared bar itself.

        This is the case that proves the CSS repair rather than the template
        override: hide the nav on the review page and every other assertion in
        this file goes green over a diff with no CSS in it at all.
        """

        @pytest.mark.parametrize("width", WIDTHS)
        def it_fits_the_full_member_row_on_a_phone(live_server, page, settings, login_via_code, db, width):
            from django.contrib.auth import get_user_model

            from membership.models import Member, MembershipPlan

            plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
            page.set_viewport_size({"width": width, "height": VIEWPORT_HEIGHT})
            login_via_code(MEMBER_EMAIL)
            user = get_user_model().objects.get(username=MEMBER_EMAIL)

            # persona == "member" needs an ACTIVE member carrying an Airtable id;
            # only then do __ext and __pill render and the five-item row is the
            # row actually under test.
            Member.objects.update_or_create(
                user=user,
                defaults={
                    "full_legal_name": "Fog Member",
                    "fog_role": Member.FogRole.MEMBER,
                    "membership_plan": plan,
                    "status": Member.Status.ACTIVE,
                    "airtable_record_id": "recFOG357",
                },
            )
            # The guest surface is what renders .pl-public-topbar for a signed-in
            # user; on the members surface they would get .pl-topbar instead.
            settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]

            page.goto(f"{live_server.url}/no-such-page-357/")
            assert page.locator(".pl-public-topbar__ext").count() == 1, (
                "the member persona did not render — this is the anonymous row, which proves nothing"
            )

            _assert_brand_fits_one_line(page, "404 page (member row)", width)
            _assert_label_not_ellipsised(page, "404 page (member row)", width)
            assert page.evaluate(NO_H_SCROLL), f"404 page scrolls sideways at {width}px"
