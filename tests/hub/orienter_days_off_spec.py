"""BDD specs for days off on both owners' Orientation Schedule cards (#767): the "Off ..." line under each
person's hours, and Days Off opening My Hours (#1300, where days off are set; the old modal's routes
redirect there, ``tests/hours/old_editors_spec.py``)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import (
    Equipment,
    GuildStaffMembership,
    Member,
    OrientationDayOff,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _member_user(username: str, *, name: str, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = name
    member.status = Member.Status.ACTIVE
    member.save()
    member.sync_user_permissions()
    return user


def _login(client: Client, user: User) -> None:
    assert client.login(username=user.username, password="pass")


def _day(offset: int) -> date:
    return timezone.localdate() + timedelta(days=offset)


def _guild_setup() -> tuple[object, User, User, User]:
    """An orientation guild with a lead and two orientors, Bob and Alice, who can all log in."""
    lead = _member_user("do_lead", name="Lead Person")
    guild = GuildFactory(guild_lead=lead.member)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    OrientationTypeFactory(guild=guild)
    bob = _member_user("do_bob", name="Bob Placeholder")
    alice = _member_user("do_alice", name="Alice Ash")
    for user in (bob, alice):
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
    return guild, lead, bob, alice


def _page_url(guild: object) -> str:
    return reverse("hub_guild_orientations", args=[guild.pk])


def describe_the_card():
    def it_lists_each_persons_upcoming_days_off_under_their_hours(client: Client):
        guild, lead, bob, _alice = _guild_setup()
        first, second = _day(10), _day(40)
        OrientationDayOff.objects.create(orienter=bob.member, date=first)
        OrientationDayOff.objects.create(orienter=bob.member, date=second)
        OrientationDayOff.objects.create(orienter=bob.member, date=_day(-3))  # over, so not listed
        _login(client, lead)
        content = client.get(_page_url(guild)).content.decode()
        assert content.count("data-days-off-line") == 1  # Bob's line only; Alice and the lead have none
        # One Days Off for every kind (#1300): each day off by its My Hours label.
        assert f"Off {first:%a, %b} {first.day}; {second:%a, %b} {second.day}." in content
        # A lead's Days Off opens their own My Hours only (Felix 3); nobody else's row has one.
        assert content.count("data-days-off-button") == 1
        assert f'href="{reverse("hours:my_hours")}#days-off"' in content

    def it_shows_a_plain_orienter_only_their_own_button(client: Client):
        guild, lead, bob, alice = _guild_setup()
        _login(client, bob)
        content = client.get(_page_url(guild)).content.decode()
        assert content.count("data-days-off-button") == 1
        assert f'href="{reverse("hours:my_hours")}#days-off"' in content
        assert f"?member={alice.member.pk}" not in content
        assert f"?member={lead.member.pk}" not in content


def describe_on_equipment():
    def _equipment_setup() -> tuple[Equipment, User, User]:
        """A tool with an owned orientation, managed by Dana and Quinn, who can both log in."""
        equipment = EquipmentFactory(name="Etching Press")
        OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
        dana = _member_user("do_dana", name="Dana Reyes")
        quinn = _member_user("do_quinn", name="Quinn Quiet")
        for user in (dana, quinn):
            EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        return equipment, dana, quinn

    def _tab_url(equipment: Equipment) -> str:
        return f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation"

    def it_lists_the_days_off_on_the_card_for_an_admin(client: Client):
        equipment, dana, _quinn = _equipment_setup()
        first, second = _day(10), _day(40)
        OrientationDayOff.objects.create(orienter=dana.member, date=first)
        OrientationDayOff.objects.create(orienter=dana.member, date=second)
        _login(client, _member_user("do_tool_admin", name="Site Admin", fog_role=Member.FogRole.ADMIN))
        content = client.get(_tab_url(equipment)).content.decode()
        assert f"Off {first:%a, %b} {first.day}; {second:%a, %b} {second.day}." in content
        assert content.count("data-days-off-line") == 1
        # An admin's Days Off on Dana's row opens Dana's My Hours (#1300).
        assert f'href="{reverse("hours:my_hours")}?member={dana.member.pk}#days-off"' in content
