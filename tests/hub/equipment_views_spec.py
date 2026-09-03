"""BDD specs for the Equipment directory views (equipment-reservations spec PR 1).

Index (filters, badges, empty states), detail (one-state requirements banner, the
orientation deep link), the admin-gated add form, and the manage panel (Details +
Staff) — including crafted-POST permission probes for every gated endpoint.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from membership.models import AdminCapability, Equipment, EquipmentStaffMembership, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
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
        _login(client, "eq_det_orient")
        orientation_type = OrientationTypeFactory(name="Lathe")
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
        slot = OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type)
        OrientationBookingFactory(member=user.member, slot=slot)
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

    def it_falls_back_to_the_details_tab_for_an_unknown_tab(client: Client):
        _login(client, "eq_mng_tab", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "bogus"})
        assert response.context["active_tab"] == "details"


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
