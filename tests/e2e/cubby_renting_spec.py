"""End-to-end: renting a guild cubby (#978), the rent sheet through Stripe and back, then Your Cubby.

A member taps an open cubby, picks Other and types below the floor (the error shows, Continue to
Payment is disabled), picks an amount, continues, and "Stripe" sends them straight back: the stubbed
Checkout answers with plfog's own return page, whose session and subscription reads are stubbed
too, so the real return page settles the rental and lands on the tab. Then the Your Cubby card:
Change Amount, Cancel Rental and Keep My Cubby. A member who loses the cubby while choosing gets
the "just rented" toast. On a phone the sheet is a bottom sheet and nothing scrolls sideways.

Stripe is patched at ``billing.stripe_utils`` in this process (live_server serves from a thread of
it). Waits are on what the page shows. Run with ``pytest -m e2e``, on PostgreSQL in CI.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import CubbyRental, Member
from tests.billing.factories import TabFactory
from tests.membership.factories import (
    CubbyFactory,
    CubbyRentalFactory,
    GuildFactory,
    MembershipPlanFactory,
    ShelfUnitFactory,
)

RENTER_EMAIL = "cubby-renter@example.com"
_PERIOD_END = int(datetime(2026, 11, 27, 18, tzinfo=UTC).timestamp())


def _renter() -> Member:
    MembershipPlanFactory()
    member = User.objects.create_user(username=RENTER_EMAIL, email=RENTER_EMAIL).member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Wren Ashcombe"
    member.save(update_fields=["status", "full_legal_name"])
    TabFactory(member=member, stripe_customer_id="cus_wren")
    return member


def _shelf() -> tuple[Any, str]:
    guild = GuildFactory(name="Thornfield Ceramics", show_cubbies_tab=True)
    unit = ShelfUnitFactory(guild=guild, name="Glaze Wall", rows=1, columns=3)
    CubbyFactory(unit=unit, label="B3", size="12 x 12 x 16 in", floor_cents=1500, suggested_cents=2000)
    CubbyFactory(unit=unit, label="B4", column=2, floor_cents=1500, suggested_cents=2000)
    return guild, f"{reverse('hub_guild_detail', args=[guild.slug])}?tab=cubbies"


def _snapshot(**_kwargs: Any) -> dict[str, Any]:
    """What Stripe says about the one rental awaiting payment (or the newest) once it is paid."""
    rental = CubbyRental.objects.order_by("-pk").first()
    assert rental is not None
    return {
        "id": "sub_e2e",
        "status": "active",
        "customer": "cus_wren",
        "metadata": {
            "kind": "cubby_rental",
            "rental_pk": str(rental.pk),
            "cubby_pk": str(rental.cubby_id),
            "member_pk": str(rental.member_id),
        },
        "cancel_at_period_end": False,
        "cancel_at": None,
        "default_payment_method": "",
        "latest_invoice": "in_e2e",
        "item_id": "si_e2e",
        "product_id": "prod_e2e",
        "unit_amount": rental.amount_cents,
        "current_period_end": _PERIOD_END,
    }


@contextmanager
def _stripe() -> Iterator[dict[str, Any]]:
    """Checkout answers with plfog's own return page; the return page finds it complete and paid."""
    calls: dict[str, Any] = {}

    def create(**kwargs: Any) -> dict[str, str]:
        calls["checkout"] = kwargs
        return {"id": "cs_e2e", "url": kwargs["success_url"]}

    with (
        patch("billing.stripe_utils.create_subscription_checkout_session", side_effect=create),
        patch(
            "billing.stripe_utils.retrieve_checkout_session",
            return_value={"status": "complete", "subscription": "sub_e2e", "url": ""},
        ),
        patch("billing.stripe_utils.retrieve_subscription", side_effect=_snapshot),
        patch("billing.stripe_utils.update_subscription_amount") as update,
        patch("billing.stripe_utils.set_subscription_cancel_at_period_end") as cancel,
    ):
        calls["update"], calls["cancel"] = update, cancel
        yield calls


def describe_renting_a_cubby():
    @pytest.fixture(autouse=True)
    def _links_stay_on_the_live_server(settings, live_server):
        # Checkout's success URL and the portal's return URL are absolute on MEMBER_BASE_URL.
        settings.MEMBER_BASE_URL = live_server.url

    def it_rents_through_checkout_and_runs_your_cubby(live_server, page, login_via_code):
        _renter()
        _guild, path = _shelf()
        page.set_viewport_size({"width": 1280, "height": 900})
        login_via_code(RENTER_EMAIL)
        with _stripe() as stripe_calls:
            page.goto(f"{live_server.url}{path}")
            pane = page.locator("[data-guild-cubbies-pane]")
            pane.locator('[data-cubby-wall] button[data-cubby="B3"]').click()

            sheet = page.locator("[data-cubby-rent-sheet]")
            expect(sheet).to_contain_text("The guild asks at least $15 a month.")
            continue_button = sheet.get_by_role("button", name="Continue to Payment")

            sheet.locator("label.pl-cubby-amount", has_text="Other").click()
            sheet.get_by_label("Your amount").fill("8")
            expect(sheet.locator("[data-cubby-error]")).to_have_text(
                "The lowest you can pay for B3 is $15 a month. Pick $15 or more."
            )
            expect(continue_button).to_be_disabled()

            sheet.get_by_label("Your amount").fill("22")
            expect(sheet.locator("[data-cubby-error]")).to_be_hidden()
            expect(sheet.locator(".pl-cubby-total")).to_contain_text("$22 a month")
            continue_button.click()

            expect(page.locator("[data-cubby-mine='B3']")).to_be_visible()
            assert stripe_calls["checkout"]["amount_cents"] == 2200
            rental = CubbyRental.objects.get()
            assert rental.status == CubbyRental.Status.ACTIVE
            card = page.locator("[data-cubby-mine='B3']")
            expect(card).to_contain_text("$22 a month")
            expect(card).to_contain_text("Fri, Nov 27")
            expect(pane.get_by_role("img", name="B3, your cubby")).to_be_visible()

            # Change Amount: the sheet loads into the card's modal, Extra Help, Save.
            card.get_by_role("button", name="Change Amount").click()
            change = page.locator("[data-cubby-change-form]")
            expect(change).to_contain_text("You pay $22 a month for B3 now.")
            change.locator("label.pl-cubby-amount", has_text="Extra Help").click()
            expect(change.locator(".pl-cubby-total")).to_contain_text("$25 a month")
            change.get_by_role("button", name="Save").click()
            expect(page.locator("[data-cubby-mine='B3']")).to_contain_text("$25 a month")
            assert stripe_calls["update"].call_args.kwargs["amount_cents"] == 2500

            # Cancel Rental asks first, then Keep My Cubby undoes it.
            page.locator("[data-cubby-mine='B3'] [data-cubby-cancel]").click()
            confirm = page.get_by_role("dialog").filter(has_text="Cancel Your Cubby?")
            expect(confirm).to_contain_text("B3 stays yours until Fri, Nov 27")
            confirm.get_by_role("button", name="Cancel Rental").click()
            keep = page.locator("[data-cubby-mine='B3'] [data-cubby-keep]")
            expect(keep).to_be_visible()
            expect(page.locator("[data-cubby-mine='B3']")).to_contain_text("Yours Until")
            keep.click()
            expect(page.locator("[data-cubby-mine='B3'] [data-cubby-cancel]")).to_be_visible()
            assert [c.kwargs["cancel"] for c in stripe_calls["cancel"].call_args_list] == [True, False]

    def it_tells_a_member_who_lost_the_cubby_while_choosing(live_server, page, login_via_code):
        _renter()
        guild, path = _shelf()
        page.set_viewport_size({"width": 1280, "height": 900})
        login_via_code(RENTER_EMAIL)
        with _stripe() as stripe_calls:
            page.goto(f"{live_server.url}{path}")
            page.locator('[data-guild-cubbies-pane] [data-cubby-wall] button[data-cubby="B4"]').click()
            sheet = page.locator("[data-cubby-rent-sheet]")
            expect(sheet).to_be_visible()
            CubbyRentalFactory(cubby=guild.shelf_units.get().cubbies.get(label="B4"))
            sheet.get_by_role("button", name="Continue to Payment").click()
            expect(
                page.locator("#plt-container").get_by_text("Someone just rented B4. Pick another open cubby.")
            ).to_be_visible()
            expect(
                page.locator("[data-guild-cubbies-pane] [data-cubby-wall]").get_by_role("img", name="B4, rented")
            ).to_be_visible()
            assert "checkout" not in stripe_calls

    def it_works_as_a_bottom_sheet_on_a_phone(live_server, page, login_via_code):
        member = _renter()
        guild, path = _shelf()
        CubbyRentalFactory(
            member=member,
            cubby=guild.shelf_units.get().cubbies.get(label="B4"),
            current_period_end=datetime(2026, 11, 27, 18, tzinfo=UTC),
        )
        page.set_viewport_size({"width": 390, "height": 844})
        login_via_code(RENTER_EMAIL)
        page.goto(f"{live_server.url}{path}")
        card = page.locator("[data-cubby-mine='B4']")
        expect(card).to_be_visible()
        expect(card.get_by_role("button", name="Change Amount")).to_be_visible()
        page.locator("[data-guild-cubbies-pane] [data-cubby-list]").get_by_role("button", name="Rent B3").click()
        sheet = page.locator("[data-cubby-rent-sheet]")
        expect(sheet.get_by_role("button", name="Continue to Payment")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
