"""Member photos on equipment pages follow the profile photo setting (#1104).

The equipment twin of #1099's guild orientation fix: a member whose profile photo is
Hidden shows as an initial on the member-facing slot list and on every row of the manage
panel that pictures a person (Staff, Orientation Schedule, Former Managers, Pending
Requests, Upcoming Slots).
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _login(client: Client, username: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.full_legal_name = username.title()
    member.status = Member.Status.ACTIVE
    member.save()
    client.force_login(user)
    return member


def _with_photo(name: str, filename: str, *, public: bool) -> Member:
    member = MemberFactory(full_legal_name=name, directory_visibility={"profile_photo": public})
    member.profile_photo.name = f"profile_photos/{filename}"
    member.save(update_fields=["profile_photo"])
    return member


def _img(member: Member) -> str:
    return f'<img src="{member.profile_photo.url}" alt="" class="pl-orient-avatar">'


def _initial(letter: str) -> str:
    return f'pl-orient-avatar pl-orient-avatar--initials">{letter}<'


def describe_orientor_photo_on_the_equipment_page():
    def _page(client: Client, *, public: bool) -> tuple[str, Member]:
        _login(client, "viewer")
        equipment = EquipmentFactory()
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
        bob = _with_photo("Bob Orientor", "bob-orientor.jpg", public=public)
        EquipmentStaffMembershipFactory(equipment=equipment, member=bob)
        OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type, orienter=bob)
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert response.status_code == 200
        return response.content.decode(), bob

    def it_shows_initials_for_an_orientor_whose_photo_is_hidden(client: Client):
        body, _bob = _page(client, public=False)

        assert "bob-orientor.jpg" not in body
        assert _initial("B") in body

    def it_shows_the_photo_of_an_orientor_who_left_it_public(client: Client):
        body, bob = _page(client, public=True)

        assert _img(bob) in body
        assert _initial("B") not in body


def describe_member_photos_on_the_equipment_manage_panel():
    """The owning guild's lead sees every group: Sam manages and runs a slot, Fran has leftover hours, Rhea asked."""

    def _page(client: Client, *, public: bool) -> tuple[str, dict[str, Member]]:
        lead = _login(client, "lena")
        equipment = EquipmentFactory(guild=GuildFactory(guild_lead=lead))
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
        sam = _with_photo("Sam Staff", "sam-staff.jpg", public=public)
        EquipmentStaffMembershipFactory(equipment=equipment, member=sam)
        slot = OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type, orienter=sam)
        fran = _with_photo("Fran Former", "fran-former.jpg", public=public)
        OrientationAvailabilityFactory(equipment_owned=True, orientation_type=orientation_type, orienter=fran)
        rhea = _with_photo("Rhea Requester", "rhea-requester.jpg", public=public)
        OrientationBookingFactory(slot=slot, member=rhea)
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "orientation"})
        assert response.status_code == 200
        return response.content.decode(), {"sam": sam, "fran": fran, "rhea": rhea}

    def it_shows_initials_on_the_staff_schedule_and_slot_rows_for_a_hidden_orientor(client: Client):
        body, _people = _page(client, public=False)

        assert "sam-staff.jpg" not in body
        # Staff tab row, Orientation Schedule group, Upcoming Slots with-chip.
        assert body.count(_initial("S")) == 3

    def it_shows_initials_on_the_former_managers_row_for_a_hidden_photo(client: Client):
        body, _people = _page(client, public=False)

        assert "fran-former.jpg" not in body
        assert body.count(_initial("F")) == 1

    def it_shows_initials_on_the_pending_request_row_for_a_hidden_photo(client: Client):
        body, _people = _page(client, public=False)

        assert "rhea-requester.jpg" not in body
        assert body.count(_initial("R")) == 1

    def it_shows_every_public_photo(client: Client):
        body, people = _page(client, public=True)

        assert body.count(_img(people["sam"])) == 3
        assert body.count(_img(people["fran"])) == 1
        assert body.count(_img(people["rhea"])) == 1
        for letter in "SFR":
            assert _initial(letter) not in body
