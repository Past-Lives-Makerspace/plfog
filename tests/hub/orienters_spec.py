"""BDD specs for orienters — now a guild staff role that can run orientations.

Orienters are members given the ``orienter`` staff role on a guild (managed on the
guild's Staff tab). Like every staff role they carry full guild-lead permissions, so
they reach the orientation dashboard, the config editor, and the booking actions for
their guild. See ``membership.permissions.can_manage_orientations``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import GuildStaffMembership, Member, OrientationBooking
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
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


def _make_orienter(guild: object, user: User) -> None:
    GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)


def _future(hours: int) -> str:
    return (timezone.localtime() + timedelta(days=3, hours=hours)).strftime("%Y-%m-%dT%H:%M")


def describe_orienter_access_to_the_config_editor():
    def it_lets_an_orienter_open_the_editor(client: Client):
        # The editor is an in-page tab now; the old URL redirects an orienter through to it.
        guild = GuildFactory()
        user = _member_user("o_edit")
        _make_orienter(guild, user)
        client.login(username="o_edit", password="pass")
        response = client.get(reverse("hub_guild_orientation_edit", args=[guild.pk]), follow=True)
        assert response.status_code == 200
        assert b"Recurring hours" in response.content

    def it_still_forbids_an_unrelated_member(client: Client):
        guild = GuildFactory()
        _member_user("o_stranger")
        client.login(username="o_stranger", password="pass")
        response = client.get(reverse("hub_guild_orientation_edit", args=[guild.pk]))
        assert response.status_code == 403


def describe_orienter_dashboard_and_booking_actions():
    def it_lets_an_orienter_open_the_dashboard(client: Client):
        guild = GuildFactory()
        user = _member_user("d_open")
        _make_orienter(guild, user)
        client.login(username="d_open", password="pass")
        assert client.get(reverse("hub_orientations_dashboard")).status_code == 200

    def it_serves_the_mine_scope_for_an_orienter(client: Client):
        guild = GuildFactory()
        user = _member_user("d_scope")
        _make_orienter(guild, user)
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
        client.login(username="d_scope", password="pass")
        assert client.get(reverse("hub_orientations_dashboard") + "?scope=mine").status_code == 200

    def it_lets_an_orienter_confirm_a_booking_on_their_guild(client: Client):
        guild = GuildFactory()
        user = _member_user("b_confirm")
        _make_orienter(guild, user)
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
        client.login(username="b_confirm", password="pass")
        response = client.post(reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm"})
        assert response.status_code == 302
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CONFIRMED

    def it_forbids_an_orienter_on_another_guilds_booking(client: Client):
        guild = GuildFactory()
        user = _member_user("b_other")
        _make_orienter(guild, user)
        booking = OrientationBookingFactory()  # a slot on some other guild
        client.login(username="b_other", password="pass")
        response = client.post(reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm"})
        assert response.status_code == 403

    def it_lets_an_orienter_mark_a_booking_completed(client: Client):
        guild = GuildFactory()
        user = _member_user("b_done")
        _make_orienter(guild, user)
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
        client.login(username="b_done", password="pass")
        response = client.post(reverse("hub_orientation_toggle_completed", args=[booking.pk]))
        assert response.status_code == 302
        booking.refresh_from_db()
        assert booking.is_completed is True

    def it_lets_an_orienter_add_a_one_off_slot(client: Client):
        guild = GuildFactory()
        user = _member_user("b_slot")
        _make_orienter(guild, user)
        client.login(username="b_slot", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
            {"starts_at": _future(0), "ends_at": _future(1), "seats": "3", "location": "Lobby"},
        )
        assert response.status_code == 302
        assert guild.orientation_slots.exists()
