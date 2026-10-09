"""End-to-end: Orientations load collapsed with an Edit button (#732), in a real browser.

On the equipment Orientation tab and the guild Orientations page each saved orientation is one
line (name, length, price) under an "Orientations" card whose "Add New Orientation +" sits at
its top right. Edit opens a row and the line follows what is typed; the add puts an open blank
row in place (on a guild, only where the Add an Orientation page does not offer it; elsewhere it
opens that page, #680); the equipment Save and the guild autosave store every row; a refused save opens
the row that has the error, on a full page post and on an autosave alike. Set
``CAPTURE_732_SCREENSHOTS=<dir>`` to write the PR screenshots there, in both themes. Run with
``pytest -m e2e``.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import OrientationType
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
)

ADMIN_EMAIL = "orientations-collapsed-admin@example.com"
ROWS_READY = "() => [...document.querySelectorAll('[data-otype-row]')].every((row) => !!row._x_dataStack)"
AUTOSAVE_READY = "() => !!(document.querySelector('[data-guild-autosave]') || {})._x_dataStack"


def _sign_in_as_admin(login_via_code) -> None:
    MembershipPlanFactory()
    login_via_code(ADMIN_EMAIL)
    user = get_user_model().objects.get(username=ADMIN_EMAIL)
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])


def _capture(page, name: str) -> None:
    folder = os.environ.get("CAPTURE_732_SCREENSHOTS")
    if not folder:
        return
    Path(folder).mkdir(parents=True, exist_ok=True)
    tour = page.get_by_role("button", name="No thanks")
    if tour.is_visible():
        tour.click()
    for theme in ("dark", "light"):
        page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
        page.locator(".pl-otype-card__head").first.scroll_into_view_if_needed()
        page.screenshot(path=str(Path(folder) / f"732-{name}-{theme}.png"), full_page=True)
    page.evaluate("() => document.documentElement.setAttribute('data-theme', 'dark')")


def describe_the_equipment_orientation_tab():
    def it_lists_collapsed_rows_expands_one_adds_one_and_saves(live_server, page, login_via_code):
        page.set_viewport_size({"width": 1280, "height": 900})
        _sign_in_as_admin(login_via_code)
        equipment = EquipmentFactory(name="Laser cutter")
        laser = OrientationTypeFactory(
            equipment_owned=True, equipment=equipment, name="Laser Basics", duration_minutes=60, price_cents=1500
        )
        page.goto(f"{live_server.url}{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation")
        page.wait_for_function(ROWS_READY)

        card = page.locator(".pl-equip-panel", has=page.get_by_role("heading", name="Orientations", exact=True))
        row = card.locator("[data-otype-row]").first
        expect(row.locator("[data-otype-name]")).to_have_text("Laser Basics")
        expect(row.locator("[data-otype-meta]")).to_have_text("60 min · $15")
        expect(page.locator("#id_otypes-0-name")).to_be_hidden()
        _capture(page, "equipment-collapsed")

        page.get_by_role("button", name="Edit Laser Basics").click()
        expect(page.locator("#id_otypes-0-name")).to_be_visible()
        page.locator("#id_otypes-0-name").fill("Laser Basics Plus")
        expect(row.locator("[data-otype-name]")).to_have_text("Laser Basics Plus")
        _capture(page, "equipment-expanded")
        page.get_by_role("button", name="Done editing Laser Basics Plus").click()
        expect(page.locator("#id_otypes-0-name")).to_be_hidden()

        card.get_by_role("button", name="Add New Orientation +").click()
        new_name = page.locator("#id_otypes-1-name")
        expect(new_name).to_be_visible()
        expect(new_name).to_be_focused()
        new_name.fill("Laser Advanced")
        page.locator("#id_otypes-1-duration_minutes").fill("90")
        card.get_by_role("button", name="Save", exact=True).click()

        expect(page.get_by_role("button", name="Edit Laser Advanced")).to_be_visible()
        expect(page.get_by_role("button", name="Edit Laser Basics Plus")).to_be_visible()
        expect(page.locator("#id_otypes-0-name")).to_be_hidden()
        laser.refresh_from_db()
        assert laser.name == "Laser Basics Plus"
        assert OrientationType.objects.get(equipment=equipment, name="Laser Advanced").duration_minutes == 90

    def it_reopens_the_row_a_save_refused(live_server, page, login_via_code):
        page.set_viewport_size({"width": 1280, "height": 900})
        _sign_in_as_admin(login_via_code)
        equipment = EquipmentFactory(name="Bandsaw")
        OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Saw Basics")
        page.goto(f"{live_server.url}{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation")
        page.wait_for_function(ROWS_READY)

        page.get_by_role("button", name="Edit Saw Basics").click()
        page.locator("#id_otypes-0-price").fill("9999")
        page.get_by_role("button", name="Done editing Saw Basics").click()
        expect(page.locator("#id_otypes-0-price")).to_be_hidden()
        page.locator("#equip-otype-rows").locator("..").get_by_role("button", name="Save", exact=True).click()

        expect(page.get_by_text("Enter a price between $0 and $500.")).to_be_visible()
        expect(page.locator("#id_otypes-0-price")).to_be_visible()

    def it_opens_a_collapsed_row_the_browser_finds_invalid_on_save(live_server, page, login_via_code):
        page.set_viewport_size({"width": 1280, "height": 900})
        _sign_in_as_admin(login_via_code)
        equipment = EquipmentFactory(name="Drill press")
        drill = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Drill Basics", price_cents=1000)
        page.goto(f"{live_server.url}{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation")
        page.wait_for_function(ROWS_READY)

        price = page.locator("#id_otypes-0-price")
        page.get_by_role("button", name="Edit Drill Basics").click()
        price.fill("15.555")
        page.get_by_role("button", name="Done editing Drill Basics").click()
        expect(price).to_be_hidden()
        page.locator("#equip-otype-rows").locator("..").get_by_role("button", name="Save", exact=True).click()

        # The browser blocks the post on the step; the row opens so its message has somewhere to show.
        expect(price).to_be_visible()
        expect(price).to_be_focused()
        assert price.evaluate("(el) => el.validity.stepMismatch")
        expect(page.get_by_role("button", name="Done editing Drill Basics")).to_be_visible()
        drill.refresh_from_db()
        assert drill.price_cents == 1000


def describe_the_guild_orientations_page():
    def it_lists_collapsed_rows_expands_one_and_links_its_add_to_the_add_page(live_server, page, login_via_code):
        page.set_viewport_size({"width": 1280, "height": 900})
        _sign_in_as_admin(login_via_code)
        guild = GuildFactory(name="Wood Guild")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        OrientationTypeFactory(guild=guild, name="Lathe", duration_minutes=90, price_cents=0)
        page.goto(f"{live_server.url}{reverse('hub_guild_orientations', args=[guild.pk])}")
        page.wait_for_function(AUTOSAVE_READY)
        page.wait_for_function(ROWS_READY)

        form = page.locator("#otypes-form")
        expect(form.get_by_role("heading", name="Orientations", exact=True)).to_be_visible()
        expect(form.locator("[data-otype-meta]").first).to_have_text("90 min · Free")
        expect(page.locator("#id_otypes-0-name")).to_be_hidden()
        _capture(page, "guild-collapsed")

        page.get_by_role("button", name="Edit Lathe").click()
        expect(page.locator("#id_otypes-0-name")).to_be_visible()
        _capture(page, "guild-expanded")
        page.get_by_role("button", name="Done editing Lathe").click()
        expect(page.locator("#id_otypes-0-name")).to_be_hidden()
        # The card's one add opens the Add an Orientation page with this guild chosen (#680).
        add = form.get_by_role("link", name="Add New Orientation +")
        expect(add).to_have_attribute("href", f"{reverse('hub_orientation_add')}?guild={guild.pk}")
        expect(page.locator("[data-add-orientation-type]")).to_have_count(1)

    def it_adds_an_open_row_in_place_on_a_hidden_guild_and_autosaves_it(live_server, page, login_via_code):
        page.set_viewport_size({"width": 1280, "height": 900})
        _sign_in_as_admin(login_via_code)
        # The add page lists an admin's active guilds only, so a hidden guild's add adds a row here.
        guild = GuildFactory(name="Hidden Wood Guild", is_active=False)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        OrientationTypeFactory(guild=guild, name="Lathe")
        page.goto(f"{live_server.url}{reverse('hub_guild_orientations', args=[guild.pk])}")
        page.wait_for_function(AUTOSAVE_READY)
        page.wait_for_function(ROWS_READY)

        page.locator("#otypes-form").get_by_role("button", name="Add New Orientation +").click()
        new_name = page.locator('input[name="otypes-1-name"]')
        expect(new_name).to_be_focused()
        new_name.fill("Spoon Carving")
        page.locator('input[name="otypes-1-duration_minutes"]').fill("120")
        expect(page.locator('input[name="otypes-1-id"]')).not_to_have_value("")
        assert OrientationType.objects.filter(guild=guild, name="Spoon Carving").exists()
        expect(page.get_by_role("button", name="Done editing Spoon Carving")).to_be_visible()
        expect(page.locator("#id_otypes-0-name")).to_be_hidden()

    def it_reopens_a_row_whose_typed_price_has_too_many_decimals(live_server, page, login_via_code):
        # The autosave posts with fetch, so the browser never blocks it; the server refuses and the row opens.
        page.set_viewport_size({"width": 1280, "height": 900})
        _sign_in_as_admin(login_via_code)
        guild = GuildFactory(name="Glass Guild")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        OrientationTypeFactory(guild=guild, name="Torch Basics")
        page.goto(f"{live_server.url}{reverse('hub_guild_orientations', args=[guild.pk])}")
        page.wait_for_function(AUTOSAVE_READY)
        page.wait_for_function(ROWS_READY)

        page.get_by_role("button", name="Edit Torch Basics").click()
        page.locator("#id_otypes-0-price").fill("15.555")
        page.get_by_role("button", name="Done editing Torch Basics").click()
        expect(page.locator("#id_otypes-0-price")).to_be_visible()
        expect(page.locator("#otypes-form [data-otype-row] .pl-field-error").first).to_be_visible()
        expect(page.get_by_role("button", name="Done editing Torch Basics")).to_be_visible()

    def it_reopens_a_row_whose_autosave_was_refused(live_server, page, login_via_code):
        page.set_viewport_size({"width": 1280, "height": 900})
        _sign_in_as_admin(login_via_code)
        guild = GuildFactory(name="Metal Guild")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        OrientationTypeFactory(guild=guild, name="Welding")
        page.goto(f"{live_server.url}{reverse('hub_guild_orientations', args=[guild.pk])}")
        page.wait_for_function(AUTOSAVE_READY)
        page.wait_for_function(ROWS_READY)

        page.get_by_role("button", name="Edit Welding").click()
        page.locator("#id_otypes-0-price").fill("9999")
        # Closed before the autosave answers: the refusal opens it again.
        page.get_by_role("button", name="Done editing Welding").click()
        expect(
            page.locator("#otypes-form [data-otype-row]").get_by_text("Enter a price between $0 and $500.")
        ).to_be_visible()
        expect(page.locator("#id_otypes-0-price")).to_be_visible()
        expect(page.get_by_role("button", name="Done editing Welding")).to_be_visible()
