"""End-to-end: the Orientations page (#502 parts 2 and 3) in a real browser.

A member books a slot from a card and lands back on the page with the card reading
Requested; a lead adds an orientation on the guild's Orientations page, picks a photo on the
freshly added row and sees its preview (the row's cloned image field script has to run),
and the autosave stores it; the equipment editor's own "Add New Orientation +" row does the
same through its Save button. On the Calendar view (part 3) a member finds a seeded slot's
chip, follows its entry and lands on that type's card; the guild page's own calendar
still files its filters under the guild. Waits are on what the page shows. Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from hub.calendar_entries import ORIENTATION_PK_OFFSET

from membership.models import OrientationBooking, OrientationType
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

MEMBER_EMAIL = "orientations-page-member@example.com"
ADMIN_EMAIL = "orientations-page-admin@example.com"
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
ALPINE_READY = "() => !!(document.querySelector('[data-guild-autosave]') || {})._x_dataStack"
CALENDAR_READY = "() => !!(document.querySelector('.pl-calendar-page') || {})._x_dataStack"
SAVED_PAST = (
    "(n) => { const pill = document.querySelector('[data-save-pill]');"
    " return !!pill && Number(pill.dataset.saves) >= n && pill.textContent.trim() === 'Saved'; }"
)


def describe_the_orientations_page():
    def it_books_a_slot_from_a_card_and_lands_back_on_the_page(live_server, page, login_via_code):
        MembershipPlanFactory()
        guild = GuildFactory(name="Woodshop Guild")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        orientation_type = OrientationTypeFactory(guild=guild, name="Shop Basics")
        OrientationSlotFactory(guild=guild, orientation_type=orientation_type, enabled_settings=False)

        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_orientations')}")
        card = page.locator(f"#orientation-type-{orientation_type.pk}")
        card.get_by_role("button", name="Book", exact=True).click()
        page.get_by_role("button", name="Send request", exact=True).click()

        expect(card.locator(".pl-equip-badge--warn")).to_have_text("Requested")
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_orientations')}")
        assert OrientationBooking.objects.filter(
            orientation_type=orientation_type,
            member__user__username=MEMBER_EMAIL,
            status=OrientationBooking.Status.REQUESTED,
        ).exists()

    def it_previews_and_saves_a_photo_on_a_freshly_added_type_row(live_server, page, login_via_code):
        MembershipPlanFactory()
        # Hidden, so the Orientations card's add adds a row in place instead of opening the add page (#732).
        guild = GuildFactory(name="Print Guild", is_active=False)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        login_via_code(ADMIN_EMAIL)
        user = get_user_model().objects.get(username=ADMIN_EMAIL)
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

        page.goto(f"{live_server.url}{reverse('hub_guild_orientations', args=[guild.pk])}")
        page.wait_for_function(ALPINE_READY)
        page.locator("#otypes-form [data-formset-add]").click()
        page.locator('input[name="otypes-0-photo"]').set_input_files(
            {"name": "press.png", "mimeType": "image/png", "buffer": _PNG}
        )
        expect(page.locator("#image-preview-id_otypes-0-photo img")).to_be_visible()

        # The row posts once its name is filled; the chosen photo goes with it.
        page.locator('input[name="otypes-0-name"]').fill("Press Basics")
        expect(page.locator('input[name="otypes-0-id"]')).not_to_have_value("")
        page.wait_for_function(SAVED_PAST, arg=1)
        saved = OrientationType.objects.get(guild=guild, name="Press Basics")
        assert saved.photo.name.startswith("orientations/photos/")

    def it_previews_and_saves_a_photo_on_an_equipment_type_row_added_by_hand(live_server, page, login_via_code):
        # The equipment editor clones its row with its own inline handler, not the guild autosave,
        # so the row's image field script has a second place it must be re-run.
        MembershipPlanFactory()
        equipment = EquipmentFactory(name="Laser cutter")
        login_via_code(ADMIN_EMAIL)
        user = get_user_model().objects.get(username=ADMIN_EMAIL)
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

        page.goto(f"{live_server.url}{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation")
        page.get_by_role("button", name="Add New Orientation +").click()
        page.locator('input[name="otypes-0-photo"]').set_input_files(
            {"name": "laser.png", "mimeType": "image/png", "buffer": _PNG}
        )
        expect(page.locator("#image-preview-id_otypes-0-photo img")).to_be_visible()
        expect(page.locator("#image-upload-zone-id_otypes-0-photo .cls-image-upload-label")).to_have_text(
            "Replace image"
        )

        page.locator('input[name="otypes-0-name"]').fill("Laser Basics")
        page.locator("#equip-otype-rows").locator("..").get_by_role("button", name="Save").click()
        expect(page.get_by_text("Laser Basics").first).to_be_attached()
        saved = OrientationType.objects.get(equipment=equipment, name="Laser Basics")
        assert saved.photo.name.startswith("orientations/photos/")


def _tomorrow_morning() -> object:
    """Tomorrow at 10 local time: inside the calendar's current 4 week window on any day."""
    return timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)


def describe_the_orientations_calendar():
    def it_finds_a_slot_on_the_calendar_and_lands_on_its_card(live_server, page, login_via_code):
        MembershipPlanFactory()
        guild = GuildFactory(name="Woodworking Guild")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        orientation_type = OrientationTypeFactory(guild=guild, name="Shop Basics")
        starts_at = _tomorrow_morning()
        slot = OrientationSlotFactory(
            guild=guild,
            orientation_type=orientation_type,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            seats=3,
            enabled_settings=False,
        )

        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_orientations')}")
        # The List view loads no calendar until the member asks for it.
        expect(page.locator("#pl-calendar-events-area")).to_have_count(0)
        page.get_by_role("tab", name="Calendar").click()
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_orientations')}?view=calendar")
        expect(page.locator(".pl-calendar-filter", has_text="Woodworking Guild")).to_be_visible()
        page.wait_for_function(CALENDAR_READY)

        page.get_by_role("button", name="Month", exact=True).click()
        chip = page.locator(".pl-calendar-grid--month .pl-calendar-grid__chip", has_text="Shop Basics")
        expect(chip).to_be_visible()
        expect(chip.locator(".pl-calendar-grid__chip-title")).to_have_text("Shop Basics")

        # The legend filters on the page: off hides the chip, on brings it back.
        legend_chip = page.locator(".pl-calendar-filter", has_text="Woodworking Guild")
        legend_chip.click()
        expect(chip).to_be_hidden()
        legend_chip.click()
        expect(chip).to_be_visible()

        chip.click()
        item = page.locator(f'.pl-calendar-list__item[data-event-pk="{ORIENTATION_PK_OFFSET + slot.pk}"]:visible')
        expect(item).to_have_class(re.compile(r"pl-calendar-list__item--flash"))
        link = item.locator("a.pl-calendar-list__title--link")
        expect(link).to_have_text("Shop Basics · Woodworking Guild · 3 seats left")
        link.click()

        card = page.locator(f"#orientation-type-{orientation_type.pk}")
        expect(page).to_have_url(
            f"{live_server.url}{reverse('hub_orientations')}#orientation-type-{orientation_type.pk}"
        )
        expect(card).to_be_visible()
        expect(card).to_be_in_viewport()

    def it_opens_the_calendar_from_view_calendar_and_keeps_the_page_list_for_a_card_anchor(
        live_server, page, login_via_code
    ):
        MembershipPlanFactory()
        guild = GuildFactory(name="Print Guild")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        orientation_type = OrientationTypeFactory(guild=guild, name="Press Basics")

        login_via_code(MEMBER_EMAIL)
        # A card anchor wins over ?view=calendar: the entry links land on the List view.
        page.goto(
            f"{live_server.url}{reverse('hub_orientations')}?view=calendar#orientation-type-{orientation_type.pk}"
        )
        expect(page.locator(f"#orientation-type-{orientation_type.pk}")).to_be_visible()
        expect(page.get_by_role("tab", name="List")).to_have_attribute("aria-selected", "true")

        page.goto(f"{live_server.url}{reverse('hub_orientations')}?view=calendar")
        expect(page.locator(".pl-calendar-filter", has_text="Print Guild")).to_be_visible()
        expect(page.locator(f"#orientation-type-{orientation_type.pk}")).to_be_hidden()
        expect(page.get_by_role("tab", name="Calendar")).to_have_attribute("aria-selected", "true")

    def it_shrugs_off_a_malformed_hash(live_server, page, login_via_code):
        MembershipPlanFactory()
        login_via_code(MEMBER_EMAIL)
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{live_server.url}{reverse('hub_orientations')}?view=calendar#%")
        expect(page.get_by_role("tab", name="Calendar")).to_have_attribute("aria-selected", "true")
        page.get_by_role("tab", name="List").click()
        expect(page.get_by_role("tab", name="List")).to_have_attribute("aria-selected", "true")
        assert errors == []


def describe_the_guild_page_calendar():
    def it_still_files_its_filters_under_the_guild(live_server, page, login_via_code):
        MembershipPlanFactory()
        guild = GuildFactory(name="Ceramics Guild")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        starts_at = _tomorrow_morning()
        OrientationSlotFactory(
            guild=guild,
            orientation_type=OrientationTypeFactory(guild=guild),
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            enabled_settings=False,
        )

        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}")
        page.locator(".vote-tab", has_text="Guild Calendar").click()
        page.wait_for_function(CALENDAR_READY)
        page.get_by_role("button", name="Month", exact=True).click()
        chip = page.locator(".pl-calendar-grid--month .pl-calendar-grid__chip", has_text="Orientation")
        expect(chip).to_be_visible()

        page.locator(".pl-calendar-filter", has_text="Orientation").click()
        expect(chip).to_be_hidden()
        stored = page.evaluate(f"() => localStorage.getItem('guildCalFiltersOff-{guild.pk}')")
        assert stored == '["orientation"]'
        assert page.evaluate("() => localStorage.getItem('guildCalFiltersOff-orientations')") is None
