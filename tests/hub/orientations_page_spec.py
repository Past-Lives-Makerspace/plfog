"""BDD specs for the Orientations page (#502 part 2): one card per orientation a member can book.

Assertions anchor on markup (card ids, URLs, classes) and factory names, never on copy a
changelog entry could also carry (STANDARDS.md, Testing Traps).
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import SiteConfiguration
from membership.models import EXAMPLE_GUILD_SLUG, Member, OrientationBooking, OrientationType
from tests.billing.factories import LateCancellationFeeFactory
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityBlockFactory,
    OrientationBookingFactory,
    OrientationRecordFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
    tiny_png_bytes,
)

pytestmark = pytest.mark.django_db

PAGE = "/orientations/"


def _login(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    client.login(username=username, password="pass")
    return user


def _guild(name: str, **settings_kwargs: object) -> object:
    guild = GuildFactory(name=name)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True, **settings_kwargs)
    return guild


def _card(pk: int) -> bytes:
    return f'id="orientation-type-{pk}"'.encode()


def _card_html(content: bytes, pk: int) -> str:
    """One card's markup: from its id to the next card (or the end of the page)."""
    text = content.decode()
    start = text.index(f'id="orientation-type-{pk}"')
    end = text.find('id="orientation-type-', start + 1)
    return text[start : end if end != -1 else len(text)]


def _png(name: str = "photo.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, tiny_png_bytes(), "image/png")


def describe_the_page():
    def it_answers_a_plain_member_at_orientations(client: Client):
        _login(client, "op_open")
        response = client.get(PAGE)
        assert response.status_code == 200
        assert reverse("hub_orientations") == PAGE

    def it_requires_login(client: Client):
        response = client.get(PAGE)
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_moves_the_staff_dashboard_under_manage(client: Client):
        user = _login(client, "op_lead")
        guild = _guild("Dashboard Guild")
        guild.guild_lead = user.member
        guild.save()
        assert reverse("hub_orientations_dashboard") == "/orientations/manage/"
        assert reverse("hub_orientations_export") == "/orientations/manage/export/"
        assert reverse("hub_orientation_add_member") == "/orientations/manage/add-member/"
        assert (
            reverse("hub_orientation_toggle_completed", args=[7]) == "/orientations/manage/bookings/7/toggle-completed/"
        )
        assert client.get("/orientations/manage/").status_code == 200

    def it_still_403s_a_plain_member_on_the_dashboard(client: Client):
        _login(client, "op_dash_member")
        assert client.get("/orientations/manage/").status_code == 403

    def it_lists_cards_for_a_signed_in_account_with_no_member(client: Client):
        user = _login(client, "op_unlinked")
        Member.objects.filter(pk=user.member.pk).delete()
        orientation_type = OrientationTypeFactory(guild=_guild("Unlinked Guild"), name="Unlinked")
        response = client.get(PAGE)
        assert response.status_code == 200
        assert _card(orientation_type.pk) in response.content

    def it_shows_the_late_fee_notice_until_the_fee_is_paid(client: Client):
        user = _login(client, "op_fee")
        fee = LateCancellationFeeFactory(
            orientation_booking=OrientationBookingFactory(member=user.member, status="cancelled")
        )
        assert f'data-late-fee-notice="{fee.pk}"'.encode() in client.get(PAGE).content


def describe_which_cards_show():
    def it_lists_every_active_type_of_every_visible_enabled_guild_and_active_item(client: Client):
        _login(client, "op_list")
        wood = _guild("Wood Guild")
        shop = OrientationTypeFactory(guild=wood, name="Shop Basics")
        lathe = OrientationTypeFactory(guild=wood, name="Lathe Basics")
        laser = OrientationTypeFactory(equipment_owned=True, name="Laser Basics")
        content = client.get(PAGE).content
        for orientation_type in (shop, lathe, laser):
            assert _card(orientation_type.pk) in content

    def it_omits_disabled_guilds_inactive_guilds_inactive_items_and_inactive_types(client: Client):
        _login(client, "op_omit")
        disabled = GuildFactory(name="Disabled Guild")
        GuildOrientationSettingsFactory(guild=disabled, is_enabled=False)
        unset = GuildFactory(name="Unset Guild")  # no settings row at all
        inactive_guild = _guild("Sleeping Guild")
        inactive_guild.is_active = False
        inactive_guild.save()
        open_guild = _guild("Open Guild")
        hidden = [
            OrientationTypeFactory(guild=disabled, name="Off"),
            OrientationTypeFactory(guild=unset, name="Unset"),
            OrientationTypeFactory(guild=inactive_guild, name="Asleep"),
            OrientationTypeFactory(guild=open_guild, name="Retired", is_active=False),
            OrientationTypeFactory(guild=None, equipment=EquipmentFactory(is_active=False), name="Retired Item"),
        ]
        shown = OrientationTypeFactory(guild=open_guild, name="Live")
        content = client.get(PAGE).content
        assert _card(shown.pk) in content
        for orientation_type in hidden:
            assert _card(orientation_type.pk) not in content

    def it_shows_the_demo_guilds_cards_only_with_display_demo_guild(client: Client):
        _login(client, "op_demo")
        demo = GuildFactory(name="Cartographers Guild", slug=EXAMPLE_GUILD_SLUG, is_active=False)
        GuildOrientationSettingsFactory(guild=demo, is_enabled=True)
        demo_type = OrientationTypeFactory(guild=demo, name="Map Room")
        assert _card(demo_type.pk) not in client.get(PAGE).content
        config = SiteConfiguration.load()
        config.display_demo_guild = True
        config.save()
        assert _card(demo_type.pk) in client.get(PAGE).content

    def it_keeps_a_retired_type_the_member_holds_a_booking_on_with_its_state_only(client: Client):
        user = _login(client, "op_pinned")
        guild = _guild("Pinned Guild")
        retired = OrientationTypeFactory(guild=guild, name="Retired Pin")
        booking = OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=guild, orientation_type=retired), member=user.member
        )
        retired.is_active = False
        retired.save()
        card = _card_html(client.get(PAGE).content, retired.pk)
        assert f"cancel-my-orientation-{booking.pk}" in card
        assert "book-slot-" not in card

    def it_orders_cards_by_owner_name(client: Client):
        _login(client, "op_order")
        zebra = OrientationTypeFactory(guild=_guild("Zebra Guild"), name="Z Type")
        alpha = OrientationTypeFactory(guild=_guild("Alpha Guild"), name="A Type")
        middle = OrientationTypeFactory(guild=None, equipment=EquipmentFactory(name="Middle Saw"), name="M Type")
        content = client.get(PAGE).content
        assert content.index(_card(alpha.pk)) < content.index(_card(middle.pk)) < content.index(_card(zebra.pk))


def describe_filters():
    def it_filters_by_owner_chip(client: Client):
        _login(client, "op_chip")
        guild_type = OrientationTypeFactory(guild=_guild("Chip Guild"), name="Guild Kind")
        item_type = OrientationTypeFactory(equipment_owned=True, name="Item Kind")
        guilds_only = client.get(PAGE, {"owner": "guild"}).content
        assert _card(guild_type.pk) in guilds_only
        assert _card(item_type.pk) not in guilds_only
        items_only = client.get(PAGE, {"owner": "equipment"}).content
        assert _card(item_type.pk) in items_only
        assert _card(guild_type.pk) not in items_only
        assert b'pl-equip-chip pl-equip-chip--active">Equipment' in items_only

    def it_ignores_an_unknown_owner_value(client: Client):
        _login(client, "op_chip_bad")
        guild_type = OrientationTypeFactory(guild=_guild("Any Guild"), name="Any Kind")
        assert _card(guild_type.pk) in client.get(PAGE, {"owner": "spaceship"}).content

    def it_searches_type_names(client: Client):
        _login(client, "op_search")
        guild = _guild("Search Guild")
        lathe = OrientationTypeFactory(guild=guild, name="Lathe Zqx")
        kiln = OrientationTypeFactory(guild=guild, name="Kiln Wvu")
        content = client.get(PAGE, {"q": "zqx"}).content
        assert _card(lathe.pk) in content
        assert _card(kiln.pk) not in content

    def it_offers_clear_filters_when_nothing_matches(client: Client):
        _login(client, "op_nomatch")
        OrientationTypeFactory(guild=_guild("Nomatch Guild"), name="Real")
        content = client.get(PAGE, {"q": "no such orientation qqq"}).content
        assert b'class="hub-card pl-orient-empty"' in content
        assert f'<a href="{PAGE}">'.encode() in content

    def it_shows_the_empty_state_with_nothing_listed(client: Client):
        _login(client, "op_empty")
        content = client.get(PAGE).content
        assert b'class="hub-card pl-orient-empty"' in content
        assert b"pl-equip-grid" not in content


def describe_a_card():
    def it_shows_owner_name_duration_and_price(client: Client):
        _login(client, "op_meta")
        guild = _guild("Meta Guild")
        paid = OrientationTypeFactory(guild=guild, name="Paid Type", duration_minutes=75, price_cents=2500)
        free = OrientationTypeFactory(guild=guild, name="Free Type", duration_minutes=30)
        content = client.get(PAGE).content
        paid_card = _card_html(content, paid.pk)
        assert reverse("hub_guild_detail", args=[guild.slug]) in paid_card
        assert "Meta Guild" in paid_card
        assert "Paid Type" in paid_card
        assert "75 min" in paid_card
        assert '<span class="pl-price-chip">$25</span>' in paid_card
        assert "pl-price-chip" not in _card_html(content, free.pk)

    def it_names_the_item_and_its_guild_on_an_equipment_card(client: Client):
        _login(client, "op_item")
        guild = GuildFactory(name="Item Guild")
        equipment = EquipmentFactory(name="Bandsaw Qq", guild=guild)
        item_type = OrientationTypeFactory(guild=None, equipment=equipment, name="Bandsaw Basics")
        card = _card_html(client.get(PAGE).content, item_type.pk)
        assert f'href="{reverse("hub_equipment_detail", args=[equipment.slug])}" class="hub-badge"' in card
        assert '<span class="pl-orient-card__owner-guild">Item Guild</span>' in card


def describe_times_on_a_card():
    def it_lists_the_next_three_slots_and_links_more_times_when_a_fourth_exists(client: Client):
        _login(client, "op_slots")
        guild = _guild("Slot Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Slotted")
        now = timezone.now()
        slots = [
            OrientationSlotFactory(
                guild=guild,
                orientation_type=orientation_type,
                starts_at=now + timedelta(days=day),
                ends_at=now + timedelta(days=day, hours=1),
            )
            for day in (4, 2, 3, 5)
        ]
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        first_three = sorted(slots, key=lambda slot: slot.starts_at)[:3]
        for slot in first_three:
            assert f"book-slot-{slot.pk}" in card
        assert f"book-slot-{slots[3].pk}" not in card
        more = orientation_type.orientation_anchor_path().replace("&", "&amp;")
        assert f'href="{more}" class="pl-orient-card__more"' in card

    def it_skips_the_more_times_link_with_three_or_fewer(client: Client):
        _login(client, "op_three")
        guild = _guild("Three Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Three")
        OrientationSlotFactory(guild=guild, orientation_type=orientation_type)
        assert "pl-orient-card__more" not in _card_html(client.get(PAGE).content, orientation_type.pk)

    def it_leaves_a_full_slot_off_the_card(client: Client):
        _login(client, "op_full")
        guild = _guild("Full Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Fully")
        full = OrientationSlotFactory(guild=guild, orientation_type=orientation_type, seats=1)
        OrientationBookingFactory(slot=full, member=MemberFactory())
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert f"book-slot-{full.pk}" not in card

    def it_posts_next_with_a_slot_booking(client: Client):
        _login(client, "op_slot_next")
        guild = _guild("Next Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Nexted")
        OrientationSlotFactory(guild=guild, orientation_type=orientation_type)
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert f'<input type="hidden" name="next" value="{PAGE}">' in card

    def it_lists_an_open_window_with_pick_a_time(client: Client):
        _login(client, "op_block")
        guild = _guild("Window Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Windowed")
        block = OrientationAvailabilityBlockFactory(guild=guild)
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        starts_url = reverse("hub_orientation_block_starts", args=[block.pk, orientation_type.pk])
        assert f'hx-get="{starts_url}?next=/orientations/"' in card
        assert f'id="block-pick-{orientation_type.pk}-body"' in card

    def it_leaves_out_a_window_with_no_room_for_the_type(client: Client):
        _login(client, "op_block_short")
        guild = _guild("Short Window Guild")
        long_type = OrientationTypeFactory(guild=guild, name="Too Long", duration_minutes=240)
        block = OrientationAvailabilityBlockFactory(guild=guild)  # three hours
        card = _card_html(client.get(PAGE).content, long_type.pk)
        assert reverse("hub_orientation_block_starts", args=[block.pk, long_type.pk]) not in card

    def it_reads_paused_for_a_closed_guild_with_no_buttons(client: Client):
        _login(client, "op_closed")
        guild = _guild("Closed Guild", is_closed=True, closed_message="Back after the kiln rebuild qq.")
        orientation_type = OrientationTypeFactory(guild=guild, name="Kiln")
        OrientationSlotFactory(guild=guild, orientation_type=orientation_type, enabled_settings=False)
        OrientationAvailabilityBlockFactory(guild=guild)
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert "pl-equip-badge--muted" in card
        assert "Back after the kiln rebuild qq." in card
        assert "book-slot-" not in card
        assert "hub_orientation_block_starts" not in card
        assert "block-pick-" not in card

    def it_reads_paused_for_closed_equipment(client: Client):
        _login(client, "op_closed_item")
        equipment = EquipmentFactory(is_closed=True, closed_message="Down for a new blade qq.")
        orientation_type = OrientationTypeFactory(guild=None, equipment=equipment, name="Blade")
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert "pl-equip-badge--muted" in card
        assert "Down for a new blade qq." in card


def describe_completed_cards():
    def it_folds_completed_types_into_the_disclosure(client: Client):
        user = _login(client, "op_done")
        guild = _guild("Done Guild")
        done = OrientationTypeFactory(guild=guild, name="Done Type")
        open_type = OrientationTypeFactory(guild=guild, name="Open Type")
        OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=guild, orientation_type=done), member=user.member
        ).mark_completed()
        content = client.get(PAGE).content.decode()
        disclosure = content.index('class="pl-disclosure pl-orient-done"')
        assert content.index(f'id="orientation-type-{done.pk}"') > disclosure
        assert content.index(f'id="orientation-type-{open_type.pk}"') < disclosure

    def it_says_recorded_on_for_a_hand_entered_record(client: Client):
        user = _login(client, "op_record")
        guild = _guild("Record Guild")
        recorded = OrientationTypeFactory(guild=guild, name="Recorded Type")
        OrientationRecordFactory(member=user.member, orientation_type=recorded)
        card = _card_html(client.get(PAGE).content, recorded.pk)
        assert f"Recorded on {timezone.localdate().strftime('%b')}" in card


def describe_member_state_on_a_card():
    def it_shows_a_requested_booking_with_cancel(client: Client):
        user = _login(client, "op_requested")
        guild = _guild("Requested Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Requested Type")
        booking = OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=guild, orientation_type=orientation_type), member=user.member
        )
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert '<span class="pl-equip-badge pl-equip-badge--warn">Requested</span>' in card
        assert f"cancel-my-orientation-{booking.pk}" in card
        assert f'<input type="hidden" name="next" value="{PAGE}">' in card

    def it_shows_a_confirmed_booking(client: Client):
        user = _login(client, "op_confirmed")
        guild = _guild("Confirmed Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Confirmed Type")
        OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=guild, orientation_type=orientation_type),
            member=user.member,
            status=OrientationBooking.Status.CONFIRMED,
        )
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert '<span class="pl-equip-badge pl-equip-badge--ok">Confirmed</span>' in card

    def it_shows_a_checkout_hold_with_resume_and_cancel(client: Client):
        user = _login(client, "op_hold")
        guild = _guild("Hold Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Hold Type", price_cents=1500)
        hold = OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=guild, orientation_type=orientation_type),
            member=user.member,
            status=OrientationBooking.Status.PENDING_PAYMENT,
        )
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert reverse("hub_orientation_checkout_resume", args=[hold.pk]) in card
        assert f"cancel-orientation-hold-{hold.pk}" in card


def describe_schedule_an_orientation():
    def it_offers_it_on_a_card_with_no_times_when_the_guild_allows_custom_requests(client: Client):
        _login(client, "op_custom")
        guild = _guild("Custom Guild", allow_custom_requests=True)
        orientation_type = OrientationTypeFactory(guild=guild, name="Untimed")
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert reverse("hub_guild_orientation_request_custom", args=[guild.pk]) in card
        assert f'<input type="hidden" name="orientation_type" value="{orientation_type.pk}">' in card
        assert f'id="id_custom_{orientation_type.pk}_starts_at"' in card

    def it_leaves_it_off_a_card_with_times(client: Client):
        _login(client, "op_custom_timed")
        guild = _guild("Timed Guild", allow_custom_requests=True)
        orientation_type = OrientationTypeFactory(guild=guild, name="Timed")
        OrientationSlotFactory(guild=guild, orientation_type=orientation_type)
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert reverse("hub_guild_orientation_request_custom", args=[guild.pk]) not in card

    def it_leaves_it_off_when_the_guild_takes_no_custom_requests(client: Client):
        _login(client, "op_custom_off")
        guild = _guild("No Custom Guild", allow_custom_requests=False)
        orientation_type = OrientationTypeFactory(guild=guild, name="No Custom")
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert "pl-orient-card__note" in card
        assert reverse("hub_guild_orientation_request_custom", args=[guild.pk]) not in card

    def it_shows_only_the_sentence_on_an_equipment_card_with_nothing_posted(client: Client):
        _login(client, "op_custom_item")
        orientation_type = OrientationTypeFactory(equipment_owned=True, name="Bare Item")
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert "pl-orient-card__note" in card
        assert "request-custom" not in card
        assert "showCustom" not in card

    def it_never_says_none_of_these_times_work(client: Client):
        _login(client, "op_none_work")
        guild = _guild("Phrase Guild", allow_custom_requests=True)
        timed = OrientationTypeFactory(guild=guild, name="Phrase Timed")
        OrientationSlotFactory(guild=guild, orientation_type=timed)
        OrientationTypeFactory(guild=guild, name="Phrase Untimed")
        assert b"None of these times work" not in client.get(PAGE).content


def describe_card_image():
    def it_uses_the_types_own_photo_first(client: Client):
        _login(client, "op_img_own")
        guild = _guild("Own Photo Guild")
        guild.banner_image = _png("banner.png")
        guild.save()
        orientation_type = OrientationTypeFactory(guild=guild, name="Own Photo")
        orientation_type.photo = _png("own.png")
        orientation_type.save()
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert orientation_type.photo.url in card
        assert "object-position: 50% 50%" in card

    def it_falls_back_to_the_equipment_photo(client: Client):
        _login(client, "op_img_item")
        guild = GuildFactory(name="Item Banner Guild", banner_image=_png("banner.png"))
        equipment = EquipmentFactory(guild=guild, photo=_png("saw.png"))
        orientation_type = OrientationTypeFactory(guild=None, equipment=equipment, name="Item Photo")
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert equipment.photo.url in card

    def it_falls_back_to_the_guild_banner(client: Client):
        _login(client, "op_img_banner")
        guild = _guild("Banner Guild")
        guild.banner_image = _png("banner.png")
        guild.hero_crop_x, guild.hero_crop_y = 20, 70
        guild.save()
        orientation_type = OrientationTypeFactory(guild=guild, name="Banner Photo")
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert guild.banner_image.url in card
        assert "object-position: 20% 70%" in card

    def it_reads_one_owners_crop_once_for_all_its_cards(client: Client):
        _login(client, "op_img_shared")
        guild = _guild("Shared Banner Guild")
        guild.banner_image = _png("banner.png")
        guild.save()
        first = OrientationTypeFactory(guild=guild, name="First Shared")
        second = OrientationTypeFactory(guild=guild, name="Second Shared")
        with mock.patch.object(
            type(guild), "hero_object_position", new_callable=mock.PropertyMock, return_value="30% 40%"
        ) as position:
            content = client.get(PAGE).content
        assert position.call_count == 1
        for orientation_type in (first, second):
            assert "object-position: 30% 40%" in _card_html(content, orientation_type.pk)

    def it_uses_the_equipments_guild_banner_when_the_item_has_no_photo(client: Client):
        _login(client, "op_img_item_banner")
        guild = GuildFactory(name="Item Owner Guild", banner_image=_png("banner.png"))
        orientation_type = OrientationTypeFactory(
            guild=None, equipment=EquipmentFactory(guild=guild), name="Item Banner"
        )
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert guild.banner_image.url in card

    def it_shows_the_placeholder_with_no_picture_anywhere(client: Client):
        _login(client, "op_img_none")
        orientation_type = OrientationTypeFactory(guild=_guild("Plain Guild"), name="Plain")
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert "pl-equip-card__placeholder" in card
        assert "pl-equip-card__photo" not in card


def describe_query_count():
    def _seed(member: Member, indexes: range) -> None:
        """One type per index, spread over guilds and equipment, each with slots, windows and state."""
        for index in indexes:
            if index % 2:
                orientation_type = OrientationTypeFactory(
                    guild=None,
                    equipment=EquipmentFactory(guild=GuildFactory(name=f"Owner {index}"), photo=_png()),
                    name=f"Item Type {index}",
                    price_cents=1000,
                )
                OrientationSlotFactory(guild=None, orientation_type=orientation_type)
            else:
                guild = GuildFactory(name=f"Counted Guild {index}", banner_image=_png())
                GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
                orientation_type = OrientationTypeFactory(guild=guild, name=f"Guild Type {index}")
                orienter = MemberFactory()
                GuildStaffMembershipFactory(guild=guild, member=orienter)
                OrientationSlotFactory(guild=guild, orientation_type=orientation_type, orienter=orienter)
                OrientationAvailabilityBlockFactory(guild=guild, orienter=orienter)
            if index == 0:
                OrientationRecordFactory(member=member, orientation_type=orientation_type)
            if index == 2:
                OrientationBookingFactory(
                    slot=OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type),
                    member=member,
                )

    def it_costs_the_same_with_one_type_as_with_six(client: Client, django_assert_num_queries):
        user = _login(client, "op_queries_one")
        _seed(user.member, range(1))
        client.get(PAGE)  # warm the per process caches (site configuration, feature switches)
        with CaptureQueriesContext(connection) as one:
            assert client.get(PAGE).status_code == 200
        _seed(user.member, range(1, 6))
        assert OrientationType.objects.count() == 6
        with django_assert_num_queries(len(one.captured_queries)):
            response = client.get(PAGE)
        assert response.content.count(b'id="orientation-type-') == 6


def describe_coming_back_to_the_page():
    def it_books_a_slot_through_the_same_service_and_lands_back(client: Client):
        user = _login(client, "op_back_slot")
        slot = OrientationSlotFactory(guild=_guild("Back Slot Guild"))
        with mock.patch("membership.orientations.request_orientation") as request_orientation:
            response = client.post(reverse("hub_orientation_book", args=[slot.pk]), {"next": PAGE})
        request_orientation.assert_called_once_with(slot, user.member, note="")
        assert response.status_code == 302
        assert response["Location"] == PAGE

    def it_picks_a_window_start_through_the_same_service_and_lands_back(client: Client):
        user = _login(client, "op_back_block")
        guild = _guild("Back Block Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Back Block")
        block = OrientationAvailabilityBlockFactory(guild=guild)
        start = block.valid_starts_for(orientation_type)[0]
        with mock.patch("membership.orientations.request_block_orientation") as request_block:
            response = client.post(
                reverse("hub_orientation_block_book", args=[block.pk]),
                {
                    "orientation_type": orientation_type.pk,
                    "starts_at": timezone.localtime(start).strftime("%Y-%m-%dT%H:%M"),
                    "next": PAGE,
                },
            )
        request_block.assert_called_once()
        assert request_block.call_args.args[:2] == (block, user.member)
        assert response["Location"] == PAGE

    def it_requests_a_custom_time_through_the_same_service_and_lands_back(client: Client):
        user = _login(client, "op_back_custom")
        guild = _guild("Back Custom Guild", allow_custom_requests=True)
        orientation_type = OrientationTypeFactory(guild=guild, name="Back Custom")
        starts = timezone.localtime(timezone.now() + timedelta(days=5)).replace(second=0, microsecond=0)
        with mock.patch("membership.orientations.request_custom_orientation") as request_custom:
            response = client.post(
                reverse("hub_guild_orientation_request_custom", args=[guild.pk]),
                {"orientation_type": orientation_type.pk, "starts_at": starts.strftime("%Y-%m-%dT%H:%M"), "next": PAGE},
            )
        request_custom.assert_called_once()
        assert request_custom.call_args.args[:2] == (guild, user.member)
        assert request_custom.call_args.kwargs["orientation_type"] == orientation_type
        assert response["Location"] == PAGE

    def it_cancels_through_the_same_service_and_lands_back(client: Client):
        user = _login(client, "op_back_cancel")
        booking = OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=_guild("Back Cancel Guild")), member=user.member
        )
        with mock.patch("membership.orientations.cancel_orientation", return_value=None) as cancel:
            response = client.post(reverse("hub_orientation_cancel_mine", args=[booking.pk]), {"next": PAGE})
        cancel.assert_called_once()
        assert cancel.call_args.args == (booking,)
        assert response["Location"] == PAGE

    def it_cancels_a_hold_through_the_same_service_and_lands_back(client: Client):
        user = _login(client, "op_back_hold")
        hold = OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=_guild("Back Hold Guild")),
            member=user.member,
            status=OrientationBooking.Status.PENDING_PAYMENT,
        )
        with mock.patch("membership.orientations.release_hold_if_unpaid", return_value="released") as release:
            response = client.post(reverse("hub_orientation_checkout_cancel_hold", args=[hold.pk]), {"next": PAGE})
        release.assert_called_once_with(hold)
        assert response["Location"] == PAGE

    def it_falls_back_to_the_owner_page_for_a_next_on_another_host(client: Client):
        _login(client, "op_back_evil")
        slot = OrientationSlotFactory(guild=_guild("Evil Next Guild"))
        with mock.patch("membership.orientations.request_orientation"):
            response = client.post(
                reverse("hub_orientation_book", args=[slot.pk]), {"next": "https://evil.example/orientations/"}
            )
        assert response["Location"] == slot.orientation_type.orientation_anchor_path()

    def it_falls_back_to_the_guild_page_for_a_refused_custom_request_without_next(client: Client):
        _login(client, "op_back_guild")
        guild = _guild("Refused Guild", allow_custom_requests=False)
        response = client.post(reverse("hub_guild_orientation_request_custom", args=[guild.pk]), {})
        assert response["Location"] == reverse("hub_guild_detail", args=[guild.slug])

    def it_carries_a_local_next_into_the_pick_a_time_form(client: Client):
        _login(client, "op_back_starts")
        guild = _guild("Starts Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Starts")
        block = OrientationAvailabilityBlockFactory(guild=guild)
        url = reverse("hub_orientation_block_starts", args=[block.pk, orientation_type.pk])
        local = client.get(url, {"next": PAGE}).content
        assert f'<input type="hidden" name="next" value="{PAGE}">'.encode() in local
        foreign = client.get(url, {"next": "https://evil.example/"}).content
        assert b'name="next"' not in foreign


def describe_a_card_whose_times_are_all_full():
    def _all_full(guild: object) -> OrientationType:
        orientation_type = OrientationTypeFactory(guild=guild, name="Packed")
        full = OrientationSlotFactory(guild=guild, orientation_type=orientation_type, seats=1)
        OrientationBookingFactory(slot=full, member=MemberFactory())
        return orientation_type

    def it_says_so_links_the_owner_page_and_keeps_custom_requests(client: Client):
        _login(client, "op_full_custom")
        guild = _guild("Packed Custom Guild", allow_custom_requests=True)
        orientation_type = _all_full(guild)
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert "Every posted time is full." in card
        assert "No times are posted yet." not in card
        more = orientation_type.orientation_anchor_path().replace("&", "&amp;")
        assert f'href="{more}" class="pl-orient-card__more"' in card
        assert reverse("hub_guild_orientation_request_custom", args=[guild.pk]) in card

    def it_says_so_and_links_the_owner_page_without_custom_requests(client: Client):
        _login(client, "op_full_plain")
        guild = _guild("Packed Plain Guild", allow_custom_requests=False)
        orientation_type = _all_full(guild)
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert "Every posted time is full." in card
        more = orientation_type.orientation_anchor_path().replace("&", "&amp;")
        assert f'href="{more}" class="pl-orient-card__more"' in card
        assert "showCustom" not in card

    def it_does_not_claim_full_when_an_open_time_remains(client: Client):
        _login(client, "op_full_partly")
        guild = _guild("Partly Full Guild")
        orientation_type = _all_full(guild)
        open_slot = OrientationSlotFactory(guild=guild, orientation_type=orientation_type)
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert f"book-slot-{open_slot.pk}" in card
        assert "Every posted time is full." not in card


def describe_the_manage_action():
    def it_links_the_dashboard_for_a_guild_lead(client: Client):
        user = _login(client, "op_manage_lead")
        GuildFactory(name="Managed Guild", guild_lead=user.member)
        content = client.get(PAGE).content.decode()
        assert (
            f'<a href="{reverse("hub_orientations_dashboard")}" class="hub-btn hub-btn--sm">Manage orientations</a>'
            in content
        )

    def it_is_absent_for_a_plain_member(client: Client):
        _login(client, "op_manage_member")
        assert b">Manage orientations</a>" not in client.get(PAGE).content


def describe_orienter_names_on_a_card():
    def it_adds_a_last_initial_when_two_of_a_guilds_leadership_share_a_first_name(client: Client):
        _login(client, "op_names")
        guild = _guild("Names Guild")
        bob_p = MemberFactory(preferred_name="Bob Placeholder")
        bob_q = MemberFactory(preferred_name="Bob Quill")
        guild.guild_lead = bob_p
        guild.save()
        GuildStaffMembershipFactory(guild=guild, member=bob_q)
        orientation_type = OrientationTypeFactory(guild=guild, name="Named")
        OrientationSlotFactory(guild=guild, orientation_type=orientation_type, orienter=bob_p)
        OrientationSlotFactory(guild=guild, orientation_type=orientation_type, orienter=bob_q)
        card = _card_html(client.get(PAGE).content, orientation_type.pk)
        assert "<small>with Bob P.</small>" in card
        assert "<small>with Bob Q.</small>" in card

    def it_keeps_the_plain_first_name_when_it_is_unique(client: Client):
        _login(client, "op_names_unique")
        guild = _guild("Unique Names Guild")
        amber = MemberFactory(preferred_name="Amber Lane")
        GuildStaffMembershipFactory(guild=guild, member=amber)
        orientation_type = OrientationTypeFactory(guild=guild, name="Unique")
        OrientationSlotFactory(guild=guild, orientation_type=orientation_type, orienter=amber)
        assert "<small>with Amber</small>" in _card_html(client.get(PAGE).content, orientation_type.pk)

    def it_heads_each_card_with_an_h2(client: Client):
        _login(client, "op_h2")
        orientation_type = OrientationTypeFactory(guild=_guild("Heading Guild"), name="Heading Type")
        assert '<h2 class="pl-orient-card__name">Heading Type</h2>' in _card_html(
            client.get(PAGE).content, orientation_type.pk
        )
