"""End-to-end: the Orientations page (#502 part 2) in a real browser.

A member books a slot from a card and lands back on the page with the card reading
Requested; a lead adds an orientation type on the guild editor, picks a photo on the
freshly added row and sees its preview (the row's cloned image field script has to run),
and the autosave stores it. Waits are on what the page shows. Run with ``pytest -m e2e``
on PostgreSQL.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import OrientationBooking, OrientationType
from tests.membership.factories import (
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
        guild = GuildFactory(name="Print Guild")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        login_via_code(ADMIN_EMAIL)
        user = get_user_model().objects.get(username=ADMIN_EMAIL)
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

        page.goto(f"{live_server.url}{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations")
        page.wait_for_function(ALPINE_READY)
        page.get_by_role("button", name="+ Add an orientation type").click()
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
