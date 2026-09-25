"""End-to-end: an instructor asks for a class discount code and an admin decides (#428, part 1).

The request form, the Pending pill on the class tab, the Requests Waiting row in Classes admin,
the review page's Approve, and the Decline confirm whose button stays disabled until a note is
typed are one flow across two roles. Only a browser proves the last part: the confirm modal is
Alpine, the note rides in a bound hidden input, and the button's disabled state is client side.
Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from typing import cast

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from classes.factories import ClassOfferingFactory, DiscountCodeRequestFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering, DiscountCode, DiscountCodeRequest
from core.models import SiteConfiguration
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

INSTRUCTOR_EMAIL = "request-teacher@example.com"
ADMIN_EMAIL = "request-admin@example.com"


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before the teardown truncates the tables.

    Every scenario here ends on a POST the browser made (the request form, Approve, or the
    Decline confirm) and the redirect it follows, so a scenario can end with a request still
    in flight. The live server thread still holds that request's row locks when
    ``transactional_db`` truncates, and the truncate is the one that loses:
    ``psycopg.errors.DeadlockDetected``, surfaced as an ERROR with no assertion failure. It
    is timing rather than ordering, so it reproduces on some machines and not others.

    Depending on ``transactional_db`` is what orders this: pytest finalises a fixture before
    the ones it depends on, so the settle always runs before the truncate. It lives in a
    teardown so a scenario added later cannot bring the flake back by forgetting the line. A
    Playwright error is swallowed on purpose: this is housekeeping, and a page left broken by
    a failing assertion must not turn that failure into a confusing teardown error.

    This leans on the hub chrome doing no polling (no ``setInterval``, ``EventSource``,
    ``WebSocket`` or ``hx-trigger="every"`` on these pages). Add one and ``networkidle``
    never arrives and every teardown quietly eats the timeout below; wait for a different
    signal then.
    """
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _instructor_discount_codes_on() -> None:
    """The master flag; the approval flag is on by default, so both on is approval mode."""
    config = SiteConfiguration.load()
    config.instructor_discount_codes_enabled = True
    config.save(update_fields=["instructor_discount_codes_enabled"])


def _seed_instructor() -> Member:
    MembershipPlanFactory()  # so the user signal can provision the member the instructor factory then updates
    user = UserFactory(username=INSTRUCTOR_EMAIL)
    return cast(
        Member, InstructorFactory(user=user, full_legal_name="Request Teacher", instructor_slug="request-teacher")
    )


def _login_as_admin(login_via_code) -> None:
    # Sign in through the real code flow, then elevate to admin (compute_actual_roles grants
    # admin from is_superuser), as guild_studio_hours_spec does.
    login_via_code(ADMIN_EMAIL)
    user = get_user_model().objects.get(username=ADMIN_EMAIL)
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])


def _seed_request(code: str) -> DiscountCodeRequest:
    MembershipPlanFactory()  # the admin's login signal needs a plan to provision their member
    return cast(DiscountCodeRequest, DiscountCodeRequestFactory(code=code, reason="Spring push."))


def describe_an_instructor():
    def it_requests_a_code_and_sees_it_pending(live_server, page, login_via_code):
        _instructor_discount_codes_on()
        instructor = _seed_instructor()
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.DRAFT, title="Bowl Turning")
        login_via_code(INSTRUCTOR_EMAIL)
        tab_url = f"{live_server.url}{reverse('classes:teach_class_discount_codes', kwargs={'pk': offering.pk})}"

        page.goto(tab_url)
        page.get_by_role("link", name="Request a Code", exact=True).click()
        expect(page.locator("#id_class_offering")).to_have_value(str(offering.pk))
        page.fill("#id_code", "earlybird")
        page.fill("#id_discount_pct", "15")
        page.fill("#id_reason", "Returning students get a head start.")
        page.get_by_role("button", name="Submit Request", exact=True).click()

        expect(page).to_have_url(tab_url)
        row = page.locator("tr", has_text="EARLYBIRD")
        expect(row.locator(".hub-pill--warn")).to_have_text("Pending")
        req = DiscountCodeRequest.objects.get()
        assert req.code == "EARLYBIRD"
        assert req.class_offering == offering
        assert not DiscountCode.objects.exists()


def describe_an_admin():
    def it_approves_a_request_from_the_queue(live_server, page, login_via_code):
        _instructor_discount_codes_on()
        req = _seed_request("SPRING20")
        _login_as_admin(login_via_code)
        queue_url = f"{live_server.url}{reverse('classes:admin_discount_codes')}"

        page.goto(queue_url)
        expect(page.locator("tr", has_text="SPRING20")).to_be_visible()
        page.get_by_role("link", name="Review", exact=True).click()
        expect(page.locator("#id_code")).to_have_value("SPRING20")
        page.get_by_role("button", name="Approve", exact=True).click()

        expect(page).to_have_url(queue_url)
        expect(page.locator("tr", has_text="SPRING20")).to_be_visible()
        expect(page.get_by_role("link", name="Review", exact=True)).to_have_count(0)
        code = DiscountCode.objects.get(code="SPRING20")
        assert code.is_approved is True
        req.refresh_from_db()
        assert req.status == DiscountCodeRequest.Status.APPROVED
        assert req.discount_code == code

    def it_declines_with_a_note(live_server, page, login_via_code):
        _instructor_discount_codes_on()
        req = _seed_request("SPRING20")
        _login_as_admin(login_via_code)

        page.goto(f"{live_server.url}{reverse('classes:admin_discount_code_request_review', kwargs={'pk': req.pk})}")
        page.get_by_role("button", name="Decline", exact=True).click()
        modal = page.locator(".pl-modal-backdrop:has-text('Decline This Request?')")
        confirm = modal.get_by_role("button", name="Decline Request", exact=True)
        expect(confirm).to_be_disabled()
        modal.locator("#decline-request-note").fill("Too steep for a first class.")
        expect(confirm).to_be_enabled()
        confirm.click()

        expect(page).to_have_url(f"{live_server.url}{reverse('classes:admin_discount_codes')}")
        req.refresh_from_db()
        assert req.status == DiscountCodeRequest.Status.DECLINED
        assert req.decision_note == "Too steep for a first class."
        assert not DiscountCode.objects.exists()
