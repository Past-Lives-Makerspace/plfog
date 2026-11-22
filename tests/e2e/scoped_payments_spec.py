"""End-to-end: a guild lead opens Payments and Reports from Guild Tools (#1294).

What a Django client cannot prove: the guild section's Payments card a lead clicks lands on the
Payments page with only their guild's rows, the tab row reads Payments and Reports, and Reports
shows their guild's share with no treasurer control on the page.

Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Page, expect

from billing.models import BillingSettings
from membership.models import Member, OrientationBooking
from tests.membership.factories import (
    GuildFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)


def _sign_in(login_via_code, email: str) -> Member:
    MembershipPlanFactory()  # the login signal auto-creates the member only when a plan exists
    login_via_code(email)
    return get_user_model().objects.get(username=email).member


def _paid_class(guild, title: str) -> None:
    from classes.factories import CategoryFactory, ClassOfferingFactory, RegistrationFactory

    registration = RegistrationFactory(
        class_offering=ClassOfferingFactory(category=CategoryFactory(guild=guild), title=title),
        amount_paid_cents=12000,
    )
    registration.confirmed_at = timezone.now()
    registration.save(update_fields=["confirmed_at"])


def describe_scoped_payments_in_the_browser():
    def it_takes_a_guild_lead_from_guild_tools_to_its_own_payments_and_reports(live_server, page: Page, login_via_code):
        BillingSettings.load()
        member = _sign_in(login_via_code, "scoped-payments-lead@example.com")
        wood = GuildFactory(name="Aa Browser Wood", guild_lead=member)
        metal = GuildFactory(name="Bb Browser Metal")
        _paid_class(wood, "Browser Spoon Carving")
        _paid_class(metal, "Browser Forging")
        OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=metal), amount_paid_cents=4000, status=OrientationBooking.Status.CONFIRMED
        )

        page.goto(f"{live_server.url}{reverse('hub_admin_tools')}")
        page.locator('[data-tools-section="guild"] [data-tool-scoped-payments]').click()
        expect(page).to_have_url(re.compile(re.escape(reverse("billing_admin_dashboard")) + r"\?tab=payments$"))
        expect(page.locator("[data-payments-scoped]")).to_be_visible()

        table = page.locator(".pl-table-scroll")
        expect(table.get_by_text("Browser Spoon Carving")).to_be_visible()
        expect(table.get_by_text("Browser Forging")).to_have_count(0)
        expect(table.get_by_text("Orientation", exact=False)).to_have_count(0)
        expect(page.get_by_role("button", name="Refund")).to_have_count(0)

        tabs = page.locator("[data-dashboard-tab]")
        expect(tabs).to_have_text(["Payments", "Reports"])
        page.locator('[data-dashboard-tab="reconciliation"]').click()
        expect(page.locator("[data-reports-scoped]")).to_be_visible()
        expect(page.locator("[data-reports-share]")).to_have_text("$12.00")
        expect(page.get_by_role("button", name="Take Snapshot")).to_have_count(0)
        report = page.locator('[hx-get*="/reconciliation/table/"]')
        expect(report.get_by_text("Aa Browser Wood").first).to_be_visible()
        expect(report.get_by_text("Bb Browser Metal")).to_have_count(0)
