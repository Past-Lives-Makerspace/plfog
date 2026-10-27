"""End-to-end: guild cubbies part 1 (#977), the staff manager and the guild page's Cubbies tab.

A guild lead opens the Cubbies manager, adds a shelf unit through the Add Shelf Unit modal, taps an
empty square, labels the cubby and saves, and the cubby is on the shelf. Steppers add a row over
htmx without a reload. With the switch on, the guild page's ?tab=cubbies deep link opens the tab:
Grid shows the shelf, List shows the rows, and tapping an open cubby opens its sheet. At 390px the
tab opens on List and nothing scrolls sideways. Waits are on what the page shows. Run with
``pytest -m e2e``, on PostgreSQL in CI.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import Cubby, Member, ShelfUnit
from tests.membership.factories import CubbyFactory, GuildFactory, MembershipPlanFactory, ShelfUnitFactory

LEAD_EMAIL = "cubbies-lead@example.com"


def _lead() -> Member:
    MembershipPlanFactory()
    member = User.objects.create_user(username=LEAD_EMAIL, email=LEAD_EMAIL).member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Sorrel Kilnwright"
    member.save(update_fields=["status", "full_legal_name"])
    return member


def describe_guild_cubbies():
    def it_lets_a_lead_add_a_shelf_and_place_a_cubby(live_server, page, login_via_code):
        guild = GuildFactory(name="Thornfield Ceramics", guild_lead=_lead())
        page.set_viewport_size({"width": 1280, "height": 900})
        login_via_code(LEAD_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_guild_cubbies', args=[guild.pk])}")

        expect(page.locator("[data-cubby-empty]")).to_be_visible()
        page.locator("[data-cubby-empty] [data-cubby-add-unit]").click()
        form = page.locator("[data-cubby-add-unit-form]")
        expect(form).to_be_visible()
        form.get_by_label("Shelf Name").fill("Glazewick Wall")
        form.get_by_label("Rows").select_option("2")
        form.get_by_label("Across").select_option("3")
        form.get_by_role("button", name="Save").click()

        unit_card = page.locator("[data-cubby-unit]")
        expect(unit_card).to_contain_text("Glazewick Wall")
        expect(unit_card.locator("[data-cubby-slot]")).to_have_count(6)

        # One more row over htmx: the card swaps back saved with three more squares.
        unit_card.get_by_role("button", name="One more row").click()
        expect(unit_card.locator("[data-cubby-saved]")).to_be_visible()
        expect(unit_card.locator("[data-cubby-slot]")).to_have_count(9)
        assert ShelfUnit.objects.get().rows == 3

        unit_card.locator('[data-cubby-slot="1-2"]').click()
        panel = page.locator("[data-cubby-panel]")
        expect(panel).to_contain_text("New Cubby on Glazewick Wall")
        panel.get_by_label("Label").fill("W7")
        panel.get_by_label("Size").fill("12 x 12 x 16 in")
        panel.get_by_role("button", name="Save").click()

        expect(page.locator('[data-cubby-unit] [data-cubby="W7"]')).to_be_visible()
        cubby = Cubby.objects.get()
        assert (cubby.label, cubby.row, cubby.column) == ("W7", 1, 2)

    def it_shows_members_the_shelf_the_list_and_a_cubbys_sheet(live_server, page, login_via_code):
        guild = GuildFactory(name="Thornfield Ceramics", guild_lead=_lead(), show_cubbies_tab=True)
        unit = ShelfUnitFactory(guild=guild, name="Glazewick Wall", rows=1, columns=3)
        CubbyFactory(unit=unit, label="W1", size="12 x 12 x 16 in", note="Beside the sink")
        CubbyFactory(unit=unit, label="W2", column=2, out_of_service=True)
        url = f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}?tab=cubbies"
        page.set_viewport_size({"width": 1280, "height": 900})
        login_via_code(LEAD_EMAIL)
        page.goto(url)

        pane = page.locator("[data-guild-cubbies-pane]")
        expect(pane.locator("[data-cubby-wall]")).to_be_visible()
        expect(pane.locator("[data-cubby-list]")).to_be_hidden()
        expect(pane.get_by_role("img", name="W2, out of service")).to_be_visible()

        pane.locator('[data-cubby-wall] button[data-cubby="W1"]').click()
        sheet = page.locator("[data-cubby-detail]")
        expect(sheet).to_contain_text("W1")
        expect(sheet).to_contain_text("Glazewick Wall, far left")
        expect(sheet).to_contain_text("Beside the sink")
        expect(sheet).to_contain_text("From $15 a month, $20 suggested.")
        sheet.get_by_role("button", name="Close").click()
        expect(sheet).to_be_hidden()

        pane.locator('[data-cubby-view="list"]').click()
        expect(pane.locator("[data-cubby-list]")).to_be_visible()
        expect(pane.locator("[data-cubby-wall]")).to_be_hidden()
        expect(pane.locator('[data-cubby-view="list"]')).to_have_attribute("aria-pressed", "true")

        # A phone opens on List, and the page never scrolls sideways.
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(url)
        expect(pane.locator("[data-cubby-list]")).to_be_visible()
        expect(pane.locator("[data-cubby-wall]")).to_be_hidden()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
