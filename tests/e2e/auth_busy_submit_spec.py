"""End-to-end: a double tap on Verify sends one login code POST, not two (#803).

The root incident: a code form posted twice in one second, the first POST signed the member in
and rotated the CSRF token, and the second got Django's 403 page. ``components/busy_submit.html``
locks the button on the first real submit, which only a browser runs. One scenario counts the
real POSTs across a double click; the others stop the navigation after the component has run,
so the button can be read mid-submit.
Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import re

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Route, expect

EMAIL = "double-tap@example.com"


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


# A window listener runs after the document listener busy_submit.html binds, so it sees each
# submit the component let through and can stop the navigation that would block every locator.
COUNT_AND_HOLD_SUBMITS = """
window.plSubmits = 0;
window.addEventListener('submit', function (e) { window.plSubmits += 1; e.preventDefault(); });
"""


def _open_the_code_page(page: Page, live_server) -> str:
    """Request a code through the real screens and return it, leaving the browser on Enter code."""
    user, _ = get_user_model().objects.get_or_create(username=EMAIL, defaults={"email": EMAIL})
    EmailAddress.objects.update_or_create(user=user, email=EMAIL, defaults={"verified": True, "primary": True})
    mail.outbox = []
    cache.clear()  # the request code rate limit is shared across the e2e lane (see conftest)
    page.goto(f"{live_server.url}{reverse('account_request_login_code')}")
    page.fill('input[name="email"]', EMAIL)
    page.locator('input[name="email"]').press("Enter")
    page.wait_for_selector('input[name="code"]')
    assert mail.outbox, "expected a login-code email to be sent"
    match = re.search(r"is:\s+(\S+)", mail.outbox[-1].body)
    assert match, mail.outbox[-1].body
    return match.group(1)


def _verify(page: Page):
    return page.locator("form[data-pl-busy-submit] button[type='submit'][data-pl-busy]")


def describe_the_enter_code_page():
    def it_sends_one_post_and_signs_in_when_verify_is_double_tapped(live_server, page):
        code = _open_the_code_page(page, live_server)
        page.fill('input[name="code"]', code)
        box = _verify(page).bounding_box()
        assert box
        held: list[Route] = []
        page.route("**/*", lambda r: held.append(r) if r.request.method == "POST" else r.continue_())

        # Two taps a phone's double tap apart, by coordinates: a locator would wait on the
        # navigation the first tap starts, and dblclick() fires both clicks inside one task,
        # where the browser folds them into one submission with or without the fix.
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_timeout(150)
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_timeout(300)

        assert len(held) == 1, [r.request.url for r in held]
        for route in held:
            route.continue_()
        page.unroute("**/*")
        page.wait_for_url(lambda url: "code/confirm" not in url)
        expect(page.locator("body")).not_to_contain_text("CSRF verification failed")

    def it_shows_verify_busy_and_disabled_while_the_post_is_in_flight(live_server, page):
        code = _open_the_code_page(page, live_server)
        verify = _verify(page)
        expect(verify).to_have_text("Verify Code")
        page.fill('input[name="code"]', code)
        page.evaluate(COUNT_AND_HOLD_SUBMITS)

        verify.dblclick()

        expect(verify).to_be_disabled()
        expect(verify).to_have_attribute("aria-busy", "true")
        expect(verify).to_have_text("Signing in...")
        assert page.evaluate("window.plSubmits") == 1

    def it_leaves_verify_alone_when_the_empty_code_fails_browser_validation(live_server, page):
        _open_the_code_page(page, live_server)
        verify = _verify(page)
        page.evaluate(COUNT_AND_HOLD_SUBMITS)

        verify.click()

        assert page.locator('input[name="code"]').evaluate("el => !el.validity.valid")
        expect(verify).to_be_enabled()
        expect(verify).not_to_have_attribute("aria-busy", "true")
        expect(verify).to_have_text("Verify Code")
        assert page.evaluate("window.plSubmits") == 0
