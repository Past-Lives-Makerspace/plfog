"""BDD specs for guild orienters — the per-guild orientation-runner permission.

Orienters are members a guild lead/admin trusts to run that guild's orientations:
they reach the dashboard, the config editor, and the booking actions, but they
cannot edit the guild page, manage classes, or add other orienters. See
``membership.permissions.can_manage_orientations``.
"""

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
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _member_user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    if not member.full_legal_name:
        member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    return user


def _future(hours: int) -> str:
    return (timezone.localtime() + timedelta(days=3, hours=hours)).strftime("%Y-%m-%dT%H:%M")


def describe_orienter_access_to_the_config_editor():
    def it_lets_a_designated_orienter_open_the_editor(client: Client):
        guild = GuildFactory()
        user = _member_user("o_edit")
        guild.orienters.add(user.member)
        client.login(username="o_edit", password="pass")
        response = client.get(reverse("hub_guild_orientation_edit", args=[guild.pk]))
        assert response.status_code == 200

    def it_still_forbids_an_unrelated_member(client: Client):
        guild = GuildFactory()
        _member_user("o_stranger")
        client.login(username="o_stranger", password="pass")
        response = client.get(reverse("hub_guild_orientation_edit", args=[guild.pk]))
        assert response.status_code == 403

    def it_hides_the_orienters_roster_from_an_orienter(client: Client):
        guild = GuildFactory()
        user = _member_user("o_hide")
        guild.orienters.add(user.member)
        client.login(username="o_hide", password="pass")
        response = client.get(reverse("hub_guild_orientation_edit", args=[guild.pk]))
        assert response.context["can_manage_orienters"] is False
        assert reverse("hub_guild_orientation_orienter_add", args=[guild.pk]).encode() not in response.content

    def it_shows_the_orienters_roster_to_a_lead(client: Client):
        lead = _member_user("o_lead")
        guild = GuildFactory(guild_lead=lead.member)
        client.login(username="o_lead", password="pass")
        response = client.get(reverse("hub_guild_orientation_edit", args=[guild.pk]))
        assert response.context["can_manage_orienters"] is True
        assert reverse("hub_guild_orientation_orienter_add", args=[guild.pk]).encode() in response.content


def describe_guild_orientation_orienter_add():
    def it_lets_a_lead_designate_an_orienter(client: Client):
        lead = _member_user("a_lead")
        guild = GuildFactory(guild_lead=lead.member)
        target = _member_user("a_target")
        client.login(username="a_lead", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_orienter_add", args=[guild.pk]),
            {"member": target.member.pk},
        )
        assert response.status_code == 302
        assert guild.orienters.filter(pk=target.member.pk).exists()

    def it_forbids_an_orienter_from_minting_another(client: Client):
        guild = GuildFactory()
        orienter = _member_user("a_orient")
        guild.orienters.add(orienter.member)
        target = _member_user("a_target2")
        client.login(username="a_orient", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_orienter_add", args=[guild.pk]),
            {"member": target.member.pk},
        )
        assert response.status_code == 403
        assert not guild.orienters.filter(pk=target.member.pk).exists()

    def it_rejects_an_invalid_member_without_adding(client: Client):
        lead = _member_user("a_inv")
        guild = GuildFactory(guild_lead=lead.member)
        client.login(username="a_inv", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_orienter_add", args=[guild.pk]),
            {"member": "999999"},
        )
        assert response.status_code == 302
        assert guild.orienters.count() == 0

    def it_excludes_the_lead_and_existing_orienters_from_the_candidate_list(client: Client):
        lead = _member_user("c_lead")
        guild = GuildFactory(guild_lead=lead.member)
        existing = _member_user("c_existing")
        guild.orienters.add(existing.member)
        fresh = _member_user("c_fresh")
        client.login(username="c_lead", password="pass")
        response = client.get(reverse("hub_guild_orientation_edit", args=[guild.pk]))
        candidate_ids = set(
            response.context["orienter_add_form"].fields["member"].queryset.values_list("pk", flat=True)
        )
        assert fresh.member.pk in candidate_ids
        assert lead.member.pk not in candidate_ids
        assert existing.member.pk not in candidate_ids

    def it_lets_an_admin_add_an_orienter_to_a_guild_with_no_lead(client: Client):
        admin = _member_user("a_admin", fog_role=Member.FogRole.ADMIN)  # noqa: F841
        guild = GuildFactory(guild_lead=None)
        target = _member_user("a_leadless_target")
        client.login(username="a_admin", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_orienter_add", args=[guild.pk]),
            {"member": target.member.pk},
        )
        assert response.status_code == 302
        assert guild.orienters.filter(pk=target.member.pk).exists()

    def it_rejects_get(client: Client):
        lead = _member_user("a_get")
        guild = GuildFactory(guild_lead=lead.member)
        client.login(username="a_get", password="pass")
        assert client.get(reverse("hub_guild_orientation_orienter_add", args=[guild.pk])).status_code == 405


def describe_guild_orientation_orienter_remove():
    def it_lets_a_lead_remove_an_orienter(client: Client):
        lead = _member_user("rm_lead")
        guild = GuildFactory(guild_lead=lead.member)
        orienter = _member_user("rm_orient")
        guild.orienters.add(orienter.member)
        client.login(username="rm_lead", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_orienter_remove", args=[guild.pk, orienter.member.pk]),
        )
        assert response.status_code == 302
        assert not guild.orienters.filter(pk=orienter.member.pk).exists()

    def it_forbids_an_orienter_from_removing_others(client: Client):
        guild = GuildFactory()
        me = _member_user("rm_self")
        other = _member_user("rm_other")
        guild.orienters.add(me.member, other.member)
        client.login(username="rm_self", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_orienter_remove", args=[guild.pk, other.member.pk]),
        )
        assert response.status_code == 403
        assert guild.orienters.filter(pk=other.member.pk).exists()

    def it_rejects_get(client: Client):
        lead = _member_user("rm_get")
        guild = GuildFactory(guild_lead=lead.member)
        client.login(username="rm_get", password="pass")
        assert client.get(reverse("hub_guild_orientation_orienter_remove", args=[guild.pk, 1])).status_code == 405


def describe_orienter_dashboard_and_booking_actions():
    def it_lets_an_orienter_open_the_dashboard(client: Client):
        guild = GuildFactory()
        user = _member_user("d_open")
        guild.orienters.add(user.member)
        client.login(username="d_open", password="pass")
        assert client.get(reverse("hub_orientations_dashboard")).status_code == 200

    def it_serves_the_mine_scope_for_an_orienter(client: Client):
        guild = GuildFactory()
        user = _member_user("d_scope")
        guild.orienters.add(user.member)
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
        client.login(username="d_scope", password="pass")
        assert client.get(reverse("hub_orientations_dashboard") + "?scope=mine").status_code == 200

    def it_lets_an_orienter_confirm_a_booking_on_their_guild(client: Client):
        guild = GuildFactory()
        user = _member_user("b_confirm")
        guild.orienters.add(user.member)
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
        client.login(username="b_confirm", password="pass")
        response = client.post(reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm"})
        assert response.status_code == 302
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CONFIRMED

    def it_forbids_an_orienter_on_another_guilds_booking(client: Client):
        guild = GuildFactory()
        user = _member_user("b_other")
        guild.orienters.add(user.member)
        booking = OrientationBookingFactory()  # a slot on some other guild
        client.login(username="b_other", password="pass")
        response = client.post(reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm"})
        assert response.status_code == 403

    def it_lets_an_orienter_mark_a_booking_completed(client: Client):
        guild = GuildFactory()
        user = _member_user("b_done")
        guild.orienters.add(user.member)
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
        client.login(username="b_done", password="pass")
        response = client.post(reverse("hub_orientation_toggle_completed", args=[booking.pk]))
        assert response.status_code == 302
        booking.refresh_from_db()
        assert booking.is_completed is True

    def it_lets_an_orienter_add_a_one_off_slot(client: Client):
        guild = GuildFactory()
        user = _member_user("b_slot")
        guild.orienters.add(user.member)
        client.login(username="b_slot", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
            {"starts_at": _future(0), "ends_at": _future(1), "seats": "3", "location": "Lobby"},
        )
        assert response.status_code == 302
        assert guild.orientation_slots.exists()
