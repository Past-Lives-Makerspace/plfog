"""End-to-end: the refunds card on a ticket bought on Eventbrite (#652, part 2).

An Eventbrite ticket is refunded through Eventbrite, a whole ticket at a time, never Stripe. The
card says so and its Refund button opens the Eventbrite variant of the modal, with no amount.
``CAPTURE_652_SCREENSHOT=1`` also saves the PR's picture under ``mockups/screenshots/``.
Run with ``pytest -m e2e`` on PostgreSQL.
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
from classes.models import Registration
from membership.models import Member, MembershipPlan
from tests.classes.eventbrite_fakes import listed_class

EMAIL = "eventbrite-admin@example.com"
CAPTURE = bool(os.environ.get("CAPTURE_652_SCREENSHOT"))
SHOTS = Path("mockups/screenshots")


def _seed() -> Registration:
    plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
    user, _ = get_user_model().objects.get_or_create(username=EMAIL, defaults={"email": EMAIL})
    Member.objects.update_or_create(
        user=user,
        defaults={
            "full_legal_name": "Robin Maker",
            "membership_plan": plan,
            "status": Member.Status.ACTIVE,
            "fog_role": Member.FogRole.ADMIN,
        },
    )
    offering = listed_class(title="Intro to Lost Wax Casting", price_cents=5000)
    return cast(
        Registration,
        RegistrationFactory(
            class_offering=offering,
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.com",
            status=Registration.Status.CONFIRMED,
            confirmed_at=timezone.now(),
            amount_paid_cents=4491,
            source=Registration.Source.EVENTBRITE,
            eventbrite_order_id="1234567890",
            eventbrite_attendee_id="9876543210",
        ),
    )


def describe_the_refunds_card_on_an_eventbrite_ticket():
    def it_sends_the_refund_through_eventbrite(live_server, page, login_via_code):
        registration = _seed()
        login_via_code(EMAIL)
        page.goto(f"{live_server.url}{reverse('classes:admin_registration_detail', kwargs={'pk': registration.pk})}")

        card = page.locator("div").filter(has=page.get_by_role("heading", name="Refunds", exact=True)).last
        expect(card).to_contain_text("refund goes back through Eventbrite, a whole ticket at a time")
        refund = card.get_by_role("button", name="Refund", exact=True)
        expect(refund).to_be_visible()
        if CAPTURE:
            SHOTS.mkdir(parents=True, exist_ok=True)
            card.screenshot(path=str(SHOTS / "652-eventbrite-refund-panel.png"))

        refund.click()
        modal = page.locator("#refund-modal-body")
        expect(modal.get_by_role("button", name="Refund in Eventbrite")).to_be_visible()
        expect(modal.locator("input[name='amount']")).to_have_count(0)
