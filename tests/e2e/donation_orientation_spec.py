"""Donation based orientations in a real browser (#636): the type editor's toggle and the member's amount input.

Drives the Alpine behaviour a Django client cannot see: the toggle swaps Price for the donation
fields, and the book modal starts from the suggestion. ``CAPTURE_636_SCREENSHOTS=1`` also saves
the PR's pictures under ``mockups/screenshots/``.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import reverse

from membership.models import Member, OrientationType
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

LEAD_EMAIL = "donation-lead-e2e@example.com"
MEMBER_EMAIL = "donation-member-e2e@example.com"
SHOTS = Path("mockups/screenshots")


def _member_for(email: str) -> Member:
    user, _ = get_user_model().objects.get_or_create(username=email, defaults={"email": email})
    return Member.objects.filter(user=user).first() or MemberFactory(user=user)


def _capture(page, name: str) -> None:
    if os.environ.get("CAPTURE_636_SCREENSHOTS"):
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / name), full_page=False)


def describe_donation_orientations():
    def it_swaps_price_for_the_donation_fields_when_staff_turn_it_on(page, live_server, login_via_code):
        guild = GuildFactory(name="Jewelry Guild", guild_lead=_member_for(LEAD_EMAIL))
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        orientation_type = OrientationTypeFactory(guild=guild, name="Bench Basics", price_cents=2500)
        login_via_code(LEAD_EMAIL)
        page.set_viewport_size({"width": 1100, "height": 900})
        page.goto(f"{live_server.url}{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations")

        price = page.locator("#id_otypes-0-price")
        minimum = page.locator("#id_otypes-0-donation_minimum")
        price.wait_for(state="visible")
        assert minimum.is_hidden()

        page.locator("label.pl-toggle:has(#id_otypes-0-is_donation)").click()
        minimum.wait_for(state="visible")
        assert price.is_hidden()
        assert page.locator("#id_otypes-0-donation_suggested").is_visible()
        # The editor autosaves; wait on the save that carries the suggestion, then read the row
        # once (polling it from here contends with the server's write on SQLite).
        save_path = reverse("hub_guild_orientation_types_save", args=[guild.pk])
        with page.expect_response(
            lambda r: (
                r.url.endswith(save_path)
                and r.request.method == "POST"
                and b'name="otypes-0-donation_suggested"\r\n\r\n15\r\n' in (r.request.post_data_buffer or b"")
            )
        ):
            page.locator("#id_otypes-0-donation_suggested").fill("15")
            page.locator("#id_otypes-0-donation_suggested").blur()
        saved = OrientationType.objects.get(pk=orientation_type.pk)
        assert (saved.is_donation, saved.donation_suggested_cents) == (True, 1500)
        tour_dismiss = page.get_by_role("button", name="No thanks")
        if tour_dismiss.is_visible():
            tour_dismiss.click()
        page.locator("#id_otypes-0-is_donation").evaluate("el => el.scrollIntoView({block: 'center'})")
        _capture(page, "636-type-form-toggle.png")

    def it_starts_the_members_amount_from_the_suggestion(page, live_server, login_via_code):
        guild = GuildFactory(name="Jewelry Guild")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        orientation_type = OrientationTypeFactory(
            guild=guild, name="Bench Basics", is_donation=True, donation_suggested_cents=1500
        )
        slot = OrientationSlotFactory(guild=guild, orientation_type=orientation_type)
        _member_for(MEMBER_EMAIL)
        login_via_code(MEMBER_EMAIL)
        page.set_viewport_size({"width": 1100, "height": 800})
        page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}?tab=orientations")

        chip = page.locator("#guild-orientation .pl-price-chip").first
        assert chip.inner_text() == "Donation, $15 suggested"
        page.locator(f'button[\\@click*="book-slot-{slot.pk}"]').first.click()
        amount = page.locator(f"#book-slot-{slot.pk}-amount")
        amount.wait_for(state="visible")
        assert amount.input_value() == "15"
        _capture(page, "636-member-amount.png")
