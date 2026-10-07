"""End-to-end: an Eventbrite buyer finishes registering, and the roster flags who has not (#652, part 3).

The emailed link opens the registration page with the finish form; signing there clears the
roster's "No waiver" flag. ``CAPTURE_652_SCREENSHOT=1`` also saves the PR's pictures under
``mockups/screenshots/``. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from classes.factories import RegistrationFactory
from classes.models import ClassOffering, Registration
from membership.models import Member, MembershipPlan
from tests.classes.eventbrite_fakes import listed_class
from tests.features import turn_on

EMAIL = "eventbrite-instructor@example.com"
CAPTURE = bool(os.environ.get("CAPTURE_652_SCREENSHOT"))
SHOTS = Path("mockups/screenshots")


def _instructor() -> Member:
    plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
    user, _ = get_user_model().objects.get_or_create(username=EMAIL, defaults={"email": EMAIL})
    member, _ = Member.objects.update_or_create(
        user=user,
        defaults={
            "full_legal_name": "Robin Maker",
            "membership_plan": plan,
            "status": Member.Status.ACTIVE,
            "instructor_slug": "robin-maker",
            "instructor_oriented_at": timezone.now(),
        },
    )
    turn_on("teach")
    return member


def _ticket(offering: ClassOffering, first: str, last: str, email: str) -> Registration:
    return cast(
        Registration,
        RegistrationFactory(
            class_offering=offering,
            first_name=first,
            last_name=last,
            email=email,
            status=Registration.Status.CONFIRMED,
            confirmed_at=timezone.now(),
            amount_paid_cents=4491,
            source=Registration.Source.EVENTBRITE,
            eventbrite_order_id="1234567890",
            eventbrite_attendee_id=f"att-{email}",
            member=None,
        ),
    )


def describe_an_eventbrite_buyer_finishing_registration():
    def it_signs_the_waiver_from_the_emailed_link(live_server, page):
        registration = _ticket(listed_class(title="Intro to Lost Wax Casting"), "Ada", "Lovelace", "ada@example.com")
        page.goto(f"{live_server.url}{reverse('classes:my_registration', args=[registration.self_serve_token])}")

        form = page.locator("#finish-form")
        expect(form.get_by_role("heading", name="Finish registering")).to_be_visible()
        expect(form.locator("input[name='create_account']")).to_be_checked()
        if CAPTURE:
            SHOTS.mkdir(parents=True, exist_ok=True)
            page.locator(".detail-card").screenshot(path=str(SHOTS / "652-eventbrite-finish-page.png"))

        form.locator("input[name='liability_signature']").fill("Ada Lovelace")
        form.locator("input[name='accepts_liability']").check()
        form.locator("input[name='create_account']").uncheck()
        form.get_by_role("button", name="Sign and finish").click()

        expect(page.locator(".detail-body").get_by_text("You're all set. Your waiver is signed.")).to_be_visible()
        expect(page.locator("#finish-form")).to_have_count(0)


def describe_the_roster_with_eventbrite_tickets():
    def it_flags_only_the_ticket_without_a_signed_waiver(live_server, page, login_via_code):
        offering = listed_class(title="Intro to Lost Wax Casting", instructor=_instructor())
        unsigned = _ticket(offering, "Ada", "Lovelace", "ada@example.com")
        signed = _ticket(offering, "Grace", "Hopper", "grace@example.com")
        signed.waivers.create(kind="liability", waiver_text="t", signature_text="Grace Hopper")
        login_via_code(EMAIL)
        page.goto(f"{live_server.url}{reverse('classes:teach_class_registrations', args=[offering.pk])}")

        unsigned_row = page.locator(f"#reg-row-{unsigned.pk}")
        signed_row = page.locator(f"#reg-row-{signed.pk}")
        expect(unsigned_row.locator("[data-roster-source='eventbrite']")).to_be_visible()
        expect(unsigned_row.locator("[data-roster-flag='no-waiver']")).to_have_text("No waiver")
        expect(signed_row.locator("[data-roster-source='eventbrite']")).to_be_visible()
        expect(signed_row.locator("[data-roster-flag='no-waiver']")).to_have_count(0)
        if CAPTURE:
            SHOTS.mkdir(parents=True, exist_ok=True)
            page.locator("table").filter(has=unsigned_row).screenshot(
                path=str(SHOTS / "652-eventbrite-roster-flag.png")
            )
