"""BDD specs for the orientations dashboard: access, filters, export, add-member, completion toggle."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Member, OrientationBooking
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _past_booking(guild: object, name: str) -> object:
    """A booking on a past slot — shows in the filterable table but not the (future-only) Upcoming list."""
    slot = OrientationSlotFactory(
        guild=guild,
        starts_at=timezone.now() - timedelta(days=2),
        ends_at=timezone.now() - timedelta(days=2) + timedelta(hours=1),
    )
    return OrientationBookingFactory(slot=slot, member=MemberFactory(full_legal_name=name))


def describe_orientations_dashboard():
    def it_renders_for_an_admin(client: Client):
        _user_with_role("d_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="d_admin", password="pass")
        response = client.get(reverse("hub_orientations_dashboard"))
        assert response.status_code == 200

    def it_renders_for_a_guild_lead(client: Client):
        user = _user_with_role("d_lead", fog_role=Member.FogRole.MEMBER)
        GuildFactory(guild_lead=user.member)
        client.login(username="d_lead", password="pass")
        assert client.get(reverse("hub_orientations_dashboard")).status_code == 200

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("d_reg", fog_role=Member.FogRole.MEMBER)
        client.login(username="d_reg", password="pass")
        assert client.get(reverse("hub_orientations_dashboard")).status_code == 403

    def it_requires_login(client: Client):
        response = client.get(reverse("hub_orientations_dashboard"))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_lists_bookings_across_guilds(client: Client):
        _user_with_role("d_list", fog_role=Member.FogRole.ADMIN)
        OrientationBookingFactory(member=MemberFactory(full_legal_name="Dash Lister"))
        client.login(username="d_list", password="pass")
        response = client.get(reverse("hub_orientations_dashboard"))
        assert b"Dash Lister" in response.content

    def it_filters_by_guild(client: Client):
        _user_with_role("d_gfilter", fog_role=Member.FogRole.ADMIN)
        alpha = GuildFactory(name="Alpha")
        beta = GuildFactory(name="Beta")
        in_alpha = _past_booking(alpha, "In Alpha")
        in_beta = _past_booking(beta, "In Beta")
        client.login(username="d_gfilter", password="pass")
        response = client.get(reverse("hub_orientations_dashboard"), {"guild": alpha.pk})
        assert reverse("hub_orientation_respond", args=[in_alpha.pk]).encode() in response.content
        assert reverse("hub_orientation_respond", args=[in_beta.pk]).encode() not in response.content

    def it_filters_by_scope_mine(client: Client):
        user = _user_with_role("d_mine", fog_role=Member.FogRole.MEMBER)
        mine = GuildFactory(guild_lead=user.member)
        other = GuildFactory()
        my_booking = _past_booking(mine, "My Member")
        their_booking = _past_booking(other, "Their Member")
        client.login(username="d_mine", password="pass")
        response = client.get(reverse("hub_orientations_dashboard"), {"scope": "mine"})
        assert reverse("hub_orientation_respond", args=[my_booking.pk]).encode() in response.content
        assert reverse("hub_orientation_respond", args=[their_booking.pk]).encode() not in response.content

    def it_lets_guild_staff_mark_bookings_done_not_only_the_lead(client: Client):
        # A co-lead/secretary/etc. manages orientations just like the lead — the "Mark done"
        # action must show for a booking in a guild they staff, not only guilds they lead.
        from membership.models import GuildStaffMembership
        from tests.membership.factories import GuildStaffMembershipFactory

        lead = _user_with_role("d_slead", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=lead.member)
        staffer = _user_with_role("d_staff", fog_role=Member.FogRole.MEMBER)
        GuildStaffMembershipFactory(guild=guild, member=staffer.member, role=GuildStaffMembership.Role.SECRETARY)
        _past_booking(guild, "Staffed Booking")
        client.login(username="d_staff", password="pass")
        response = client.get(reverse("hub_orientations_dashboard"))
        assert response.status_code == 200
        assert b"Mark done" in response.content


def describe_orientations_export():
    def it_streams_a_csv(client: Client):
        _user_with_role("x_admin", fog_role=Member.FogRole.ADMIN)
        OrientationBookingFactory(member=MemberFactory(full_legal_name="CSV Person"))
        client.login(username="x_admin", password="pass")
        response = client.get(reverse("hub_orientations_export"))
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        body = b"".join(response.streaming_content)
        assert b"Member" in body
        assert b"CSV Person" in body

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("x_reg", fog_role=Member.FogRole.MEMBER)
        client.login(username="x_reg", password="pass")
        assert client.get(reverse("hub_orientations_export")).status_code == 403


def describe_orientation_add_member():
    def it_adds_a_member_to_a_slot(client: Client):
        _user_with_role("am_admin", fog_role=Member.FogRole.ADMIN)
        slot = OrientationSlotFactory()
        member = MemberFactory()
        client.login(username="am_admin", password="pass")
        response = client.post(reverse("hub_orientation_add_member"), {"member": member.pk, "slot": slot.pk})
        assert response.status_code == 302
        assert OrientationBooking.objects.filter(member=member, slot=slot).exists()

    def it_flashes_an_error_on_invalid_input(client: Client):
        _user_with_role("am_bad", fog_role=Member.FogRole.ADMIN)
        client.login(username="am_bad", password="pass")
        response = client.post(reverse("hub_orientation_add_member"), {}, follow=True)
        assert response.status_code == 200
        assert OrientationBooking.objects.count() == 0
        assert any("Couldn't add" in str(m) for m in response.context["messages"])

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("am_reg", fog_role=Member.FogRole.MEMBER)
        slot = OrientationSlotFactory()
        member = MemberFactory()
        client.login(username="am_reg", password="pass")
        response = client.post(reverse("hub_orientation_add_member"), {"member": member.pk, "slot": slot.pk})
        assert response.status_code == 403
        assert OrientationBooking.objects.count() == 0

    def it_rejects_get_requests(client: Client):
        _user_with_role("am_get", fog_role=Member.FogRole.ADMIN)
        client.login(username="am_get", password="pass")
        assert client.get(reverse("hub_orientation_add_member")).status_code == 405


def describe_orientation_toggle_completed():
    def it_marks_completed_for_an_editor(client: Client):
        _user_with_role("tc_admin", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory()
        client.login(username="tc_admin", password="pass")
        response = client.post(reverse("hub_orientation_toggle_completed", args=[booking.pk]))
        assert response.status_code == 302
        booking.refresh_from_db()
        assert booking.is_completed is True

    def it_unmarks_a_completed_booking(client: Client):
        _user_with_role("tc_un", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory()
        booking.mark_completed()
        client.login(username="tc_un", password="pass")
        client.post(reverse("hub_orientation_toggle_completed", args=[booking.pk]))
        booking.refresh_from_db()
        assert booking.is_completed is False

    def it_forbids_a_non_editor(client: Client):
        _user_with_role("tc_reg", fog_role=Member.FogRole.MEMBER)
        booking = OrientationBookingFactory()
        client.login(username="tc_reg", password="pass")
        assert client.post(reverse("hub_orientation_toggle_completed", args=[booking.pk])).status_code == 403

    def it_rejects_get_requests(client: Client):
        _user_with_role("tc_get", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory()
        client.login(username="tc_get", password="pass")
        assert client.get(reverse("hub_orientation_toggle_completed", args=[booking.pk])).status_code == 405


def describe_equipment_owned_rows():
    """Equipment-owned bookings (guild None) on the dashboard — the minimal v1 fix."""

    def _equipment_booking(name: str = "CNC Router") -> object:
        from tests.membership.factories import EquipmentFactory, OrientationTypeFactory

        equipment = EquipmentFactory(name=name)
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
        slot = OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)
        return OrientationBookingFactory(slot=slot)

    def it_renders_the_owner_name_for_an_admin_without_crashing(client: Client):
        _user_with_role("eqdash_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="eqdash_admin", password="pass")
        booking = _equipment_booking(name="Big Laser")
        response = client.get(reverse("hub_orientations_dashboard"))
        assert response.status_code == 200
        assert b"Big Laser" in response.content
        assert booking.member.display_name.encode() in response.content

    def it_excludes_equipment_rows_from_a_leads_mine_scope(client: Client):
        # Past-dated, so the (deliberately unscoped, future-only) Upcoming list can't
        # show it — the filterable table is the only surface, and Mine excludes it.
        from tests.membership.factories import EquipmentFactory, OrientationTypeFactory

        lead_user = _user_with_role("eqdash_lead")
        guild = GuildFactory(guild_lead=lead_user.member)
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
        equipment = EquipmentFactory(name="Scoped Laser")
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
        past_slot = OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=orientation_type,
            starts_at=timezone.now() - timedelta(days=2),
            ends_at=timezone.now() - timedelta(days=2) + timedelta(hours=1),
        )
        equipment_booking = OrientationBookingFactory(slot=past_slot)
        client.login(username="eqdash_lead", password="pass")
        response = client.get(reverse("hub_orientations_dashboard"), {"scope": "mine"})
        assert response.status_code == 200
        # The owner name never renders — the table's Mine scope excluded the row.
        # (The member NAME can't be asserted absent: the add-member picker lists everyone.)
        assert b"Scoped Laser" not in response.content
        assert equipment_booking is not None

    def it_lets_an_admin_add_a_member_to_an_equipment_slot(client: Client):
        from tests.membership.factories import EquipmentFactory, OrientationTypeFactory

        _user_with_role("eqdash_add", fog_role=Member.FogRole.ADMIN)
        client.login(username="eqdash_add", password="pass")
        equipment = EquipmentFactory()
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
        slot = OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)
        member = MemberFactory()
        response = client.post(reverse("hub_orientation_add_member"), {"member": member.pk, "slot": slot.pk})
        assert response.status_code == 302
        booking = OrientationBooking.objects.get(member=member)
        assert booking.guild is None
