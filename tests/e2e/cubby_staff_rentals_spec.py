"""End-to-end: guild cubbies part 3 (#979), the staff Rentals tab.

A guild lead opens Cubbies > Rentals: the tiles, the chips and the table list who rents what, and
the Amount header sorts it. Assign a Cubby opens its modal: the member is picked from the search
list, Paid Another Way reveals the monthly amount, and Assign and Email lands back on the tab with
the new row. End Rental opens its confirm, which stays disabled until a reason is typed, and the
rental moves to Ended. At 390px every rental is a card, the table is gone and nothing scrolls
sideways. Waits are on what the page shows. Run with ``pytest -m e2e``, on PostgreSQL in CI.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import CubbyRental, Member
from tests.membership.factories import (
    CubbyFactory,
    CubbyRentalFactory,
    GuildFactory,
    MembershipPlanFactory,
    ShelfUnitFactory,
)

LEAD_EMAIL = "cubbies-rentals-lead@example.com"


def _person(email: str, name: str) -> Member:
    MembershipPlanFactory()
    member = User.objects.create_user(username=email, email=email).member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _guild_with_rentals() -> tuple[object, CubbyRental]:
    guild = GuildFactory(
        name="Larkspur Clay Guild", guild_lead=_person(LEAD_EMAIL, "Sorrel Kilnwright"), show_cubbies_tab=True
    )
    unit = ShelfUnitFactory(guild=guild, name="Glazewick Wall", rows=2, columns=3)
    CubbyFactory(unit=unit, label="W1", column=1)
    CubbyFactory(unit=unit, label="W2", column=2)
    CubbyFactory(unit=unit, label="W3", column=3)
    CubbyFactory(unit=unit, label="W4", row=2, column=1)
    renter = _person("quenby@example.com", "Quenby Ashgrove")
    CubbyRentalFactory(cubby=unit.cubbies.get(label="W1"), member=renter, amount_cents=1800)
    comped = CubbyRentalFactory(
        cubby=unit.cubbies.get(label="W2"),
        member=_person("ivo@example.com", "Ivo Brackenridge"),
        source=CubbyRental.Source.COMPED,
        amount_cents=0,
        stripe_subscription_id=None,
    )
    _person("fennel@example.com", "Fennel Stoatwhistle")
    return guild, comped


def describe_cubby_staff_rentals():
    def it_lists_sorts_assigns_and_ends(live_server, page, login_via_code):
        guild, comped = _guild_with_rentals()
        page.set_viewport_size({"width": 1280, "height": 900})
        login_via_code(LEAD_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_guild_cubbies_rentals', args=[guild.pk])}")

        pane = page.locator("[data-cubby-rentals]")
        expect(pane.locator('[data-cubby-tile="rented"]')).to_contain_text("2")
        expect(pane.locator('[data-cubby-tile="rented"]')).to_contain_text("1 comped")
        expect(pane.locator('[data-rental-filter="current"]')).to_have_attribute("aria-current", "true")
        table = pane.locator("[data-cubby-rentals-table]")
        expect(table.locator("tbody tr")).to_have_count(2)
        expect(pane.locator("[data-cubby-rentals-table] [data-rental-chip='comped']")).to_be_visible()
        expect(pane.locator("[data-rental-card]").first).to_be_hidden()

        # Amount sorts the table, high to low on the second tap.
        table.get_by_role("link", name="Amount").click()
        expect(page).to_have_url(
            f"{live_server.url}{reverse('hub_guild_cubbies_rentals', args=[guild.pk])}?sort=amount_cents&dir=asc"
        )
        expect(table.locator("tbody tr").first).to_contain_text("Ivo Brackenridge")
        table.get_by_role("link", name="Amount").click()
        expect(table.locator("tbody tr").first).to_contain_text("Quenby Ashgrove")

        # Assign a Cubby: pick the member, a cubby, Paid Another Way and its amount.
        pane.locator("[data-cubby-assign-open]").click()
        form = page.locator("[data-cubby-assign-form]")
        expect(form).to_be_visible()
        form.get_by_label("Member", exact=True).fill("fenn")
        form.locator('[data-member-option="Fennel Stoatwhistle"]').click()
        expect(form.get_by_label("Member", exact=True)).to_have_value("Fennel Stoatwhistle")
        form.get_by_label("Cubby").select_option(label="W3, open")
        amount = form.locator("[data-cubby-assign-amount]")
        expect(amount).to_be_hidden()
        form.get_by_label("Paid Another Way").check()
        expect(amount).to_be_visible()
        form.get_by_label("They pay", exact=True).fill("16")
        form.locator("[data-cubby-assign-submit]").click()

        expect(table.locator("tbody tr")).to_have_count(3)
        expect(table).to_contain_text("Fennel Stoatwhistle")
        expect(table).to_contain_text("Paid another way")
        assigned = CubbyRental.objects.get(member__full_legal_name="Fennel Stoatwhistle")
        assert (assigned.source, assigned.amount_cents, assigned.cubby.label) == ("outside", 1600, "W3")

        # End Rental: the confirm needs a reason before its button works.
        pane.locator(f'[data-cubby-end="{comped.pk}"]').click()
        dialog = page.get_by_role("dialog").filter(has_text="End Ivo Brackenridge's Rental?")
        expect(dialog).to_be_visible()
        end_button = dialog.get_by_role("button", name="End Rental")
        expect(end_button).to_be_disabled()
        dialog.get_by_label("Reason").fill("The kiln crew needs W2 back.")
        expect(end_button).to_be_enabled()
        end_button.click()

        expect(table.locator("tbody tr")).to_have_count(2)
        comped.refresh_from_db()
        assert (comped.status, comped.end_reason) == ("ended", "The kiln crew needs W2 back.")
        pane.locator('[data-rental-filter="ended"]').click()
        expect(table.locator("tbody tr")).to_have_count(1)
        expect(table.locator("[data-rental-chip='ended']")).to_be_visible()

    def it_stacks_each_rental_into_a_card_on_a_phone(live_server, page, login_via_code):
        guild, _comped = _guild_with_rentals()
        page.set_viewport_size({"width": 390, "height": 844})
        login_via_code(LEAD_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_guild_cubbies_rentals', args=[guild.pk])}")

        pane = page.locator("[data-cubby-rentals]")
        expect(pane.locator("[data-rental-card]")).to_have_count(2)
        expect(pane.locator("[data-rental-card]").first).to_be_visible()
        expect(pane.locator("[data-cubby-rentals-table]")).to_be_hidden()
        expect(pane.locator("[data-rental-card]").first).to_contain_text("Next Charge")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
