"""End-to-end: an admin aims an announcement at All Instructors (#806).

The Python suite renders the picker, the count and the send; only a browser proves the Alpine
count follows the audience select and the Email switch (an instructor with no account is reached
by email alone, so switching Email off drops them from the count). ``CAPTURE_806_SCREENSHOTS=1``
also saves the PR's picture of the composer with All Instructors selected, light, desktop, under
``mockups/screenshots/``. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

ADMIN_EMAIL = "instructors-admin@example.com"
EDITOR = '.pl-rte[data-rte-for="id_body"] .ql-editor'
REACH = ".pl-wizard-reach strong"
SHOTS = Path("mockups/screenshots")
CAPTURE = bool(os.environ.get("CAPTURE_806_SCREENSHOTS"))


def _seed() -> None:
    """Two instructor accounts (one never signed in) and one instructor with no account."""
    MembershipPlanFactory()
    for username, last_login in (("taught@example.com", timezone.now()), ("unsigned@example.com", None)):
        user = get_user_model().objects.create_user(username=username, email=username, last_login=last_login)
        Member.objects.filter(user=user).update(status=Member.Status.ACTIVE, instructor_oriented_at=timezone.now())
    MemberFactory(_pre_signup_email="noaccount@example.com", instructor_slug="no-account")


def _sign_in_as_admin(login_via_code) -> None:
    login_via_code(ADMIN_EMAIL)
    user = get_user_model().objects.get(username=ADMIN_EMAIL)
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])


def describe_the_all_instructors_audience():
    def it_counts_every_instructor_and_drops_the_email_only_one_with_email_off(live_server, page, login_via_code):
        _seed()
        _sign_in_as_admin(login_via_code)
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server.url}{reverse('hub_compose')}")
        expect(page.locator(EDITOR)).to_be_visible()

        page.locator("select[name=audience]").select_option("instructor")
        expect(page.locator("select[name=audience] option:checked")).to_have_text("All Instructors")
        page.locator(EDITOR).fill("Pay day moves to the first Friday of the month.")
        if CAPTURE:
            SHOTS.mkdir(parents=True, exist_ok=True)
            page.evaluate("document.documentElement.setAttribute('data-theme', 'light')")
            page.wait_for_timeout(600)  # let the theme's colour transitions finish before the shot
            page.screenshot(path=str(SHOTS / "806-all-instructors-light-desktop.png"), full_page=True)
            page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")

        page.get_by_role("tab", name="2. Preview & send").click()
        expect(page.locator(REACH)).to_have_text("3")

        page.get_by_role("tab", name="1. Compose").click()
        page.locator("label.pl-toggle:has(#id_send_email)").click()
        expect(page.locator("#id_send_email")).not_to_be_checked()
        page.get_by_role("tab", name="2. Preview & send").click()
        expect(page.locator(REACH)).to_have_text("2")
