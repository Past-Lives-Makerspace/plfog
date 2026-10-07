"""BDD specs for the Equipment directory views (equipment-reservations spec PR 1).

Index (filters, badges, empty states), detail (one-state requirements banner, the
orientation deep link), the admin-gated add form, and the manage panel (Details +
Staff) — including crafted-POST permission probes for every gated endpoint.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub.forms import EquipmentForm
from membership.equipment import reserve
from membership.models import (
    AdminCapability,
    Equipment,
    EquipmentError,
    EquipmentStaffMembership,
    Member,
    OrientationBooking,
    OrientationType,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationRecordFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
    tiny_png_bytes,
)

pytestmark = pytest.mark.django_db


def _member_user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    if not member.full_legal_name:
        member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    return user


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    user = _member_user(username, fog_role=fog_role)
    client.login(username=username, password="pass")
    return user


def describe_equipment_index():
    def it_requires_login(client: Client):
        response = client.get(reverse("hub_equipment_index"))
        assert response.status_code == 302
        assert "/login" in response["Location"] or "/accounts/" in response["Location"]

    def it_shows_the_empty_state_when_nothing_is_bookable(client: Client):
        _login(client, "eq_empty")
        response = client.get(reverse("hub_equipment_index"))
        assert response.status_code == 200
        assert b"Nothing is bookable yet. Check back soon." in response.content

    def it_hides_inactive_equipment(client: Client):
        _login(client, "eq_retired")
        EquipmentFactory(name="Old Bandsaw", is_active=False)
        response = client.get(reverse("hub_equipment_index"))
        assert b"Old Bandsaw" not in response.content
        assert b"Nothing is bookable yet. Check back soon." in response.content

    def it_renders_cards_with_access_badges(client: Client):
        user = _login(client, "eq_badges")
        EquipmentFactory(name="Open Bench")
        EquipmentFactory(name="Gated Lathe", unlocking_orientations=[OrientationTypeFactory(name="Lathe")])
        response = client.get(reverse("hub_equipment_index"))
        assert b"You're all set" in response.content
        assert b"Orientation needed" in response.content
        assert user is not None  # the badge set proves the bulk access sets flowed through

    def it_flags_every_card_with_the_fee_warning_while_a_fee_is_unpaid(client: Client):
        """The block until paid (#456, part 2): the index card never says all set while the detail page blocks."""
        from tests.billing.factories import LateCancellationFeeFactory
        from tests.membership.factories import OrientationBookingFactory

        user = _login(client, "eq_idx_fee")
        EquipmentFactory(name="Open Bench")
        EquipmentFactory(name="Gated Lathe", unlocking_orientations=[OrientationTypeFactory(name="Lathe")])
        LateCancellationFeeFactory(
            orientation_booking=OrientationBookingFactory(member=user.member, status="cancelled")
        )
        response = client.get(reverse("hub_equipment_index"))
        content = response.content.decode()
        assert content.count("pl-equip-badge--warn") == 2
        assert "pl-equip-badge--ok" not in content
        assert "Pay your late cancellation fee to book again" in content
        assert {card["access_state"] for card in response.context["cards"]} == {"needs_fee"}

    def it_looks_the_fee_up_once_for_the_whole_grid(client: Client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from tests.billing.factories import LateCancellationFeeFactory
        from tests.membership.factories import OrientationBookingFactory

        user = _login(client, "eq_idx_fee_queries")
        for name in ("Bench A", "Bench B", "Bench C"):
            EquipmentFactory(name=name)
        url = reverse("hub_equipment_index")

        def count_queries() -> int:
            client.get(url)  # warm the session and per-request caches so both samples are steady state
            with CaptureQueriesContext(connection) as ctx:
                assert client.get(url).status_code == 200
            return len(ctx.captured_queries)

        without_fee = count_queries()
        LateCancellationFeeFactory(
            orientation_booking=OrientationBookingFactory(member=user.member, status="cancelled")
        )
        assert count_queries() <= without_fee + 1

    def it_shows_a_running_orientation_as_reserved_on_the_card(client: Client, midday_now):
        from datetime import time, timedelta

        from django.utils import timezone

        from tests.membership.factories import EquipmentHoursFactory, OrientationBookingFactory, OrientationSlotFactory

        _login(client, "eq_card_orient")
        busy_tool = EquipmentFactory(name="Busy Router")
        free_tool = EquipmentFactory(name="Free Router")
        for equipment in (busy_tool, free_tool):
            EquipmentHoursFactory(
                equipment=equipment,
                weekday=timezone.localtime().weekday(),
                start_time=time(0, 0),
                end_time=time(23, 59),
            )
            slot = OrientationSlotFactory(
                equipment_owned=True,
                orientation_type=OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Basics"),
                starts_at=timezone.now() - timedelta(minutes=30),
                ends_at=timezone.now() + timedelta(minutes=30),
                seats=1,
            )
            if equipment is busy_tool:
                OrientationBookingFactory(slot=slot)
        response = client.get(reverse("hub_equipment_index"))
        cards = {card["equipment"].name: card["availability"] for card in response.context["cards"]}
        assert cards["Busy Router"][0] == "busy"
        assert cards["Busy Router"][1].startswith("Reserved until ")
        assert cards["Free Router"] == ("free", "Available now")
        assert b"Reserved until " in response.content

    def it_shows_membership_inactive_badges_to_a_member_who_is_not_active(client: Client):
        user = _login(client, "eq_former")
        user.member.status = Member.Status.INVITED
        user.member.save(update_fields=["status"])
        EquipmentFactory(name="Open Bench")
        response = client.get(reverse("hub_equipment_index"))
        assert b"Membership inactive" in response.content

    def describe_filters():
        def it_filters_by_guild_slug(client: Client):
            _login(client, "eq_fg")
            woodshop = GuildFactory(name="Woodshop")
            EquipmentFactory(name="Table Saw", guild=woodshop)
            EquipmentFactory(name="Kiln", guild=GuildFactory(name="Ceramics"))
            response = client.get(reverse("hub_equipment_index"), {"guild": woodshop.slug})
            assert b"Table Saw" in response.content
            assert b"Kiln" not in response.content

        def it_filters_standalone(client: Client):
            _login(client, "eq_fs")
            EquipmentFactory(name="Table Saw", guild=GuildFactory())
            EquipmentFactory(name="House Printer", guild=None)
            response = client.get(reverse("hub_equipment_index"), {"guild": "standalone"})
            assert b"House Printer" in response.content
            assert b"Table Saw" not in response.content

        def it_filters_by_kind(client: Client):
            _login(client, "eq_fk")
            EquipmentFactory(name="Table Saw", kind=Equipment.Kind.TOOL)
            EquipmentFactory(name="Media Room", kind=Equipment.Kind.ROOM)
            response = client.get(reverse("hub_equipment_index"), {"kind": "room"})
            assert b"Media Room" in response.content
            assert b"Table Saw" not in response.content

        def it_filters_to_spaces(client: Client):
            # #502: Space is the third kind; the view's `kind in Kind.values` gate admits it as is.
            _login(client, "eq_fspace")
            EquipmentFactory(name="Loading Dock", kind=Equipment.Kind.SPACE)
            EquipmentFactory(name="Table Saw", kind=Equipment.Kind.TOOL)
            response = client.get(reverse("hub_equipment_index"), {"kind": "space"})
            assert b"Loading Dock" in response.content
            assert b"Table Saw" not in response.content
            assert b'<span class="hub-badge">Space</span>' in response.content
            assert b"kind=space&q=" in response.content and b"pl-equip-chip--active" in response.content

        def it_searches_by_name(client: Client):
            _login(client, "eq_fq")
            EquipmentFactory(name="Table Saw")
            EquipmentFactory(name="Kiln")
            response = client.get(reverse("hub_equipment_index"), {"q": "saw"})
            assert b"Table Saw" in response.content
            assert b"Kiln" not in response.content

        def it_shows_the_filtered_empty_state_with_a_clear_link(client: Client):
            _login(client, "eq_fe")
            EquipmentFactory(name="Table Saw")
            response = client.get(reverse("hub_equipment_index"), {"q": "zzz"})
            assert b"Nothing matches those filters." in response.content
            assert b"Clear filters" in response.content

    def describe_add_button():
        def it_shows_for_an_admin(client: Client):
            _login(client, "eq_addbtn_admin", fog_role=Member.FogRole.ADMIN)
            response = client.get(reverse("hub_equipment_index"))
            assert reverse("hub_equipment_add").encode() in response.content
            assert b">+ Add</a>" in response.content

        def it_shows_for_an_equipment_capability_holder(client: Client):
            user = _login(client, "eq_addbtn_cap")
            user.member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
            response = client.get(reverse("hub_equipment_index"))
            assert reverse("hub_equipment_add").encode() in response.content

        def it_shows_for_a_space_manager(client: Client):
            # #502: a Space Manager creates rooms and spaces, so the action shows for them too.
            user = _login(client, "eq_addbtn_space_mgr")
            user.member.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_MANAGER)
            response = client.get(reverse("hub_equipment_index"))
            assert reverse("hub_equipment_add").encode() in response.content

        def it_hides_from_a_plain_member(client: Client):
            _login(client, "eq_addbtn_plain")
            response = client.get(reverse("hub_equipment_index"))
            assert reverse("hub_equipment_add").encode() not in response.content

    def it_treats_a_user_with_no_member_as_inactive(client: Client):
        user = _member_user("eq_no_member")
        user.member.delete()
        client.login(username="eq_no_member", password="pass")
        EquipmentFactory(name="Open Bench")
        response = client.get(reverse("hub_equipment_index"))
        assert response.status_code == 200
        assert b"Membership inactive" in response.content

    def it_puts_the_equipment_link_in_the_sidebar(client: Client):
        _login(client, "eq_nav")
        response = client.get(reverse("hub_equipment_index"))
        assert reverse("hub_equipment_index").encode() in response.content

    def it_is_the_reservations_page_with_four_kind_chips(client: Client):
        """#502: the Equipment page reads Reservations; URLs, names and the feature key stay."""
        import re

        _login(client, "eq_title")
        EquipmentFactory(name="Open Bench")
        content = client.get(reverse("hub_equipment_index")).content.decode()
        assert re.search(r"<title>[^<]*Reservations", content)
        assert ">Reservations</h1>" in content
        assert "Book a room, a space, or a tool you are trained on." in content
        assert 'aria-label="Search reservations"' in content
        assert 'placeholder="Search reservations"' in content
        for chip in ("Tools", "Rooms", "Spaces"):
            assert f">{chip}</a>" in content
        assert "kind=space&q=" in content

    def describe_locked_card():
        """#502: a tool the member is not trained on locks, with the pre portal bubble and a Book link."""

        def it_locks_an_untrained_members_card(client: Client):
            from django.utils.html import escape

            _login(client, "eq_lock")
            orientation_type = OrientationTypeFactory(name="Lathe")
            GuildOrientationSettingsFactory(guild=orientation_type.guild, is_enabled=True)
            EquipmentFactory(name="Gated Lathe", unlocking_orientations=[orientation_type])
            content = client.get(reverse("hub_equipment_index")).content.decode()
            assert 'class="hub-card pl-equip-card pl-equip-card--locked"' in content
            assert 'class="pl-equip-card__lock" aria-hidden="true"' in content
            assert "pl-equip-card__lock-glyph" in content
            card = content.split("pl-equip-card--locked", 1)[1].split("pl-equip-card__cta", 1)[0]
            assert 'class="pl-equip-card__state"' in card
            assert "Orientation needed" in card
            sentence = (
                "Trained on this before the Member Portal went live? Message us in #member-portal-general "
                "on Discord and say you need access to the Gated Lathe."
            )
            assert f'<span class="pl-help__bubble">{sentence}</span>' in card
            assert f'aria-label="Already trained: {sentence}"' in card
            assert "title=" not in card  # FRONTEND.md rule 19: the bubble, never a native tooltip
            expected = (
                f'href="{escape(orientation_type.orientations_page_path())}" '
                'class="pl-equip-card__cta">Book the orientation</a>'
            )
            assert expected in content

        def it_falls_back_to_the_owner_page_when_the_orientations_page_does_not_list_the_type(client: Client):
            from django.utils.html import escape

            _login(client, "eq_lock_unlisted")
            hidden = OrientationTypeFactory(name="Hidden Lathe Basics")  # its guild never enabled orientations
            retired = OrientationTypeFactory(name="Retired Lathe Basics", is_active=False)
            GuildOrientationSettingsFactory(guild=retired.guild, is_enabled=True)
            EquipmentFactory(name="Hidden Gate", unlocking_orientations=[hidden])
            EquipmentFactory(name="Retired Gate", unlocking_orientations=[retired])
            content = client.get(reverse("hub_equipment_index")).content.decode()
            for orientation_type in (hidden, retired):
                assert (
                    f'href="{escape(orientation_type.orientation_anchor_path())}" class="pl-equip-card__cta"' in content
                )
                assert orientation_type.orientations_page_path() not in content

        def it_decides_every_cards_link_in_the_grids_own_query(client: Client, django_assert_num_queries):
            from django.db import connection
            from django.test.utils import CaptureQueriesContext

            _login(client, "eq_lock_queries")
            first = OrientationTypeFactory(name="Query Gate One")
            EquipmentFactory(name="Gate One", unlocking_orientations=[first])
            client.get(reverse("hub_equipment_index"))
            with CaptureQueriesContext(connection) as one:
                client.get(reverse("hub_equipment_index"))
            for index in range(4):
                gate = OrientationTypeFactory(name=f"Query Gate {index + 2}")
                GuildOrientationSettingsFactory(guild=gate.guild, is_enabled=True)
                EquipmentFactory(name=f"Gate {index + 2}", unlocking_orientations=[gate])
            with django_assert_num_queries(len(one.captured_queries)):
                client.get(reverse("hub_equipment_index"))

        def it_leaves_a_trained_members_card_unlocked(client: Client):
            user = _login(client, "eq_trained")
            orientation_type = OrientationTypeFactory(name="Lathe")
            OrientationRecordFactory(member=user.member, orientation_type=orientation_type)
            EquipmentFactory(name="Gated Lathe", unlocking_orientations=[orientation_type])
            content = client.get(reverse("hub_equipment_index")).content.decode()
            assert "You're all set" in content
            assert "pl-equip-card--locked" not in content
            assert "pl-equip-card__lock" not in content
            assert "pl-equip-card__cta" not in content
            assert "say you need access to the Gated Lathe." not in content

        def it_leaves_the_other_states_as_they_were(client: Client):
            # The fee state wins over the orientation gap, so the card is not locked (#456 order).
            from tests.billing.factories import LateCancellationFeeFactory

            user = _login(client, "eq_lock_fee")
            EquipmentFactory(name="Gated Lathe", unlocking_orientations=[OrientationTypeFactory(name="Lathe")])
            LateCancellationFeeFactory(
                orientation_booking=OrientationBookingFactory(member=user.member, status="cancelled")
            )
            content = client.get(reverse("hub_equipment_index")).content.decode()
            assert "Pay your late cancellation fee to book again" in content
            assert "pl-equip-card--locked" not in content
            assert "pl-equip-card__cta" not in content

        def it_renders_the_grid_in_a_fixed_number_of_queries(client: Client):
            """The lock reads only prefetched rows: one gated card costs what six mixed cards cost."""
            from django.db import connection
            from django.test.utils import CaptureQueriesContext

            _login(client, "eq_lock_queries")
            url = reverse("hub_equipment_index")

            def count_queries() -> int:
                client.get(url)  # warm the session and per-request caches so both samples are steady state
                with CaptureQueriesContext(connection) as ctx:
                    assert client.get(url).status_code == 200
                return len(ctx.captured_queries)

            def gated(name: str, **kwargs) -> None:
                EquipmentFactory(
                    name=name, unlocking_orientations=[OrientationTypeFactory(name=f"{name} basics")], **kwargs
                )

            gated("Lathe")
            with_one = count_queries()
            gated("Mill", guild=GuildFactory(name="Metal"))
            gated("Dark Room", kind=Equipment.Kind.ROOM)
            gated("Loading Dock", kind=Equipment.Kind.SPACE)
            gated("Press")
            EquipmentFactory(name="Open Bench")
            assert Equipment.objects.count() == 6
            assert count_queries() == with_one

    def describe_shared_card_builder():
        """#502 part 4 moved the card building into ``reservation_cards`` so the guild page shares it."""

        def _grid(client: Client) -> None:
            from tests.membership.factories import EquipmentHoursFactory

            _login(client, "eq_shared_cards")
            woodshop = GuildFactory(name="Woodshop")
            lathe = EquipmentFactory(
                name="Lathe", guild=woodshop, unlocking_orientations=[OrientationTypeFactory(name="Lathe basics")]
            )
            EquipmentFactory(name="Woodshop Saw", guild=woodshop)
            EquipmentFactory(name="Dark Room", kind=Equipment.Kind.ROOM)
            EquipmentHoursFactory(equipment=lathe)

        def it_renders_the_same_cards(client: Client):
            _grid(client)
            cards = client.get(reverse("hub_equipment_index")).context["cards"]
            assert [(c["equipment"].name, c["access_state"], c["availability"]) for c in cards] == [
                ("Dark Room", Equipment.AccessState.OK, ("muted", "Not taking reservations yet")),
                ("Lathe", Equipment.AccessState.NEEDS_ORIENTATION, ("muted", "Not open right now")),
                ("Woodshop Saw", Equipment.AccessState.OK, ("muted", "Not taking reservations yet")),
            ]

        def it_keeps_the_query_count_it_had_before_the_extraction(client: Client, django_assert_num_queries):
            _grid(client)
            url = reverse("hub_equipment_index")
            client.get(url)  # warm the session and per-request caches
            # Measured on this grid before the extraction (36), plus the one staff prefetch (#615),
            # less the joined guild lookup the equipment guild gate needed, plus the two fixed
            # unlocking orientation prefetches (#656): the gate's list and the Book links' list.
            with django_assert_num_queries(38):
                assert client.get(url).status_code == 200

        def it_answers_an_empty_grid_without_the_member_lookups(django_assert_num_queries):
            from hub.equipment_views import _equipment_queryset, reservation_cards

            member = MemberFactory()
            # The listing rule's site configuration read and the item read; no access sets, no fee lookup.
            with django_assert_num_queries(2):
                assert reservation_cards(member, _equipment_queryset().active()) == []


def describe_equipment_add():
    def it_403s_a_plain_member_on_get_and_post(client: Client):
        _login(client, "eq_add_plain")
        assert client.get(reverse("hub_equipment_add")).status_code == 403
        assert client.post(reverse("hub_equipment_add"), {"name": "Sneaky Saw", "kind": "tool"}).status_code == 403
        assert not Equipment.objects.filter(name="Sneaky Saw").exists()

    def it_403s_a_guild_lead(client: Client):
        user = _login(client, "eq_add_lead")
        GuildFactory(guild_lead=user.member)
        assert client.get(reverse("hub_equipment_add")).status_code == 403

    def it_renders_for_an_admin(client: Client):
        _login(client, "eq_add_admin", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_equipment_add"))
        assert response.status_code == 200
        assert b"Add Equipment" in response.content

    def it_labels_the_standalone_choice_plainly(client: Client):
        _login(client, "eq_add_label", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_equipment_add"))
        assert b"Standalone (run by the makerspace)" in response.content
        assert b"Pick the guild that runs this equipment, or leave it Standalone." in response.content
        assert b"Blank means standalone" not in response.content

    def it_creates_and_redirects_to_the_new_detail_page(client: Client):
        _login(client, "eq_add_ok", fog_role=Member.FogRole.ADMIN)
        response = client.post(reverse("hub_equipment_add"), {"name": "Test Saw", "kind": "tool", "is_active": "on"})
        equipment = Equipment.objects.get(name="Test Saw")
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_equipment_detail", args=[equipment.slug])

    def it_lets_an_equipment_capability_holder_create(client: Client):
        user = _login(client, "eq_add_cap")
        user.member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        response = client.post(reverse("hub_equipment_add"), {"name": "Cap Saw", "kind": "tool", "is_active": "on"})
        assert response.status_code == 302
        assert Equipment.objects.filter(name="Cap Saw").exists()

    def it_offers_every_kind_to_an_admin(client: Client):
        _login(client, "eq_add_kinds_admin", fog_role=Member.FogRole.ADMIN)
        content = client.get(reverse("hub_equipment_add")).content.decode()
        for value in ("tool", "room", "space"):
            assert f'<option value="{value}"' in content

    def it_offers_every_guild_to_an_admin(client: Client):
        _login(client, "eq_add_admin_guilds", fog_role=Member.FogRole.ADMIN)
        other = GuildFactory(name="Ceramics")
        content = client.get(reverse("hub_equipment_add")).content.decode()
        assert f'<option value="{other.pk}"' in content

    def it_uses_the_plain_invalid_choice_for_an_admins_crafted_kind(client: Client):
        # The Space Manager sentence is for Space Managers; an admin's garbage kind gets Django's own refusal.
        _login(client, "eq_add_admin_badkind", fog_role=Member.FogRole.ADMIN)
        response = client.post(reverse("hub_equipment_add"), {"name": "Odd Saw", "kind": "banana", "is_active": "on"})
        assert response.status_code == 200
        assert b"Select a valid choice" in response.content
        assert EquipmentForm.KIND_NOT_ALLOWED.encode() not in response.content
        assert not Equipment.objects.filter(name="Odd Saw").exists()

    def it_gives_no_staff_row_to_a_creator_who_already_manages(client: Client):
        _login(client, "eq_add_admin_nostaff", fog_role=Member.FogRole.ADMIN)
        client.post(reverse("hub_equipment_add"), {"name": "Admin Saw", "kind": "tool", "is_active": "on"})
        assert Equipment.objects.filter(name="Admin Saw").exists()
        assert not EquipmentStaffMembership.objects.filter(equipment__name="Admin Saw").exists()

    def describe_space_manager():
        """#502: a Space Manager adds rooms and spaces, never tools, and manages what they add."""

        def _space_manager(client: Client, username: str) -> User:
            user = _login(client, username)
            user.member.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_MANAGER)
            return user

        def it_sees_room_and_space_only(client: Client):
            _space_manager(client, "eq_add_sm_get")
            response = client.get(reverse("hub_equipment_add"))
            assert response.status_code == 200
            content = response.content.decode()
            assert '<option value="room"' in content
            assert '<option value="space"' in content
            assert '<option value="tool"' not in content

        def it_creates_a_space_and_lands_on_its_page_as_its_manager(client: Client):
            user = _space_manager(client, "eq_add_sm_post")
            response = client.post(
                reverse("hub_equipment_add"), {"name": "Loading Dock", "kind": "space", "is_active": "on"}
            )
            equipment = Equipment.objects.get(name="Loading Dock")
            assert equipment.kind == Equipment.Kind.SPACE
            assert response.status_code == 302
            assert response["Location"] == reverse("hub_equipment_detail", args=[equipment.slug])
            staff_row = EquipmentStaffMembership.objects.get(equipment=equipment)
            assert staff_row.member == user.member
            assert staff_row.granted_by == user.member
            assert client.get(reverse("hub_equipment_manage", args=[equipment.slug])).status_code == 200

        def it_refuses_a_posted_tool_with_the_sentence(client: Client):
            _space_manager(client, "eq_add_sm_tool")
            response = client.post(
                reverse("hub_equipment_add"), {"name": "Sneaky Saw", "kind": "tool", "is_active": "on"}
            )
            assert response.status_code == 200
            assert EquipmentForm.KIND_NOT_ALLOWED.encode() in response.content
            assert not Equipment.objects.filter(name="Sneaky Saw").exists()

        def it_offers_only_the_guilds_they_lead_or_staff_plus_standalone(client: Client):
            user = _space_manager(client, "eq_add_sm_guilds")
            staffed = GuildFactory(name="Woodworking")
            GuildStaffMembershipFactory(guild=staffed, member=user.member)
            led = GuildFactory(name="Metal", guild_lead=user.member)
            other = GuildFactory(name="Ceramics")
            content = client.get(reverse("hub_equipment_add")).content.decode()
            assert f'<option value="{staffed.pk}"' in content
            assert f'<option value="{led.pk}"' in content
            assert f'<option value="{other.pk}"' not in content
            assert "Standalone (run by the makerspace)" in content

        def it_refuses_a_posted_guild_they_are_not_part_of(client: Client):
            _space_manager(client, "eq_add_sm_other_guild")
            other = GuildFactory(name="Ceramics")
            response = client.post(
                reverse("hub_equipment_add"),
                {"name": "Sneaky Dock", "kind": "space", "guild": other.pk, "is_active": "on"},
            )
            assert response.status_code == 200
            assert b"Select a valid choice" in response.content
            assert not Equipment.objects.filter(name="Sneaky Dock").exists()

        def it_files_a_space_under_a_guild_they_staff(client: Client):
            user = _space_manager(client, "eq_add_sm_own_guild")
            staffed = GuildFactory(name="Woodworking")
            GuildStaffMembershipFactory(guild=staffed, member=user.member)
            response = client.post(
                reverse("hub_equipment_add"),
                {"name": "Wood Dock", "kind": "space", "guild": staffed.pk, "is_active": "on"},
            )
            assert response.status_code == 302
            assert Equipment.objects.get(name="Wood Dock").guild == staffed

        def it_keeps_every_kind_on_the_form_when_none_are_given():
            # The manage panel's Details tab passes nothing and keeps today's rules.
            form = EquipmentForm()
            assert [value for value, _label in form.fields["kind"].choices] == ["tool", "room", "space"]

    def it_rejects_an_orientation_from_another_guild(client: Client):
        _login(client, "eq_add_mismatch", fog_role=Member.FogRole.ADMIN)
        woodshop = GuildFactory(name="Woodshop")
        foreign_type = OrientationTypeFactory(guild=GuildFactory(name="Ceramics"), name="Wheel")
        response = client.post(
            reverse("hub_equipment_add"),
            {
                "name": "Mismatch Saw",
                "kind": "tool",
                "guild": woodshop.pk,
                "unlocking_orientations": foreign_type.pk,
                "is_active": "on",
            },
        )
        assert response.status_code == 200
        assert b"Pick orientations offered by the chosen guild" in response.content
        assert not Equipment.objects.filter(name="Mismatch Saw").exists()

    def it_allows_any_guilds_orientation_on_standalone_equipment(client: Client):
        # The house Makerspace-guild convention: a standalone tool's gate lives on a guild's type.
        _login(client, "eq_add_standalone", fog_role=Member.FogRole.ADMIN)
        orientation_type = OrientationTypeFactory(name="Lathe")
        response = client.post(
            reverse("hub_equipment_add"),
            {"name": "House Lathe", "kind": "tool", "unlocking_orientations": orientation_type.pk, "is_active": "on"},
        )
        assert response.status_code == 302
        assert list(Equipment.objects.get(name="House Lathe").unlocking_orientations.all()) == [orientation_type]


def describe_equipment_detail():
    def it_shows_all_set_when_nothing_gates_it(client: Client):
        _login(client, "eq_det_ok")
        equipment = EquipmentFactory(name="Open Bench")
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert response.status_code == 200
        assert b"You're all set." in response.content
        assert b"needs to be active" not in response.content

    def it_shows_the_orientation_gap_with_a_deep_link(client: Client):
        from tests.membership.factories import GuildOrientationSettingsFactory

        _login(client, "eq_det_orient")
        orientation_type = OrientationTypeFactory(name="Lathe")
        # The guild must actually be taking bookings, or the honest paused variant renders.
        GuildOrientationSettingsFactory(guild=orientation_type.guild, is_enabled=True)
        equipment = EquipmentFactory(unlocking_orientations=[orientation_type])
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert b"You need the Lathe orientation before you can reserve this equipment." in response.content
        assert b"Book the Orientation" in response.content
        expected = (
            f"{reverse('hub_guild_detail', args=[orientation_type.guild.slug])}"
            f"?tab=orientations&amp;type={orientation_type.pk}#guild-orientation"
        )
        assert expected.encode() in response.content
        assert b"You're all set." not in response.content

    def it_shows_the_unpaid_late_fee_with_a_pay_button_instead_of_all_set(client: Client):
        """The block until paid (#456, part 2): the banner's fee state wins over every other state."""
        from tests.billing.factories import LateCancellationFeeFactory
        from tests.membership.factories import OrientationBookingFactory

        user = _login(client, "eq_det_late_fee")
        equipment = EquipmentFactory(name="Open Bench")
        fee = LateCancellationFeeFactory(
            orientation_booking=OrientationBookingFactory(member=user.member, status="cancelled"), amount_cents=3750
        )
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        content = response.content.decode()
        assert f'data-late-fee-notice="{fee.pk}"' in content
        assert "Pay your $37.50 late cancellation fee to book again." in content
        assert f'action="{reverse("hub_late_fee_pay", args=[fee.pk])}"' in content
        assert "You're all set." not in content

    def it_shows_all_set_again_once_the_fee_is_paid(client: Client):
        from billing.models import LateCancellationFee
        from tests.billing.factories import LateCancellationFeeFactory
        from tests.membership.factories import OrientationBookingFactory

        user = _login(client, "eq_det_fee_paid")
        equipment = EquipmentFactory(name="Open Bench")
        LateCancellationFeeFactory(
            orientation_booking=OrientationBookingFactory(member=user.member, status="cancelled"),
            status=LateCancellationFee.Status.PAID,
        )
        content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        assert "data-late-fee-notice" not in content
        assert "You're all set." in content

    def it_shows_the_booked_orientation_instead_of_the_button(client: Client):
        from tests.membership.factories import OrientationBookingFactory, OrientationSlotFactory

        user = _login(client, "eq_det_booked")
        orientation_type = OrientationTypeFactory(name="Lathe")
        from membership.models import OrientationBooking

        slot = OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type)
        OrientationBookingFactory(member=user.member, slot=slot, status=OrientationBooking.Status.CONFIRMED)
        equipment = EquipmentFactory(unlocking_orientations=[orientation_type])
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert b"Your orientation is booked for" in response.content
        assert b"Book the Orientation" not in response.content

    def it_shows_the_inactive_membership_state(client: Client):
        user = _login(client, "eq_det_former")
        user.member.status = Member.Status.INVITED
        user.member.save(update_fields=["status"])
        equipment = EquipmentFactory()
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert b"Your membership needs to be active to reserve equipment." in response.content
        assert b"You're all set." not in response.content

    def it_renders_the_about_section_with_the_readonly_space(client: Client):
        from tests.membership.factories import SpaceFactory

        _login(client, "eq_det_about")
        space = SpaceFactory(name="Media Room")
        equipment = EquipmentFactory(
            description="A big router.", location_note="Back corner of the wood shop.", space=space
        )
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert b"A big router." in response.content
        assert b"Back corner of the wood shop." in response.content
        assert b"See it on the space map" in response.content

    def it_404s_retired_equipment_for_a_plain_member(client: Client):
        _login(client, "eq_det_retired")
        equipment = EquipmentFactory(is_active=False)
        assert client.get(reverse("hub_equipment_detail", args=[equipment.slug])).status_code == 404

    def it_shows_retired_equipment_to_a_manager_with_a_notice(client: Client):
        user = _login(client, "eq_det_retired_mgr")
        equipment = EquipmentFactory(is_active=False)
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert response.status_code == 200
        assert b"This equipment is retired." in response.content

    def it_shows_the_manage_button_only_to_managers(client: Client):
        user = _login(client, "eq_det_manage_btn")
        equipment = EquipmentFactory()
        manage_url = reverse("hub_equipment_manage", args=[equipment.slug])
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert manage_url.encode() not in response.content
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert manage_url.encode() in response.content

    def describe_pre_portal_sentence():
        """#502: the needs orientation banner says how a member trained before the portal gets access."""

        sentence = (
            "Trained on this before the Member Portal went live? Message us in #member-portal-general on "
            "Discord and say you need access to the Gated Lathe."
        )

        def _gated(**kwargs) -> tuple[Equipment, OrientationType]:
            orientation_type = OrientationTypeFactory(name="Lathe")
            return EquipmentFactory(
                name="Gated Lathe", unlocking_orientations=[orientation_type], **kwargs
            ), orientation_type

        def it_follows_the_book_leaf(client: Client):
            _login(client, "eq_det_pre_book")
            equipment, orientation_type = _gated()
            GuildOrientationSettingsFactory(guild=orientation_type.guild, is_enabled=True)
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert f"You need the Lathe orientation before you can reserve this equipment. {sentence}</p>" in content
            assert "Book the Orientation" in content

        def it_follows_the_paused_leaf(client: Client):
            _login(client, "eq_det_pre_paused")
            equipment, _orientation_type = _gated()  # no settings row: the guild is not taking bookings
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert (
                f"You need the Lathe orientation before you can reserve this equipment. {sentence} "
                "Orientation bookings for this tool are paused. Check back soon.</p>"
            ) in content
            assert "Book the Orientation" not in content

        def it_names_a_rooms_kind_in_the_paused_leaf(client: Client):
            _login(client, "eq_det_pre_room")
            equipment, _orientation_type = _gated(kind=Equipment.Kind.ROOM)
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert "Orientation bookings for this room are paused." in content

        def it_stays_off_the_booked_leaf(client: Client):
            user = _login(client, "eq_det_pre_booked")
            equipment, orientation_type = _gated()
            OrientationBookingFactory(
                slot=OrientationSlotFactory(orientation_type=orientation_type),
                member=user.member,
                status=OrientationBooking.Status.CONFIRMED,
            )
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert "Your orientation is booked for" in content
            assert sentence not in content

        def it_stays_off_the_requested_leaf(client: Client):
            user = _login(client, "eq_det_pre_requested")
            equipment, orientation_type = _gated()
            OrientationBookingFactory(
                slot=OrientationSlotFactory(orientation_type=orientation_type), member=user.member
            )
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert "Your orientation request is in." in content
            assert sentence not in content

        def it_stays_off_for_a_trained_member(client: Client):
            user = _login(client, "eq_det_pre_trained")
            equipment, orientation_type = _gated()
            OrientationRecordFactory(member=user.member, orientation_type=orientation_type)
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert "You're all set." in content
            assert sentence not in content


def describe_orientation_deep_link_on_the_guild_page():
    def it_highlights_the_linked_type_group(client: Client):
        _login(client, "eq_deeplink")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        orientation_type = OrientationTypeFactory(guild=guild, name="Lathe")
        response = client.get(
            reverse("hub_guild_detail", args=[guild.slug]),
            {"tab": "orientations", "type": str(orientation_type.pk)},
        )
        assert response.status_code == 200
        assert f'id="orientation-type-{orientation_type.pk}"'.encode() in response.content
        assert b"pl-orient-type--highlight" in response.content

    def it_does_not_highlight_without_the_param(client: Client):
        _login(client, "eq_deeplink_off")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        orientation_type = OrientationTypeFactory(guild=guild, name="Lathe")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert f'id="orientation-type-{orientation_type.pk}"'.encode() in response.content
        assert b"pl-orient-type--highlight" not in response.content


def describe_equipment_manage():
    def it_403s_a_plain_member(client: Client):
        _login(client, "eq_mng_plain")
        equipment = EquipmentFactory()
        assert client.get(reverse("hub_equipment_manage", args=[equipment.slug])).status_code == 403

    def it_renders_for_every_manager_tier(client: Client):
        equipment_guild = GuildFactory()
        equipment = EquipmentFactory(guild=equipment_guild)
        url = reverse("hub_equipment_manage", args=[equipment.slug])

        lead = _member_user("eq_mng_lead")
        equipment_guild.guild_lead = lead.member
        equipment_guild.save(update_fields=["guild_lead"])
        client.login(username="eq_mng_lead", password="pass")
        assert client.get(url).status_code == 200

        manager = _member_user("eq_mng_row")
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager.member)
        client.login(username="eq_mng_row", password="pass")
        assert client.get(url).status_code == 200

        holder = _member_user("eq_mng_cap")
        holder.member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        client.login(username="eq_mng_cap", password="pass")
        assert client.get(url).status_code == 200

        _member_user("eq_mng_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="eq_mng_admin", password="pass")
        assert client.get(url).status_code == 200

    def it_shows_the_staff_empty_state(client: Client):
        _login(client, "eq_mng_empty", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "staff"})
        assert b"No per-equipment managers yet." in response.content
        assert b"+ Add Manager" in response.content
        # The rendered Alpine state comes from the sanitized server value and the
        # unbound add form stays collapsed on a plain GET.
        assert b"{ section: 'staff' }" in response.content
        assert b"{ showAdd: false }" in response.content

    def it_falls_back_to_the_details_tab_for_an_unknown_tab(client: Client):
        _login(client, "eq_mng_tab", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "bogus"})
        assert response.context["active_tab"] == "details"
        # The client-effective state: x-data renders the sanitized value, never the raw
        # ?tab= param — a garbage tab must not blank both panes.
        assert b"{ section: 'details' }" in response.content
        assert b"bogus" not in response.content

    def it_denies_a_manager_of_other_equipment(client: Client):
        # Cross-resource probe: an EquipmentStaffMembership row on A grants nothing on B.
        user = _login(client, "eq_mng_cross")
        mine = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=mine, member=user.member)
        other = EquipmentFactory(name="Other Saw")
        assert client.get(reverse("hub_equipment_manage", args=[other.slug])).status_code == 403
        response = client.post(
            reverse("hub_equipment_details_save", args=[other.slug]), {"name": "Hacked", "kind": "tool"}
        )
        assert response.status_code == 403
        other.refresh_from_db()
        assert other.name == "Other Saw"

    def it_narrows_the_orientation_choices_to_the_owning_guild_on_display(client: Client):
        _login(client, "eq_mng_narrow", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        own_type = OrientationTypeFactory(guild=guild, name="Lathe")
        OrientationTypeFactory(name="Wheel")  # another guild's type
        equipment = EquipmentFactory(guild=guild)
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]))
        choices = response.context["form"].fields["unlocking_orientations"].choices
        assert [value for value, _label in choices] == ["new", str(own_type.pk)]


def describe_equipment_details_save():
    def it_403s_a_crafted_post_from_a_plain_member(client: Client):
        _login(client, "eq_save_plain")
        equipment = EquipmentFactory(name="Locked Saw")
        response = client.post(
            reverse("hub_equipment_details_save", args=[equipment.slug]), {"name": "Hacked", "kind": "tool"}
        )
        assert response.status_code == 403
        equipment.refresh_from_db()
        assert equipment.name == "Locked Saw"

    def it_saves_and_redirects_back_to_the_details_tab(client: Client):
        user = _login(client, "eq_save_mgr")
        equipment = EquipmentFactory(name="Old Name")
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        response = client.post(
            reverse("hub_equipment_details_save", args=[equipment.slug]),
            {"name": "New Name", "kind": "tool", "is_active": "on"},
        )
        assert response.status_code == 302
        assert response["Location"].endswith("?tab=details")
        equipment.refresh_from_db()
        assert equipment.name == "New Name"
        assert equipment.slug == "old-name"  # slug is stable across renames

    def it_accepts_changing_guild_and_orientation_together(client: Client):
        # The bound form validates the type against the POSTED guild, not the stale
        # instance guild — re-homing equipment in one save must not dead-end.
        _login(client, "eq_save_rehome", fog_role=Member.FogRole.ADMIN)
        old_guild = GuildFactory(name="Woodshop")
        old_type = OrientationTypeFactory(guild=old_guild, name="Saw Basics")
        new_guild = GuildFactory(name="Ceramics")
        new_type = OrientationTypeFactory(guild=new_guild, name="Wheel")
        equipment = EquipmentFactory(guild=old_guild, unlocking_orientations=[old_type])
        response = client.post(
            reverse("hub_equipment_details_save", args=[equipment.slug]),
            {
                "name": equipment.name,
                "kind": "tool",
                "guild": new_guild.pk,
                "unlocking_orientations": new_type.pk,
                "is_active": "on",
            },
        )
        assert response.status_code == 302
        equipment.refresh_from_db()
        assert equipment.guild == new_guild
        assert list(equipment.unlocking_orientations.all()) == [new_type]

    def it_rejects_an_orientation_that_mismatches_the_posted_guild(client: Client):
        _login(client, "eq_save_mismatch", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Woodshop")
        foreign_type = OrientationTypeFactory(guild=GuildFactory(name="Ceramics"), name="Wheel")
        equipment = EquipmentFactory(guild=guild)
        response = client.post(
            reverse("hub_equipment_details_save", args=[equipment.slug]),
            {
                "name": equipment.name,
                "kind": "tool",
                "guild": guild.pk,
                "unlocking_orientations": foreign_type.pk,
                "is_active": "on",
            },
        )
        assert response.status_code == 200
        assert b"Pick orientations offered by the chosen guild" in response.content
        assert not equipment.unlocking_orientations.exists()

    def it_rerenders_with_errors_and_saves_nothing_on_invalid_input(client: Client):
        _login(client, "eq_save_bad", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory(name="Solid Saw")
        response = client.post(
            reverse("hub_equipment_details_save", args=[equipment.slug]),
            {"name": "", "kind": "tool"},
        )
        assert response.status_code == 200
        assert b"This field is required." in response.content
        equipment.refresh_from_db()
        assert equipment.name == "Solid Saw"

    def describe_space_manager_on_the_details_tab():
        """#502 fix round: the Details tab narrows like the add page, so a space cannot become a tool."""

        def _managed_space(client: Client, username: str, **kwargs) -> tuple[User, Equipment]:
            user = _login(client, username)
            user.member.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_MANAGER)
            equipment = EquipmentFactory(name="Loading Dock", kind=Equipment.Kind.SPACE, **kwargs)
            EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
            return user, equipment

        def it_refuses_turning_their_space_into_a_tool(client: Client):
            _user, equipment = _managed_space(client, "eq_save_sm_tool")
            response = client.post(
                reverse("hub_equipment_details_save", args=[equipment.slug]),
                {"name": "Loading Dock", "kind": "tool", "is_active": "on"},
            )
            assert response.status_code == 200
            assert EquipmentForm.KIND_NOT_ALLOWED.encode() in response.content
            equipment.refresh_from_db()
            assert equipment.kind == Equipment.Kind.SPACE

        def it_lets_them_make_it_a_room(client: Client):
            _user, equipment = _managed_space(client, "eq_save_sm_room")
            response = client.post(
                reverse("hub_equipment_details_save", args=[equipment.slug]),
                {"name": "Loading Dock", "kind": "room", "is_active": "on"},
            )
            assert response.status_code == 302
            equipment.refresh_from_db()
            assert equipment.kind == Equipment.Kind.ROOM

        def it_shows_room_and_space_only_on_the_details_tab(client: Client):
            _user, equipment = _managed_space(client, "eq_manage_sm_get")
            content = client.get(reverse("hub_equipment_manage", args=[equipment.slug])).content.decode()
            assert '<option value="room"' in content
            assert '<option value="space"' in content
            assert '<option value="tool"' not in content

        def it_keeps_a_tool_they_manage_through_a_guild_valid(client: Client):
            user = _login(client, "eq_save_sm_guild_tool")
            user.member.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_MANAGER)
            guild = GuildFactory(name="Woodworking", guild_lead=user.member)
            equipment = EquipmentFactory(name="Table Saw", kind=Equipment.Kind.TOOL, guild=guild)
            content = client.get(reverse("hub_equipment_manage", args=[equipment.slug])).content.decode()
            assert '<option value="tool"' in content
            response = client.post(
                reverse("hub_equipment_details_save", args=[equipment.slug]),
                {"name": "Table Saw", "kind": "tool", "guild": guild.pk, "is_active": "on"},
            )
            assert response.status_code == 302
            equipment.refresh_from_db()
            assert equipment.kind == Equipment.Kind.TOOL

        def it_narrows_the_guild_picker_to_their_guilds_and_the_current_one(client: Client):
            current = GuildFactory(name="Ceramics")
            user, equipment = _managed_space(client, "eq_save_sm_guilds", guild=current)
            staffed = GuildFactory(name="Woodworking")
            GuildStaffMembershipFactory(guild=staffed, member=user.member)
            other = GuildFactory(name="Metal")
            content = client.get(reverse("hub_equipment_manage", args=[equipment.slug])).content.decode()
            assert f'<option value="{current.pk}"' in content
            assert f'<option value="{staffed.pk}"' in content
            assert f'<option value="{other.pk}"' not in content
            response = client.post(
                reverse("hub_equipment_details_save", args=[equipment.slug]),
                {"name": "Loading Dock", "kind": "space", "guild": other.pk, "is_active": "on"},
            )
            assert response.status_code == 200
            assert b"Select a valid choice" in response.content
            equipment.refresh_from_db()
            assert equipment.guild == current

        def it_keeps_every_kind_and_guild_for_a_guild_lead(client: Client):
            user = _login(client, "eq_manage_lead_kinds")
            guild = GuildFactory(name="Woodworking", guild_lead=user.member)
            other = GuildFactory(name="Ceramics")
            equipment = EquipmentFactory(name="Table Saw", guild=guild)
            content = client.get(reverse("hub_equipment_manage", args=[equipment.slug])).content.decode()
            for value in ("tool", "room", "space"):
                assert f'<option value="{value}"' in content
            assert f'<option value="{other.pk}"' in content
            assert EquipmentForm.KIND_NOT_ALLOWED not in content


def describe_equipment_photo_delete():
    def it_403s_a_crafted_post_from_a_plain_member(client: Client):
        _login(client, "eq_photo_plain")
        equipment = EquipmentFactory()
        assert client.post(reverse("hub_equipment_photo_delete", args=[equipment.slug])).status_code == 403

    def it_clears_the_photo_for_a_manager(client: Client):
        user = _login(client, "eq_photo_mgr")
        equipment = EquipmentFactory(photo=SimpleUploadedFile("saw.png", tiny_png_bytes(), "image/png"))
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        response = client.post(reverse("hub_equipment_photo_delete", args=[equipment.slug]))
        assert response.status_code == 302
        equipment.refresh_from_db()
        assert not equipment.photo

    def it_redirects_quietly_when_there_is_no_photo(client: Client):
        _login(client, "eq_photo_none", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        response = client.post(reverse("hub_equipment_photo_delete", args=[equipment.slug]))
        assert response.status_code == 302


def describe_equipment_staff_add():
    def it_403s_a_crafted_post_from_a_plain_member(client: Client):
        _login(client, "eq_sa_plain")
        equipment = EquipmentFactory()
        target = MemberFactory()
        response = client.post(reverse("hub_equipment_staff_add", args=[equipment.slug]), {"member": target.pk})
        assert response.status_code == 403
        assert not equipment.staff_memberships.exists()

    def it_adds_a_manager_and_records_the_granter(client: Client):
        user = _login(client, "eq_sa_admin", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        target = MemberFactory()
        response = client.post(reverse("hub_equipment_staff_add", args=[equipment.slug]), {"member": target.pk})
        assert response.status_code == 302
        assert response["Location"].endswith("?tab=staff")
        staff = equipment.staff_memberships.get()
        assert staff.member == target
        assert staff.granted_by == user.member

    def it_rejects_a_duplicate_grant_with_a_form_error(client: Client):
        _login(client, "eq_sa_dup", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        target = MemberFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=target)
        response = client.post(reverse("hub_equipment_staff_add", args=[equipment.slug]), {"member": target.pk})
        assert response.status_code == 200
        assert b"They already manage this equipment." in response.content
        assert equipment.staff_memberships.count() == 1
        # The bound (error-bearing) form re-renders revealed on the staff pane, so the
        # error is actually visible — not hidden inside the collapsed + Add Manager reveal.
        assert b"{ section: 'staff' }" in response.content
        assert b"{ showAdd: true }" in response.content


def describe_equipment_staff_remove():
    def it_403s_a_crafted_post_from_a_plain_member(client: Client):
        _login(client, "eq_sr_plain")
        staff = EquipmentStaffMembershipFactory()
        response = client.post(reverse("hub_equipment_staff_remove", args=[staff.equipment.slug, staff.pk]))
        assert response.status_code == 403
        assert EquipmentStaffMembership.objects.filter(pk=staff.pk).exists()

    def it_removes_the_manager(client: Client):
        _login(client, "eq_sr_admin", fog_role=Member.FogRole.ADMIN)
        staff = EquipmentStaffMembershipFactory()
        response = client.post(reverse("hub_equipment_staff_remove", args=[staff.equipment.slug, staff.pk]))
        assert response.status_code == 302
        assert not EquipmentStaffMembership.objects.filter(pk=staff.pk).exists()

    def it_404s_a_staff_row_from_another_equipment(client: Client):
        _login(client, "eq_sr_cross", fog_role=Member.FogRole.ADMIN)
        staff = EquipmentStaffMembershipFactory()
        other = EquipmentFactory()
        response = client.post(reverse("hub_equipment_staff_remove", args=[other.slug, staff.pk]))
        assert response.status_code == 404
        assert EquipmentStaffMembership.objects.filter(pk=staff.pk).exists()


def describe_equipment_orientation_surface():
    """The on-page Orientation section, the owner-aware banner, and the book redirect."""

    def _owned_type(equipment, **kwargs):
        from tests.membership.factories import OrientationTypeFactory

        return OrientationTypeFactory(
            equipment_owned=True, equipment=equipment, name=kwargs.pop("name", "Operator Basics"), **kwargs
        )

    def _slot(orientation_type):
        from tests.membership.factories import OrientationSlotFactory

        return OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)

    def it_renders_the_slot_list_with_request_and_price_chip(client: Client):
        _login(client, "eqo_slots")
        equipment = EquipmentFactory(name="CNC Router")
        orientation_type = _owned_type(equipment, price_cents=1500)
        _slot(orientation_type)
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        content = response.content.decode()
        assert "Get Oriented for the CNC Router" in content
        assert 'id="equipment-orientation"' in content
        assert "Request" in content
        assert "$15" in content
        assert "now through our secure checkout" in content  # paid confirm copy

    def it_shows_oriented_pending_and_hold_states(client: Client):
        from membership.models import OrientationBooking
        from tests.membership.factories import OrientationBookingFactory

        user = _login(client, "eqo_states")
        equipment = EquipmentFactory()
        done_type = _owned_type(equipment, name="Done Type")
        OrientationBookingFactory(
            slot=_slot(done_type), member=user.member, is_completed=True, status=OrientationBooking.Status.CONFIRMED
        )
        pending_type = _owned_type(equipment, name="Pending Type")
        OrientationBookingFactory(slot=_slot(pending_type), member=user.member)
        hold_type = _owned_type(equipment, name="Hold Type", price_cents=1500)
        OrientationBookingFactory(
            slot=_slot(hold_type),
            member=user.member,
            status=OrientationBooking.Status.PENDING_PAYMENT,
            amount_paid_cents=1500,
        )
        content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        assert "You've completed this orientation." in content
        assert "Waiting for a manager to confirm." in content
        assert "Resume payment" in content
        assert '<span class="pl-equip-badge pl-equip-badge--warn">Payment pending</span>' in content

    def it_shows_the_empty_state_with_a_manager_link_for_managers(client: Client):
        user = _login(client, "eqo_empty_mgr")
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        _owned_type(equipment)
        content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        assert "No orientation times are posted yet. Check back soon." in content
        assert "Add times from the manage panel." in content

    def it_hides_the_manager_link_from_members(client: Client):
        _login(client, "eqo_empty_plain")
        equipment = EquipmentFactory()
        _owned_type(equipment)
        content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        assert "No orientation times are posted yet. Check back soon." in content
        assert "Add times from the manage panel." not in content

    def it_omits_the_section_with_no_renderable_types(client: Client):
        _login(client, "eqo_none")
        equipment = EquipmentFactory()
        _owned_type(equipment, is_active=False)
        content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        assert 'id="equipment-orientation"' not in content

    def it_highlights_the_type_from_the_query_param(client: Client):
        _login(client, "eqo_highlight")
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        _slot(orientation_type)
        response = client.get(
            reverse("hub_equipment_detail", args=[equipment.slug]), {"type": str(orientation_type.pk)}
        )
        assert b"pl-orient-type--highlight" in response.content

    def describe_inactive_type_pinning():
        def it_keeps_a_confirmed_bookings_cancel_controls(client: Client):
            from membership.models import OrientationBooking
            from tests.membership.factories import OrientationBookingFactory

            user = _login(client, "eqo_pin_conf")
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment)
            OrientationBookingFactory(
                slot=_slot(orientation_type), member=user.member, status=OrientationBooking.Status.CONFIRMED
            )
            orientation_type.is_active = False
            orientation_type.save(update_fields=["is_active"])
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert "Cancel my orientation" in content
            assert "Request</button>" not in content  # no slot list on an inactive type

        def it_keeps_a_holds_resume_controls(client: Client):
            from membership.models import OrientationBooking
            from tests.membership.factories import OrientationBookingFactory

            user = _login(client, "eqo_pin_hold")
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment, price_cents=1500)
            OrientationBookingFactory(
                slot=_slot(orientation_type),
                member=user.member,
                status=OrientationBooking.Status.PENDING_PAYMENT,
                amount_paid_cents=1500,
            )
            orientation_type.is_active = False
            orientation_type.save(update_fields=["is_active"])
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert "Resume payment" in content

        def it_hides_the_inactive_type_from_uninvolved_members(client: Client):
            _login(client, "eqo_pin_none")
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment)
            _slot(orientation_type)
            orientation_type.is_active = False
            orientation_type.save(update_fields=["is_active"])
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert 'id="equipment-orientation"' not in content

    def describe_owner_aware_banner():
        def it_anchors_an_equipment_owned_required_type_down_the_page(client: Client):
            _login(client, "eqo_banner_own")
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment)
            _slot(orientation_type)
            equipment.unlocking_orientations.set([orientation_type])
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert f"?type={orientation_type.pk}#equipment-orientation" in content
            assert "Book the Orientation" in content

        def it_shows_the_paused_copy_with_no_dead_link(client: Client):
            _login(client, "eqo_banner_paused")
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment, is_active=False)
            equipment.unlocking_orientations.set([orientation_type])
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert "Orientation bookings for this tool are paused. Check back soon." in content
            assert "Book the Orientation" not in content

        def it_shows_the_booked_sub_state_even_on_a_paused_type(client: Client):
            from membership.models import OrientationBooking
            from tests.membership.factories import OrientationBookingFactory

            user = _login(client, "eqo_banner_booked")
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment)
            OrientationBookingFactory(
                slot=_slot(orientation_type), member=user.member, status=OrientationBooking.Status.CONFIRMED
            )
            orientation_type.is_active = False
            orientation_type.save(update_fields=["is_active"])
            equipment.unlocking_orientations.set([orientation_type])
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert "Your orientation is booked for" in content
            assert "paused. Check back soon." not in content

        def it_uses_manager_copy_for_an_equipment_owned_pending_request(client: Client):
            from tests.membership.factories import OrientationBookingFactory

            user = _login(client, "eqo_banner_pending")
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment)
            OrientationBookingFactory(slot=_slot(orientation_type), member=user.member)  # REQUESTED
            equipment.unlocking_orientations.set([orientation_type])
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert "A manager will confirm a time." in content
            assert "The guild will confirm a time." not in content

    def describe_book_redirect():
        def it_lands_back_on_the_equipment_anchor(client: Client):
            _login(client, "eqo_book")
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment)
            slot = _slot(orientation_type)
            response = client.post(reverse("hub_orientation_book", args=[slot.pk]))
            assert response.status_code == 302
            assert (
                response["Location"] == f"/equipment/{equipment.slug}/?type={orientation_type.pk}#equipment-orientation"
            )

        def it_keeps_the_guild_redirect_for_guild_slots(client: Client):
            from tests.membership.factories import OrientationSlotFactory

            _login(client, "eqo_book_guild")
            slot = OrientationSlotFactory()
            response = client.post(reverse("hub_orientation_book", args=[slot.pk]))
            assert response.status_code == 302
            assert response["Location"].startswith(f"/guilds/{slot.guild.slug}/?tab=orientations")

    def describe_equipment_form_unlocking_orientations():
        def it_offers_and_saves_the_equipments_own_type(client: Client):
            _login(client, "eqo_form_own", fog_role=Member.FogRole.ADMIN)
            equipment = EquipmentFactory(name="Own Saw")
            orientation_type = _owned_type(equipment)
            response = client.post(
                reverse("hub_equipment_details_save", args=[equipment.slug]),
                {
                    "name": equipment.name,
                    "kind": "tool",
                    "unlocking_orientations": orientation_type.pk,
                    "is_active": "on",
                },
            )
            assert response.status_code == 302
            assert list(equipment.unlocking_orientations.all()) == [orientation_type]

        def it_round_trips_an_inactive_selected_required_type(client: Client):
            _login(client, "eqo_form_inactive", fog_role=Member.FogRole.ADMIN)
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment, is_active=False)
            equipment.unlocking_orientations.set([orientation_type])
            response = client.post(
                reverse("hub_equipment_details_save", args=[equipment.slug]),
                {
                    "name": equipment.name,
                    "kind": "tool",
                    "unlocking_orientations": orientation_type.pk,
                    "is_active": "on",
                },
            )
            assert response.status_code == 302  # no invalid-choice error
            assert list(equipment.unlocking_orientations.all()) == [orientation_type]

        def it_hides_an_inactive_type_from_other_equipment(client: Client):
            _login(client, "eqo_form_hidden", fog_role=Member.FogRole.ADMIN)
            other = EquipmentFactory()
            inactive = _owned_type(other, is_active=False)
            fresh = EquipmentFactory()
            response = client.get(reverse("hub_equipment_manage", args=[fresh.slug]))
            choices = response.context["form"].fields["unlocking_orientations"].choices
            assert str(inactive.pk) not in [value for value, _label in choices]


def describe_equipment_orientation_list():
    """The equipment page lists orientation times the way a guild page does (equipment-orientations-guild-pattern)."""

    def _day(offset: int) -> date:
        return timezone.localdate() + timedelta(days=offset)

    def _at(day: date, hour: int) -> datetime:
        return timezone.make_aware(datetime.combine(day, time(hour, 0)))

    def _owned_type(equipment: Equipment, **kwargs):
        return OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics", **kwargs)

    def _slot(orientation_type, hour: int, *, orienter=None, seats: int = 1, offset: int = 2):
        return OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=orientation_type,
            orienter=orienter,
            starts_at=_at(_day(offset), hour),
            ends_at=_at(_day(offset), hour + 1),
            seats=seats,
        )

    def _dana(equipment: Equipment):
        dana = MemberFactory(full_legal_name="Dana Reyes")
        EquipmentStaffMembershipFactory(equipment=equipment, member=dana)
        return dana

    def it_renders_the_plain_list_with_the_with_cell_and_both_confirm_copies(client: Client):
        _login(client, "ol_list")
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        dana = _dana(equipment)
        personal = _slot(orientation_type, 10, orienter=dana)
        shared = _slot(orientation_type, 12)
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        content = response.content.decode()
        section = response.context["orientation_sections"][0]
        assert [slot.pk for slot in section["slots"]] == [personal.pk, shared.pk]
        assert section["slots"][0].with_display == "with Dana"
        assert section["slots"][1].with_display == ""
        assert "pl-orient-slots__head" in content
        assert "10:00 AM to 11:00 AM" in content
        assert "with Dana" in content
        assert 'pl-orient-avatar pl-orient-avatar--initials">D<' in content
        assert content.count("Request</button>") == 2
        assert "send your request to Dana to confirm" in content
        # Escaped like the orienter prompt above: the message is composed with |add (#456).
        assert "We&#x27;ll send your request to the equipment managers to confirm." in content
        assert "{ page: 0, size: 5, total: 2 }" in content
        assert "pl-orient-days" not in content
        assert "aria-pressed" not in content

    def it_renders_a_dozen_slots_in_as_many_queries_as_two(client: Client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        _login(client, "ol_queries")
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        dana = _dana(equipment)
        url = reverse("hub_equipment_detail", args=[equipment.slug])

        def count_queries() -> int:
            client.get(url)  # warm the session and per-request caches so both samples are steady state
            with CaptureQueriesContext(connection) as ctx:
                assert client.get(url).status_code == 200
            return len(ctx.captured_queries)

        for hour in (10, 11):
            OrientationBookingFactory(slot=_slot(orientation_type, hour, orienter=dana, seats=2))
        with_two = count_queries()
        for hour in range(12, 22):
            _slot(orientation_type, hour, orienter=dana, seats=2)
        assert count_queries() == with_two  # is_full reads the one aggregate, never a COUNT per row

    def it_pages_five_at_a_time_with_no_cap(client: Client):
        _login(client, "ol_pager")
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        for offset in range(2, 9):
            _slot(orientation_type, 10, offset=offset)
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert len(response.context["orientation_sections"][0]["slots"]) == 7
        content = response.content.decode()
        assert "{ page: 0, size: 5, total: 7 }" in content
        assert "pl-orient-slots__pager" in content

    def it_shows_full_and_who_the_member_is_waiting_on(client: Client):
        user = _login(client, "ol_states")
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        dana = _dana(equipment)
        full = _slot(orientation_type, 10)
        OrientationBookingFactory(slot=full)
        content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        assert ">Full<" in content
        assert "Request</button>" not in content
        booked = _slot(orientation_type, 12, orienter=dana)
        OrientationBookingFactory(slot=booked, member=user.member)
        content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        assert "· with Dana" in content
        assert "Waiting for Dana to confirm." in content

    def it_renders_for_a_user_with_no_member(client: Client):
        user = _member_user("ol_nomember")
        user.member.delete()
        client.login(username="ol_nomember", password="pass")
        equipment = EquipmentFactory()
        _slot(_owned_type(equipment), 10)
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert response.status_code == 200
        assert len(response.context["orientation_sections"][0]["slots"]) == 1

    def it_hides_a_slot_under_a_confirmed_reservation_until_it_is_cancelled(client: Client):
        _login(client, "ol_reserved")
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        covered = _slot(orientation_type, 10)
        _slot(orientation_type, 11)
        reservation = EquipmentReservationFactory(
            equipment=equipment, starts_at=_at(_day(2), 10), ends_at=_at(_day(2), 11)
        )

        def listed() -> list[int]:
            response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
            return [slot.pk for slot in response.context["orientation_sections"][0]["slots"]]

        assert covered.pk not in listed()
        reservation.status = "cancelled"
        reservation.save(update_fields=["status"])
        assert covered.pk in listed()

    def it_closes_a_deleted_windows_slot_when_the_member_cancels(client: Client):
        from membership import orientations
        from membership.models import OrientationBooking, OrientationSlot
        from tests.membership.factories import OrientationAvailabilityFactory

        user = _login(client, "ol_selfcancel")
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        rule = OrientationAvailabilityFactory(equipment_owned=True, orientation_type=orientation_type)
        slot = OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=orientation_type,
            availability=rule,
            source=OrientationSlot.Source.GENERATED,
            starts_at=_at(_day(2), 10),
            ends_at=_at(_day(2), 11),
            seats=4,
        )
        booking = OrientationBookingFactory(slot=slot, member=user.member, status=OrientationBooking.Status.CONFIRMED)
        orientations.retire_rule(rule)  # the window is gone; the booked slot survives capped to 1
        response = client.post(reverse("hub_orientation_cancel_mine", args=[booking.pk]))
        assert response.status_code == 302
        slot.refresh_from_db()
        assert slot.is_cancelled is True
        content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        assert "No orientation times are posted yet. Check back soon." in content


def describe_equipment_own_orientation():
    """Issue #466: "New orientation for this equipment" on the add page and the Details tab.

    One Save creates the equipment, a type it owns (guild empty, active) and the
    requirement, in one transaction; a bad type name saves nothing and lands beside the
    field; the other two choices behave as before and ignore the nested inputs.
    """

    def _post(
        equipment_name: str = "CNC Router", type_name: str = "Operator Basics", **overrides: str | list[str]
    ) -> dict[str, str | list[str]]:
        data: dict[str, str | list[str]] = {
            "name": equipment_name,
            "kind": "tool",
            "is_active": "on",
            "unlocking_orientations": [EquipmentForm.NEW_TYPE_CHOICE],
            "new_type-name": type_name,
            "new_type-duration_minutes": "45",
            "new_type-default_seats": "2",
            "new_type-price": "15",
            "new_type-default_location": "Wood shop",
        }
        data.update(overrides)
        return data

    def _error_sits_beside_the_name_field(content: str, message: str) -> bool:
        """The error list renders between the nested name input and the next nested input."""
        start = content.index('name="new_type-name"')
        end = content.index('name="new_type-duration_minutes"')
        return message in content[start:end]

    def describe_the_picker():
        def it_offers_new_then_the_types_in_the_old_order_as_a_multi_select(client: Client):
            _login(client, "eqn_choices", fog_role=Member.FogRole.ADMIN)
            wheel = OrientationTypeFactory(guild=GuildFactory(name="Ceramics"), name="Wheel")
            saw = OrientationTypeFactory(guild=GuildFactory(name="Woodshop"), name="Saw Basics")
            OrientationTypeFactory(guild=GuildFactory(name="Metals"), name="Retired", is_active=False)
            response = client.get(reverse("hub_equipment_add"))
            field = response.context["form"].fields["unlocking_orientations"]
            assert list(field.choices) == [
                ("new", "New orientation for this equipment"),
                (str(wheel.pk), str(wheel)),
                (str(saw.pk), str(saw)),
            ]
            assert field.required is False
            assert field.label == "Orientations that unlock it"
            content = response.content.decode()
            select = content[content.index('<select name="unlocking_orientations"') :].split(">", 1)[0]
            assert " multiple" in select
            assert 'size="3"' in select

        def it_renders_the_nested_fields_closed_with_the_model_defaults(client: Client):
            _login(client, "eqn_closed", fog_role=Member.FogRole.ADMIN)
            response = client.get(reverse("hub_equipment_add"))
            content = response.content.decode()
            form = response.context["form"]
            assert form.creates_orientation_type is False
            assert form.new_type_form.is_bound is False
            assert form.new_type_form.prefix == "new_type"
            assert form.new_type_form["duration_minutes"].value() == 60
            assert form.new_type_form["default_seats"].value() == 4
            assert "newOrientation: false" in content
            assert '<div x-show="newOrientation" x-cloak class="pl-equip-new-type">' in content
            for rendered in EquipmentForm.NEW_TYPE_FIELDS:
                assert f'name="new_type-{rendered}"' in content
            for unrendered in ("description", "sort_order", "is_active"):
                assert f'name="new_type-{unrendered}"' not in content
            # No browser-side required attribute: the inputs sit hidden until the choice is made.
            name_input = content[content.index('name="new_type-name"') :].split(">", 1)[0]
            assert "required" not in name_input

    def describe_add_page():
        def it_creates_the_equipment_its_own_type_and_the_gate_in_one_save(client: Client):
            _login(client, "eqn_add_ok", fog_role=Member.FogRole.ADMIN)
            response = client.post(reverse("hub_equipment_add"), _post())
            equipment = Equipment.objects.get(name="CNC Router")
            assert response.status_code == 302
            assert response["Location"] == reverse("hub_equipment_detail", args=[equipment.slug])
            assert Equipment.objects.count() == 1
            assert OrientationType.objects.count() == 1
            new_type = OrientationType.objects.get()
            assert new_type.equipment == equipment
            assert new_type.guild is None
            assert new_type.is_active is True
            assert new_type.name == "Operator Basics"
            assert new_type.duration_minutes == 45
            assert new_type.default_seats == 2
            assert new_type.price_cents == 1500
            assert new_type.default_location == "Wood shop"
            assert (new_type.description, new_type.sort_order) == ("", 0)
            assert list(equipment.unlocking_orientations.all()) == [new_type]
            assert list(equipment.owned_orientation_types.all()) == [new_type]

        def it_closes_the_gate_on_the_first_detail_page_load(client: Client):
            _login(client, "eqn_add_gate_admin", fog_role=Member.FogRole.ADMIN)
            response = client.post(reverse("hub_equipment_add"), _post(), follow=True)
            equipment = Equipment.objects.get(name="CNC Router")
            assert response.redirect_chain == [(reverse("hub_equipment_detail", args=[equipment.slug]), 302)]
            assert response.context["access_state"] == Equipment.AccessState.NEEDS_ORIENTATION
            client.logout()
            member = _login(client, "eqn_add_gate_member").member
            response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
            assert response.context["access_state"] == Equipment.AccessState.NEEDS_ORIENTATION
            assert "pl-equip-banner--warn" in response.content.decode()
            with pytest.raises(
                EquipmentError, match="Operator Basics orientation before you can reserve this equipment"
            ):
                reserve(equipment, member, timezone.now() + timedelta(days=1), 60)
            assert not equipment.reservations.exists()

        def it_lists_the_new_type_on_manage_orientation(client: Client):
            _login(client, "eqn_add_listed", fog_role=Member.FogRole.ADMIN)
            client.post(reverse("hub_equipment_add"), _post())
            equipment = Equipment.objects.get(name="CNC Router")
            response = client.get(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation")
            formset = response.context["orientation_types_formset"]
            assert [row.instance for row in formset.forms] == list(equipment.unlocking_orientations.all())
            assert formset.forms[0]["is_active"].value() is True
            assert formset.forms[0].instance.equipment == equipment
            assert 'name="otypes-0-name" value="Operator Basics"' in response.content.decode()

        def it_rerenders_with_the_error_beside_the_field_and_saves_nothing_on_a_blank_name(client: Client):
            _login(client, "eqn_add_blank", fog_role=Member.FogRole.ADMIN)
            response = client.post(reverse("hub_equipment_add"), _post(type_name="   "))
            assert response.status_code == 200
            form = response.context["form"]
            assert form.creates_orientation_type is True
            assert form.new_type_form.errors == {"name": ["This field is required."]}
            assert form.errors == {}
            content = response.content.decode()
            assert _error_sits_beside_the_name_field(content, "This field is required.")
            # The panel renders open (no x-cloak) so the error is in view.
            assert "newOrientation: true" in content
            assert '<div x-show="newOrientation" class="pl-equip-new-type">' in content
            assert not Equipment.objects.exists()
            assert not OrientationType.objects.exists()

        def it_ignores_the_nested_fields_when_no_orientation_is_needed(client: Client):
            _login(client, "eqn_add_none", fog_role=Member.FogRole.ADMIN)
            response = client.post(reverse("hub_equipment_add"), _post(unlocking_orientations=[]))
            assert response.status_code == 302
            assert not Equipment.objects.get(name="CNC Router").unlocking_orientations.exists()
            assert not OrientationType.objects.exists()

        def it_ignores_the_nested_fields_when_an_existing_type_is_picked(client: Client):
            _login(client, "eqn_add_existing", fog_role=Member.FogRole.ADMIN)
            existing = OrientationTypeFactory(name="Lathe")
            response = client.post(reverse("hub_equipment_add"), _post(unlocking_orientations=[str(existing.pk)]))
            assert response.status_code == 302
            assert list(Equipment.objects.get(name="CNC Router").unlocking_orientations.all()) == [existing]
            assert OrientationType.objects.count() == 1

        def it_still_holds_an_existing_type_to_the_chosen_guild(client: Client):
            _login(client, "eqn_add_mismatch", fog_role=Member.FogRole.ADMIN)
            woodshop = GuildFactory(name="Woodshop")
            foreign_type = OrientationTypeFactory(guild=GuildFactory(name="Ceramics"), name="Wheel")
            response = client.post(
                reverse("hub_equipment_add"),
                _post(guild=str(woodshop.pk), unlocking_orientations=[str(foreign_type.pk)]),
            )
            assert response.status_code == 200
            assert response.context["form"].errors == {
                "unlocking_orientations": [
                    "Pick orientations offered by the chosen guild, or this equipment's own orientations."
                ]
            }
            assert not Equipment.objects.exists()

        def it_lets_guild_run_equipment_take_a_new_type_of_its_own(client: Client):
            # The new type is the equipment's own, so the guild match rule does not apply to it.
            _login(client, "eqn_add_guild", fog_role=Member.FogRole.ADMIN)
            woodshop = GuildFactory(name="Woodshop")
            response = client.post(reverse("hub_equipment_add"), _post(guild=str(woodshop.pk)))
            assert response.status_code == 302
            equipment = Equipment.objects.get(name="CNC Router")
            assert equipment.guild == woodshop
            new_type = equipment.unlocking_orientations.get()
            assert new_type.guild is None
            assert new_type.equipment == equipment

        def it_refuses_a_crafted_choice_and_saves_nothing(client: Client):
            _login(client, "eqn_add_crafted", fog_role=Member.FogRole.ADMIN)
            response = client.post(reverse("hub_equipment_add"), _post(unlocking_orientations=["424242"]))
            assert response.status_code == 200
            assert list(response.context["form"].errors) == ["unlocking_orientations"]
            assert response.context["form"].creates_orientation_type is False
            assert not Equipment.objects.exists()
            assert not OrientationType.objects.exists()

    def describe_details_tab():
        def _save_url(equipment: Equipment) -> str:
            return reverse("hub_equipment_details_save", args=[equipment.slug])

        def it_creates_the_type_and_sets_the_requirement_on_existing_equipment(client: Client):
            # A per-equipment manager, the Details tab's existing audience.
            user = _login(client, "eqn_det_mgr")
            equipment = EquipmentFactory(name="Old Lathe")
            EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
            response = client.post(_save_url(equipment), _post(equipment_name="Old Lathe", type_name="Lathe Basics"))
            assert response.status_code == 302
            assert response["Location"].endswith("?tab=details")
            equipment.refresh_from_db()
            new_type = equipment.owned_orientation_types.get()
            assert list(equipment.unlocking_orientations.all()) == [new_type]
            assert (new_type.name, new_type.guild, new_type.is_active) == ("Lathe Basics", None, True)
            assert Equipment.objects.count() == 1
            assert equipment.access_state(MemberFactory()) == Equipment.AccessState.NEEDS_ORIENTATION

        def it_rerenders_with_the_error_beside_the_field_and_saves_nothing_on_a_blank_name(client: Client):
            _login(client, "eqn_det_blank", fog_role=Member.FogRole.ADMIN)
            equipment = EquipmentFactory(name="Solid Saw")
            response = client.post(_save_url(equipment), _post(equipment_name="Renamed Saw", type_name=""))
            assert response.status_code == 200
            assert response.context["form"].new_type_form.errors == {"name": ["This field is required."]}
            assert _error_sits_beside_the_name_field(response.content.decode(), "This field is required.")
            equipment.refresh_from_db()
            assert equipment.name == "Solid Saw"
            assert not equipment.unlocking_orientations.exists()
            assert not OrientationType.objects.exists()

        def it_refuses_a_name_this_equipment_already_uses_whatever_the_case(client: Client):
            _login(client, "eqn_det_dup", fog_role=Member.FogRole.ADMIN)
            equipment = EquipmentFactory(name="Solid Saw")
            OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
            response = client.post(
                _save_url(equipment), _post(equipment_name="Renamed Saw", type_name="operator basics")
            )
            assert response.status_code == 200
            message = (
                'This equipment already has an orientation named "operator basics". Give the new one its own name.'
            )
            assert response.context["form"].new_type_form.errors == {"name": [message]}
            assert _error_sits_beside_the_name_field(response.content.decode(), "already has an orientation named")
            equipment.refresh_from_db()
            assert equipment.name == "Solid Saw"
            assert not equipment.unlocking_orientations.exists()
            assert OrientationType.objects.count() == 1

        def it_only_checks_the_name_against_this_equipments_own_types(client: Client):
            _login(client, "eqn_det_scoped", fog_role=Member.FogRole.ADMIN)
            OrientationTypeFactory(name="Operator Basics")  # a guild's type
            OrientationTypeFactory(equipment_owned=True, name="Operator Basics")  # another equipment's type
            equipment = EquipmentFactory(name="Solid Saw")
            response = client.post(_save_url(equipment), _post(equipment_name="Solid Saw"))
            assert response.status_code == 302
            equipment.refresh_from_db()
            assert equipment.unlocking_orientations.get().equipment == equipment
            assert OrientationType.objects.count() == 3

        def it_keeps_a_guild_type_and_no_orientation_working_as_before(client: Client):
            _login(client, "eqn_det_unchanged", fog_role=Member.FogRole.ADMIN)
            guild = GuildFactory(name="Woodshop")
            guild_type = OrientationTypeFactory(guild=guild, name="Saw Basics")
            equipment = EquipmentFactory(name="Solid Saw", guild=guild)
            response = client.post(
                _save_url(equipment),
                _post(equipment_name="Solid Saw", guild=str(guild.pk), unlocking_orientations=[str(guild_type.pk)]),
            )
            assert response.status_code == 302
            assert list(equipment.unlocking_orientations.all()) == [guild_type]
            response = client.post(
                _save_url(equipment), _post(equipment_name="Solid Saw", guild=str(guild.pk), unlocking_orientations=[])
            )
            assert response.status_code == 302
            assert not equipment.unlocking_orientations.exists()
            assert OrientationType.objects.count() == 1

        def it_still_allows_the_equipments_own_type_on_guild_run_equipment(client: Client):
            _login(client, "eqn_det_own", fog_role=Member.FogRole.ADMIN)
            guild = GuildFactory(name="Woodshop")
            equipment = EquipmentFactory(name="Solid Saw", guild=guild)
            own_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
            response = client.post(
                _save_url(equipment),
                _post(equipment_name="Solid Saw", guild=str(guild.pk), unlocking_orientations=[str(own_type.pk)]),
            )
            assert response.status_code == 302
            assert list(equipment.unlocking_orientations.all()) == [own_type]
            assert OrientationType.objects.count() == 1

    def describe_the_form_save():
        def it_refuses_commit_false_when_a_type_is_being_made():
            # The type needs a saved equipment to belong to, so a deferred save has no
            # honest answer; the views always call save().
            form = EquipmentForm(_post())
            assert form.is_valid() is True
            with pytest.raises(ValueError, match="commit=False"):
                form.save(commit=False)
            assert not Equipment.objects.exists()
            assert not OrientationType.objects.exists()

        def it_saves_plainly_when_no_type_is_being_made():
            form = EquipmentForm(_post(unlocking_orientations=[]))
            assert form.is_valid() is True
            equipment = form.save()
            assert equipment.pk is not None
            assert not equipment.unlocking_orientations.exists()
            assert not OrientationType.objects.exists()


def describe_late_cancel_fee_on_the_orientation_prompt():
    """The equipment page's Request prompt carries the policy sentence only with a fee (#456, part 1)."""

    def _late_fees(enabled: bool) -> None:
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.late_cancel_fees_enabled = enabled
        config.save()

    def _page(client: Client, equipment: Equipment) -> str:
        from tests.membership.factories import OrientationSlotFactory, OrientationTypeFactory

        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
        OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Request</button>" in content
        return content

    def it_appends_the_sentence_when_the_equipment_charges_a_fee(client: Client):
        _login(client, "lcf_prompt_on")
        _late_fees(True)
        assert "$37.50" in _page(client, EquipmentFactory(late_cancel_fee_cents=3750))

    def it_leaves_the_prompt_clean_with_no_fee(client: Client):
        _login(client, "lcf_prompt_free")
        _late_fees(True)
        assert "$37.50" not in _page(client, EquipmentFactory())

    def it_leaves_the_prompt_clean_while_the_site_switch_is_off(client: Client):
        _login(client, "lcf_prompt_off")
        _late_fees(False)
        assert "$37.50" not in _page(client, EquipmentFactory(late_cancel_fee_cents=3750))


def describe_any_one_of_several_orientations():
    """#656: one page for the press, unlocked by either of its two orientations."""

    def _press() -> tuple[Equipment, OrientationType, OrientationType]:
        guild = GuildFactory(name="Anyone Printmaking")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        beginner = OrientationTypeFactory(guild=guild, name="Anyone Press Beginner")
        experienced = OrientationTypeFactory(guild=guild, name="Anyone Press Experienced")
        press = EquipmentFactory(name="Anyone Etching Press", unlocking_orientations=[beginner, experienced])
        return press, beginner, experienced

    def _complete(member: Member, orientation_type: OrientationType) -> None:
        slot = OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type)
        OrientationBookingFactory(member=member, slot=slot, is_completed=True)

    def describe_the_edit_form():
        def it_saves_several_orientations_and_then_none(client: Client):
            _login(client, "any_form_several", fog_role=Member.FogRole.ADMIN)
            press, beginner, experienced = _press()
            url = reverse("hub_equipment_details_save", args=[press.slug])
            payload = {"name": press.name, "kind": "tool", "is_active": "on"}
            response = client.post(url, {**payload, "unlocking_orientations": [beginner.pk, experienced.pk]})
            assert response.status_code == 302
            assert list(press.unlocking_orientations.all()) == [beginner, experienced]
            assert client.post(url, payload).status_code == 302
            assert not press.unlocking_orientations.exists()

        def it_shows_every_saved_orientation_selected(client: Client):
            _login(client, "any_form_selected", fog_role=Member.FogRole.ADMIN)
            press, beginner, experienced = _press()
            form = client.get(reverse("hub_equipment_manage", args=[press.slug])).context["form"]
            assert form["unlocking_orientations"].value() == [str(beginner.pk), str(experienced.pk)]
            content = str(form["unlocking_orientations"])
            assert f'value="{beginner.pk}" selected' in content
            assert f'value="{experienced.pk}" selected' in content

        def it_adds_a_new_own_orientation_beside_a_picked_one(client: Client):
            _login(client, "any_form_new", fog_role=Member.FogRole.ADMIN)
            press, beginner, _experienced = _press()
            response = client.post(
                reverse("hub_equipment_details_save", args=[press.slug]),
                {
                    "name": press.name,
                    "kind": "tool",
                    "is_active": "on",
                    "unlocking_orientations": [EquipmentForm.NEW_TYPE_CHOICE, beginner.pk],
                    "new_type-name": "Anyone Own Basics",
                    "new_type-duration_minutes": "60",
                    "new_type-default_seats": "2",
                    "new_type-price": "",
                    "new_type-default_location": "",
                },
            )
            assert response.status_code == 302
            own = press.owned_orientation_types.get()
            assert set(press.unlocking_orientations.all()) == {beginner, own}

    def describe_the_rollback_mirror():
        """#656, one release: every form write keeps the retired column equal to the list's first type."""

        def it_mirrors_an_item_gated_after_the_migration_from_the_multi_select(client: Client):
            _login(client, "any_mirror_gate", fog_role=Member.FogRole.ADMIN)
            _press_unused, beginner, experienced = _press()
            bench = EquipmentFactory(name="Any Mirror Bench")
            response = client.post(
                reverse("hub_equipment_details_save", args=[bench.slug]),
                {
                    "name": bench.name,
                    "kind": "tool",
                    "is_active": "on",
                    "unlocking_orientations": [experienced.pk, beginner.pk],
                },
            )
            assert response.status_code == 302
            bench.refresh_from_db()
            assert bench.required_orientation == beginner

        def it_mirrors_an_item_gated_by_a_new_orientation_of_its_own(client: Client):
            _login(client, "any_mirror_new", fog_role=Member.FogRole.ADMIN)
            bench = EquipmentFactory(name="Any Mirror New Bench")
            response = client.post(
                reverse("hub_equipment_details_save", args=[bench.slug]),
                {
                    "name": bench.name,
                    "kind": "tool",
                    "is_active": "on",
                    "unlocking_orientations": [EquipmentForm.NEW_TYPE_CHOICE],
                    "new_type-name": "Any Mirror Basics",
                    "new_type-duration_minutes": "60",
                    "new_type-default_seats": "2",
                    "new_type-price": "",
                    "new_type-default_location": "",
                },
            )
            assert response.status_code == 302
            bench.refresh_from_db()
            assert bench.required_orientation == bench.owned_orientation_types.get()

        def it_nulls_the_column_when_the_item_is_cleared_to_none(client: Client):
            _login(client, "any_mirror_clear", fog_role=Member.FogRole.ADMIN)
            press, beginner, _experienced = _press()
            press.required_orientation = beginner  # the state the forward migration leaves
            press.save(update_fields=["required_orientation"])
            response = client.post(
                reverse("hub_equipment_details_save", args=[press.slug]),
                {"name": press.name, "kind": "tool", "is_active": "on"},
            )
            assert response.status_code == 302
            press.refresh_from_db()
            assert not press.unlocking_orientations.exists()
            assert press.required_orientation is None

    def describe_a_member_who_completed_one():
        def it_reads_all_set_on_the_page_and_the_card(client: Client):
            user = _login(client, "any_done_one")
            press, _beginner, experienced = _press()
            _complete(user.member, experienced)
            detail = client.get(reverse("hub_equipment_detail", args=[press.slug])).content.decode()
            assert "pl-equip-banner--ok" in detail
            assert "You're all set." in detail
            index = client.get(reverse("hub_equipment_index"))
            assert [card["access_state"] for card in index.context["cards"]] == [Equipment.AccessState.OK]
            assert "pl-equip-card--locked" not in index.content.decode()

        def it_can_reserve(client: Client):
            user = _login(client, "any_done_reserve")
            press, beginner, _experienced = _press()
            _complete(user.member, beginner)
            assert press.booking_blockers(user.member) == []

    def describe_a_member_with_none():
        def it_lists_every_orientation_with_its_own_book_link(client: Client):
            _login(client, "any_none_detail")
            press, beginner, experienced = _press()
            content = client.get(reverse("hub_equipment_detail", args=[press.slug])).content.decode()
            assert "Any one of these orientations unlocks this." in content
            assert "data-equip-unlocks" in content
            for orientation_type in (beginner, experienced):
                row = content[content.index(f'data-unlock-type="{orientation_type.pk}"') :].split("</p>", 1)[0]
                assert orientation_type.name in row
                assert f'href="{orientation_type.orientation_anchor_path().replace("&", "&amp;")}"' in row
                assert ">Book</a>" in row
            assert "You're all set." not in content

        def it_shows_a_paused_orientation_without_a_book_link(client: Client):
            _login(client, "any_none_paused")
            press, _beginner, experienced = _press()
            experienced.is_active = False
            experienced.save()
            content = client.get(reverse("hub_equipment_detail", args=[press.slug])).content.decode()
            row = content[content.index(f'data-unlock-type="{experienced.pk}"') :].split("</p>", 1)[0]
            assert "Bookings are paused. Check back soon." in row
            assert ">Book</a>" not in row

        def it_shows_a_requested_orientation_in_place_of_its_book_link(client: Client):
            user = _login(client, "any_none_requested")
            press, beginner, _experienced = _press()
            slot = OrientationSlotFactory(guild=beginner.guild, orientation_type=beginner)
            OrientationBookingFactory(member=user.member, slot=slot, status=OrientationBooking.Status.REQUESTED)
            content = client.get(reverse("hub_equipment_detail", args=[press.slug])).content.decode()
            row = content[content.index(f'data-unlock-type="{beginner.pk}"') :].split("</p>", 1)[0]
            assert "Your request is in. The guild will confirm a time." in row
            assert ">Book</a>" not in row

        def it_shows_a_confirmed_orientation_with_its_time(client: Client):
            user = _login(client, "any_none_confirmed")
            press, beginner, _experienced = _press()
            slot = OrientationSlotFactory(guild=beginner.guild, orientation_type=beginner)
            OrientationBookingFactory(member=user.member, slot=slot, status=OrientationBooking.Status.CONFIRMED)
            content = client.get(reverse("hub_equipment_detail", args=[press.slug])).content.decode()
            row = content[content.index(f'data-unlock-type="{beginner.pk}"') :].split("</p>", 1)[0]
            assert "Booked for" in row

        def it_gives_the_locked_card_a_book_link_per_orientation(client: Client):
            _login(client, "any_none_card")
            press, beginner, experienced = _press()
            content = client.get(reverse("hub_equipment_index")).content.decode()
            assert "pl-equip-card--locked" in content
            for orientation_type in (beginner, experienced):
                link = orientation_type.orientations_page_path()
                assert f'href="{link}" class="pl-equip-card__cta" data-unlock-type="{orientation_type.pk}"' in content
                assert f"Book {orientation_type.name}</a>" in content
            assert "Book the orientation</a>" not in content

        def it_keeps_the_grid_query_count_however_many_orientations_unlock_a_card(client: Client):
            from django.db import connection
            from django.test.utils import CaptureQueriesContext

            _login(client, "any_none_queries")
            url = reverse("hub_equipment_index")

            def count_queries() -> int:
                client.get(url)
                with CaptureQueriesContext(connection) as ctx:
                    assert client.get(url).status_code == 200
                return len(ctx.captured_queries)

            EquipmentFactory(name="Any Lathe", unlocking_orientations=[OrientationTypeFactory(name="Any Lathe 1")])
            with_one = count_queries()
            EquipmentFactory(
                name="Any Mill",
                unlocking_orientations=[OrientationTypeFactory(name=f"Any Mill {n}") for n in range(3)],
            )
            _press()
            assert count_queries() == with_one
