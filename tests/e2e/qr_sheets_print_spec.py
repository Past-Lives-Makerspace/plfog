"""End-to-end: the equipment and orientation QR sheets (#631) print on one Letter page.

A tool's staff member opens each sheet in a real browser, in a light and a dark colour
scheme. The sheet stays black on white whatever the scheme, the toolbar is hidden in print
media, and Chromium's Letter PDF of it is exactly one page. Run with ``pytest -m e2e`` on
PostgreSQL.
"""

from __future__ import annotations

import io
import re

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image, ImageOps
from playwright.sync_api import Page, expect

from membership.models import Equipment, OrientationType
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
)

STAFF_EMAIL = "qr-sheet-staff@example.com"
LONG_ABOUT = " ".join(["Cuts tight curves and resaws boards up to twelve inches tall."] * 8)
PDF_PAGE = re.compile(rb"/Type\s*/Page(?!s)")


def _photo(name: str) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    shade = Image.linear_gradient("L").rotate(90).resize((1600, 900))
    ImageOps.colorize(shade, black=(52, 36, 22), white=(196, 150, 98)).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


def _world(login_via_code) -> tuple[Equipment, OrientationType]:
    """A staff member signed in on a gated, photographed bandsaw with a paid orientation of its own."""
    MembershipPlanFactory()
    login_via_code(STAFF_EMAIL)
    staff = get_user_model().objects.get(username=STAFF_EMAIL).member
    equipment = EquipmentFactory(
        name="Laguna Fourteen Inch Bandsaw",
        guild=GuildFactory(name="Print Sheet Woodshop"),
        photo=_photo("bandsaw.jpg"),
        description=LONG_ABOUT,
        location_note="Back corner of the wood shop, by the dust collector",
    )
    EquipmentStaffMembershipFactory(equipment=equipment, member=staff)
    orientation_type = OrientationTypeFactory(
        guild=None,
        equipment=equipment,
        name="Bandsaw Safety Checkout",
        description=LONG_ABOUT,
        price_cents=3500,
        default_location="Wood shop bench two",
        photo=_photo("checkout.jpg"),
    )
    equipment.required_orientation = orientation_type
    equipment.save(update_fields=["required_orientation"])
    return equipment, orientation_type


def _assert_one_black_on_white_letter_page(page: Page, url: str, scheme: str) -> None:
    page.emulate_media(media="screen", color_scheme=scheme)
    page.goto(url)
    sheet = page.locator("main.pl-flyer")
    expect(sheet).to_be_visible()
    expect(sheet.locator(".pl-flyer__qr svg").first).to_be_visible()
    page.wait_for_function("() => [...document.images].every((img) => img.complete && img.naturalWidth > 0)")

    page.emulate_media(media="print", color_scheme=scheme)
    expect(page.locator(".pl-flyer-toolbar")).to_be_hidden()
    colours = page.evaluate(
        """() => ({
            sheet: getComputedStyle(document.querySelector('main.pl-flyer')).backgroundColor,
            body: getComputedStyle(document.body).backgroundColor,
            name: getComputedStyle(document.querySelector('.pl-flyer__name')).color,
        })"""
    )
    assert colours == {"sheet": "rgb(255, 255, 255)", "body": "rgb(255, 255, 255)", "name": "rgb(9, 46, 76)"}

    pdf = page.pdf(format="Letter", print_background=True)
    assert len(PDF_PAGE.findall(pdf)) == 1, f"{url} in {scheme} printed {len(PDF_PAGE.findall(pdf))} pages"


def describe_the_qr_sheets_in_print():
    @pytest.mark.parametrize("scheme", ["light", "dark"])
    def it_prints_the_equipment_sheet_on_one_letter_page(live_server, page: Page, login_via_code, serve_media, scheme):
        equipment, _ = _world(login_via_code)
        url = f"{live_server.url}{reverse('hub_equipment_flyer', args=[equipment.slug])}"
        _assert_one_black_on_white_letter_page(page, url, scheme)
        # Both QRs made it onto the page: the tool, and its orientation.
        expect(page.locator("[data-qr-target]")).to_have_count(2)

    @pytest.mark.parametrize("scheme", ["light", "dark"])
    def it_prints_the_orientation_sheet_on_one_letter_page(
        live_server, page: Page, login_via_code, serve_media, scheme
    ):
        _, orientation_type = _world(login_via_code)
        url = f"{live_server.url}{reverse('hub_orientation_type_flyer', args=[orientation_type.pk])}"
        _assert_one_black_on_white_letter_page(page, url, scheme)
        expect(page.locator("[data-qr-sheet-price]")).to_contain_text("$35")
