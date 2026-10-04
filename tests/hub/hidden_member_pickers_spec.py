"""Specs for #614: members under the ``hide_from_directory`` override stay out of the pickers.

Guild leads, staff and instructors choose other members in the announcement composer's "add
anyone" list, the guild Staff tab, the orientation dashboard, the equipment "+ Add Manager"
form and the council meeting attendee list. The app store review account (and the Help
Center's example accounts) must never show up in any of them.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from hub.forms import EquipmentStaffAddForm, OrientationAddMemberForm, announcement_add_member_choices
from hub.meeting_views import _attendee_picker_context
from hub.views import _staff_candidates
from membership.models import Member
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MeetingFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _linked(username: str, *, hidden: bool) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com")
    Member.objects.filter(user=user).update(hide_from_directory=hidden)
    return user


def describe_without_hidden():
    def it_drops_only_members_under_the_override():
        shown = MemberFactory()
        hidden = MemberFactory(hide_from_directory=True)

        result = list(Member.objects.filter(pk__in=[shown.pk, hidden.pk]).without_hidden())

        assert result == [shown]


def describe_announcement_add_member_choices():
    def it_leaves_out_a_hidden_member():
        shown = _linked("pickershown", hidden=False)
        hidden = _linked("pickerhidden", hidden=True)

        values = {value for value, _label in announcement_add_member_choices()}

        assert f"user:{shown.pk}" in values
        assert f"user:{hidden.pk}" not in values


def describe_staff_candidates():
    def it_leaves_out_a_hidden_member():
        guild = GuildFactory()
        shown = MemberFactory()
        hidden = MemberFactory(hide_from_directory=True)

        candidates = list(_staff_candidates(guild))

        assert shown in candidates
        assert hidden not in candidates


def describe_orientation_add_member_form():
    def it_leaves_out_a_hidden_member():
        shown = MemberFactory()
        hidden = MemberFactory(hide_from_directory=True)

        queryset = OrientationAddMemberForm().fields["member"].queryset  # type: ignore[attr-defined]

        assert shown in queryset
        assert hidden not in queryset


def describe_equipment_staff_add_form():
    def it_leaves_out_a_hidden_member():
        shown = MemberFactory()
        hidden = MemberFactory(hide_from_directory=True)

        queryset = EquipmentStaffAddForm(equipment=EquipmentFactory()).fields["member"].queryset  # type: ignore[attr-defined]

        assert shown in queryset
        assert hidden not in queryset


def describe_council_attendee_picker():
    def it_leaves_out_a_hidden_guild_staff_member():
        shown = GuildStaffMembershipFactory().member
        hidden = GuildStaffMembershipFactory(member=MemberFactory(hide_from_directory=True)).member

        options = list(_attendee_picker_context(MeetingFactory(guild=None))["roster_options"])

        assert shown in options
        assert hidden not in options
