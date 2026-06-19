"""End-to-end: a member signs in with an emailed code, then books a free class.

The Phase 1 browser-QA slice. It exercises the two flows that matter most and
are hardest to cover with unit tests:

1. passwordless login-by-code (the real allauth screens + emailed code), and
2. registering for a published free class from the public catalog.

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


def describe_login_and_book():
    def it_signs_in_then_registers_for_a_free_class(live_server, page, login_via_code):
        # A published, free class with one upcoming session is bookable.
        offering = ClassOfferingFactory(
            title="Intro to Lost Wax Casting",
            slug="intro-to-lost-wax-casting",
            status=ClassOffering.Status.PUBLISHED,
            is_private=False,
            price_cents=0,
        )
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=7),
            ends_at=timezone.now() + timedelta(days=7, hours=2),
        )

        # 1. Sign in through the genuine login-by-code flow. Landing clear of the
        #    /accounts/ screens proves the code was accepted and the session
        #    cookie persisted across the redirect.
        login_via_code(MEMBER_EMAIL)
        expect(page).not_to_have_url(re.compile(r"/accounts/"))

        # 2. The published class shows up in the public catalog.
        page.goto(f"{live_server.url}{reverse('classes:public_list')}")
        expect(page.locator("body")).to_contain_text(offering.title)

        # 3. Open its detail page and follow the register call-to-action.
        detail_path = reverse("classes:public_class_detail", kwargs={"slug": offering.slug})
        page.goto(f"{live_server.url}{detail_path}")
        register_path = reverse("classes:register", kwargs={"slug": offering.slug})
        page.locator(f'a[href="{register_path}"]').first.click()

        # 4. Fill the registration form, including the typed liability signature.
        page.fill('input[name="first_name"]', "Casey")
        page.fill('input[name="last_name"]', "Member")
        page.fill('input[name="email"]', MEMBER_EMAIL)
        page.fill('input[name="liability_signature"]', "Casey Member")
        page.check('input[name="accepts_liability"]')
        page.locator("button.btn-reg").click()

        # 5. A free registration confirms immediately on the success page.
        expect(page.locator("body")).to_contain_text("You're in!")
