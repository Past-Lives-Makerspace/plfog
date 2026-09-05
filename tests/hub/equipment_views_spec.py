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

from membership.models import AdminCapability, Equipment, EquipmentStaffMembership, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
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
        assert b"No equipment is bookable yet. Check back soon." in response.content

    def it_hides_inactive_equipment(client: Client):
        _login(client, "eq_retired")
        EquipmentFactory(name="Old Bandsaw", is_active=False)
        response = client.get(reverse("hub_equipment_index"))
        assert b"Old Bandsaw" not in response.content
        assert b"No equipment is bookable yet. Check back soon." in response.content

    def it_renders_cards_with_access_badges(client: Client):
        user = _login(client, "eq_badges")
        EquipmentFactory(name="Open Bench")
        EquipmentFactory(name="Gated Lathe", required_orientation=OrientationTypeFactory(name="Lathe"))
        woodshop = GuildFactory(name="Woodshop")
        EquipmentFactory(name="Members Saw", guild=woodshop, requires_guild_membership=True)
        response = client.get(reverse("hub_equipment_index"))
        assert b"You're all set" in response.content
        assert b"Orientation needed" in response.content
        assert b"Woodshop members only" in response.content
        assert user is not None  # the badge set proves the bulk access sets flowed through

    def it_shows_membership_inactive_badges_to_a_former_member(client: Client):
        user = _login(client, "eq_former")
        user.member.status = Member.Status.FORMER
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
            assert b"+ Add Equipment" in response.content

        def it_shows_for_an_equipment_capability_holder(client: Client):
            user = _login(client, "eq_addbtn_cap")
            user.member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
            response = client.get(reverse("hub_equipment_index"))
            assert b"+ Add Equipment" in response.content

        def it_hides_from_a_plain_member(client: Client):
            _login(client, "eq_addbtn_plain")
            response = client.get(reverse("hub_equipment_index"))
            assert b"+ Add Equipment" not in response.content

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

    def it_rejects_requires_guild_membership_without_a_guild(client: Client):
        _login(client, "eq_add_bad", fog_role=Member.FogRole.ADMIN)
        response = client.post(
            reverse("hub_equipment_add"),
            {"name": "Bad Saw", "kind": "tool", "requires_guild_membership": "on", "is_active": "on"},
        )
        assert response.status_code == 200
        assert b"Pick a guild first, or turn this off." in response.content
        assert not Equipment.objects.filter(name="Bad Saw").exists()

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
                "required_orientation": foreign_type.pk,
                "is_active": "on",
            },
        )
        assert response.status_code == 200
        assert b"Pick an orientation offered by the chosen guild" in response.content
        assert not Equipment.objects.filter(name="Mismatch Saw").exists()

    def it_allows_any_guilds_orientation_on_standalone_equipment(client: Client):
        # The house Makerspace-guild convention: a standalone tool's gate lives on a guild's type.
        _login(client, "eq_add_standalone", fog_role=Member.FogRole.ADMIN)
        orientation_type = OrientationTypeFactory(name="Lathe")
        response = client.post(
            reverse("hub_equipment_add"),
            {"name": "House Lathe", "kind": "tool", "required_orientation": orientation_type.pk, "is_active": "on"},
        )
        assert response.status_code == 302
        assert Equipment.objects.get(name="House Lathe").required_orientation == orientation_type


def describe_equipment_detail():
    def it_shows_all_set_when_nothing_gates_it(client: Client):
        _login(client, "eq_det_ok")
        equipment = EquipmentFactory(name="Open Bench")
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert response.status_code == 200
        assert b"You're all set." in response.content
        assert b"members can book this" not in response.content
        assert b"needs to be active" not in response.content

    def it_shows_the_orientation_gap_with_a_deep_link(client: Client):
        from tests.membership.factories import GuildOrientationSettingsFactory

        _login(client, "eq_det_orient")
        orientation_type = OrientationTypeFactory(name="Lathe")
        # The guild must actually be taking bookings, or the honest paused variant renders.
        GuildOrientationSettingsFactory(guild=orientation_type.guild, is_enabled=True)
        equipment = EquipmentFactory(required_orientation=orientation_type)
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert b"You need the Lathe orientation before you can book time here." in response.content
        assert b"Book the Orientation" in response.content
        expected = (
            f"{reverse('hub_guild_detail', args=[orientation_type.guild.slug])}"
            f"?tab=orientations&amp;type={orientation_type.pk}#guild-orientation"
        )
        assert expected.encode() in response.content
        assert b"You're all set." not in response.content

    def it_shows_the_booked_orientation_instead_of_the_button(client: Client):
        from tests.membership.factories import OrientationBookingFactory, OrientationSlotFactory

        user = _login(client, "eq_det_booked")
        orientation_type = OrientationTypeFactory(name="Lathe")
        from membership.models import OrientationBooking

        slot = OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type)
        OrientationBookingFactory(member=user.member, slot=slot, status=OrientationBooking.Status.CONFIRMED)
        equipment = EquipmentFactory(required_orientation=orientation_type)
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert b"Your orientation is booked for" in response.content
        assert b"Book the Orientation" not in response.content

    def it_shows_the_guild_gap_with_a_join_button(client: Client):
        _login(client, "eq_det_guild")
        woodshop = GuildFactory(name="Woodshop")
        equipment = EquipmentFactory(guild=woodshop, requires_guild_membership=True)
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert b"Only Woodshop members can book this." in response.content
        assert b"Join Woodshop" in response.content
        assert b"You're all set." not in response.content

    def it_shows_the_inactive_membership_state(client: Client):
        user = _login(client, "eq_det_former")
        user.member.status = Member.Status.FORMER
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
        queryset = response.context["form"].fields["required_orientation"].queryset
        assert list(queryset) == [own_type]


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
        equipment = EquipmentFactory(guild=old_guild, required_orientation=old_type)
        response = client.post(
            reverse("hub_equipment_details_save", args=[equipment.slug]),
            {
                "name": equipment.name,
                "kind": "tool",
                "guild": new_guild.pk,
                "required_orientation": new_type.pk,
                "is_active": "on",
            },
        )
        assert response.status_code == 302
        equipment.refresh_from_db()
        assert equipment.guild == new_guild
        assert equipment.required_orientation == new_type

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
                "required_orientation": foreign_type.pk,
                "is_active": "on",
            },
        )
        assert response.status_code == 200
        assert b"Pick an orientation offered by the chosen guild" in response.content
        equipment.refresh_from_db()
        assert equipment.required_orientation is None

    def it_rerenders_with_errors_and_saves_nothing_on_invalid_input(client: Client):
        _login(client, "eq_save_bad", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory(name="Solid Saw")
        response = client.post(
            reverse("hub_equipment_details_save", args=[equipment.slug]),
            {"name": "Broken Saw", "kind": "tool", "requires_guild_membership": "on"},
        )
        assert response.status_code == 200
        assert b"Pick a guild first, or turn this off." in response.content
        equipment.refresh_from_db()
        assert equipment.name == "Solid Saw"


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
        assert "Finishing Your Booking" in content

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
            equipment.required_orientation = orientation_type
            equipment.save(update_fields=["required_orientation"])
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert f"?type={orientation_type.pk}#equipment-orientation" in content
            assert "Book the Orientation" in content

        def it_shows_the_paused_copy_with_no_dead_link(client: Client):
            _login(client, "eqo_banner_paused")
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment, is_active=False)
            equipment.required_orientation = orientation_type
            equipment.save(update_fields=["required_orientation"])
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
            equipment.required_orientation = orientation_type
            equipment.save(update_fields=["required_orientation"])
            content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
            assert "Your orientation is booked for" in content
            assert "paused. Check back soon." not in content

        def it_uses_manager_copy_for_an_equipment_owned_pending_request(client: Client):
            from tests.membership.factories import OrientationBookingFactory

            user = _login(client, "eqo_banner_pending")
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment)
            OrientationBookingFactory(slot=_slot(orientation_type), member=user.member)  # REQUESTED
            equipment.required_orientation = orientation_type
            equipment.save(update_fields=["required_orientation"])
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

    def describe_equipment_form_required_orientation():
        def it_offers_and_saves_the_equipments_own_type(client: Client):
            _login(client, "eqo_form_own", fog_role=Member.FogRole.ADMIN)
            equipment = EquipmentFactory(name="Own Saw")
            orientation_type = _owned_type(equipment)
            response = client.post(
                reverse("hub_equipment_details_save", args=[equipment.slug]),
                {
                    "name": equipment.name,
                    "kind": "tool",
                    "required_orientation": orientation_type.pk,
                    "is_active": "on",
                },
            )
            assert response.status_code == 302
            equipment.refresh_from_db()
            assert equipment.required_orientation == orientation_type

        def it_round_trips_an_inactive_selected_required_type(client: Client):
            _login(client, "eqo_form_inactive", fog_role=Member.FogRole.ADMIN)
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment, is_active=False)
            equipment.required_orientation = orientation_type
            equipment.save(update_fields=["required_orientation"])
            response = client.post(
                reverse("hub_equipment_details_save", args=[equipment.slug]),
                {
                    "name": equipment.name,
                    "kind": "tool",
                    "required_orientation": orientation_type.pk,
                    "is_active": "on",
                },
            )
            assert response.status_code == 302  # no invalid-choice error
            equipment.refresh_from_db()
            assert equipment.required_orientation == orientation_type

        def it_hides_an_inactive_type_from_other_equipment(client: Client):
            _login(client, "eqo_form_hidden", fog_role=Member.FogRole.ADMIN)
            other = EquipmentFactory()
            inactive = _owned_type(other, is_active=False)
            fresh = EquipmentFactory()
            response = client.get(reverse("hub_equipment_manage", args=[fresh.slug]))
            queryset = response.context["form"].fields["required_orientation"].queryset
            assert inactive not in queryset


def describe_equipment_day_picker():
    """The equipment page's day chips + time rows picker (equipment-orientation-hours spec §6.4)."""

    def _day(offset: int) -> date:
        return timezone.localdate() + timedelta(days=offset)

    def _at(day: date, hour: int) -> datetime:
        return timezone.make_aware(datetime.combine(day, time(hour, 0)))

    def _owned_type(equipment: Equipment, **kwargs):
        return OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics", **kwargs)

    def _slot(orientation_type, day: date, hour: int, *, seats: int = 1):
        return OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=orientation_type,
            starts_at=_at(day, hour),
            ends_at=_at(day, hour + 1),
            seats=seats,
        )

    def it_renders_a_chip_per_day_and_selects_the_first_open_day(client: Client):
        _login(client, "dp_chips")
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        full_day, open_day, later_day = _day(2), _day(3), _day(5)
        OrientationBookingFactory(slot=_slot(orientation_type, full_day, 10))
        _slot(orientation_type, open_day, 10)
        _slot(orientation_type, open_day, 11)
        _slot(orientation_type, later_day, 9)
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        section = response.context["orientation_sections"][0]
        assert [(d["iso"], d["open_count"], len(d["slots"])) for d in section["days"]] == [
            (full_day.isoformat(), 0, 1),
            (open_day.isoformat(), 2, 2),
            (later_day.isoformat(), 1, 1),
        ]
        assert section["default_day"] == open_day.isoformat()
        assert section["all_full"] is False
        content = response.content.decode()
        # One static state per chip; the leading space keeps Alpine's :aria-pressed binding out of the count.
        assert content.count(' aria-pressed="') == 3
        assert content.count("pl-orient-days__chip--full") == 1
        assert content.count(' aria-pressed="true"') == 1
        assert f"{{ day: '{open_day.isoformat()}' }}" in content
        # Object syntax: Alpine removes the class again when another day is picked.
        assert ":class=\"{ 'pl-orient-days__chip--active': day === '" + open_day.isoformat() + "' }\"" in content
        assert 'pl-orient-days__chip-count">Full<' in content
        assert 'pl-orient-days__chip-count">2 open<' in content
        assert f"x-show=\"day === '{open_day.isoformat()}'\"" in content
        assert "10:00 AM to 11:00 AM" in content
        assert "1 seat left" in content
        assert "Request</button>" in content
        # A string literal passed through {% include with %} is safe text, so the apostrophe stays literal.
        assert "We'll send your request to the equipment managers to confirm." in content
        assert 'class="pl-orient-days"' in content
        assert "pl-orient-slots__pager" not in content
        assert "Every time is full right now." not in content

    def it_shows_the_all_full_state_and_selects_the_first_day(client: Client):
        _login(client, "dp_full")
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        OrientationBookingFactory(slot=_slot(orientation_type, _day(2), 10))
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        section = response.context["orientation_sections"][0]
        assert section["all_full"] is True
        assert section["default_day"] == _day(2).isoformat()
        content = response.content.decode()
        assert "Every time is full right now. Check back soon." in content
        assert "Request</button>" not in content
        assert "0 of 1 open" in content

    def it_shows_open_of_total_for_multi_seat_slots(client: Client):
        _login(client, "dp_seats")
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        OrientationBookingFactory(slot=_slot(orientation_type, _day(2), 10, seats=4))
        content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        assert "3 of 4 open" in content

    def it_renders_for_a_user_with_no_member(client: Client):
        user = _member_user("dp_nomember")
        user.member.delete()
        client.login(username="dp_nomember", password="pass")
        equipment = EquipmentFactory()
        _slot(_owned_type(equipment), _day(2), 10)
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert response.status_code == 200
        assert len(response.context["orientation_sections"][0]["days"]) == 1

    def it_does_not_pin_a_declined_booking(client: Client):
        from membership.models import OrientationBooking

        user = _login(client, "dp_declined")
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        slot = _slot(orientation_type, _day(2), 10)
        OrientationBookingFactory(slot=slot, member=user.member, status=OrientationBooking.Status.DECLINED)
        content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        assert "Request</button>" in content

    def it_closes_a_deleted_windows_slot_when_the_member_cancels(client: Client):
        from membership import orientations
        from membership.models import OrientationBooking, OrientationSlot
        from tests.membership.factories import OrientationAvailabilityFactory

        user = _login(client, "dp_selfcancel")
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

    def it_hides_a_slot_under_a_confirmed_reservation_until_it_is_cancelled(client: Client):
        _login(client, "dp_reserved")
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        _slot(orientation_type, _day(2), 10)
        _slot(orientation_type, _day(2), 11)
        reservation = EquipmentReservationFactory(
            equipment=equipment, starts_at=_at(_day(2), 10), ends_at=_at(_day(2), 11)
        )

        def picker_starts() -> list:
            response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
            days = response.context["orientation_sections"][0]["days"]
            return [slot.starts_at for day in days for slot in day["slots"]]

        # The reservation itself still lists under Upcoming Reservations; only the picker hides the slot.
        assert picker_starts() == [_at(_day(2), 11)]
        reservation.status = "cancelled"
        reservation.save(update_fields=["status"])
        assert picker_starts() == [_at(_day(2), 10), _at(_day(2), 11)]

    def it_keeps_the_empty_state_with_no_slots(client: Client):
        _login(client, "dp_empty")
        equipment = EquipmentFactory()
        _owned_type(equipment)
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        section = response.context["orientation_sections"][0]
        assert section["days"] == []
        assert section["default_day"] == ""
        assert section["all_full"] is False
        assert "No orientation times are posted yet. Check back soon." in response.content.decode()
