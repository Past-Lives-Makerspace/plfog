"""BDD specs for equipment staff on the equipment page and the reservation cards (#615).

The equipment page gets a Staff card built from the guild page's Guild Staff markup, and every
reservation card (the Reservations page and the guild page's Reservations tab) names the staff
on one line. Both read ``EquipmentQuerySet.with_staff``, one prefetch per page however many
cards or staff there are. Assertions anchor on markup and factory names (STANDARDS.md, section 8).
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from membership.models import Equipment, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

STAFF_CARD = "data-equip-staff"
CARD_LINE = "data-equip-card-staff"


def _login(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = username.title()
    member.save()
    client.login(username=username, password="pass")
    return user


def _staff(equipment: Equipment, name: str, **member_fields: object) -> Member:
    member = MemberFactory(full_legal_name=name, **member_fields)
    EquipmentStaffMembershipFactory(equipment=equipment, member=member)
    return member


def _detail(client: Client, equipment: Equipment) -> str:
    response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
    assert response.status_code == 200
    return response.content.decode()


def _staff_names(content: str) -> list[str]:
    card = content[content.index(STAFF_CARD) :]
    card = card[: card.index('<h2 class="hub-detail-label" style="margin:0 0 0.5rem;">About</h2>')]
    return re.findall(r'<span class="hub-member-name">([^<]+)</span>', card)


def _card_lines(content: str) -> list[str]:
    return re.findall(rf"<div [^>]*{CARD_LINE}>([^<]+)</div>", content)


def describe_the_equipment_page():
    def it_lists_every_staff_member_by_name_with_their_role(client: Client):
        _login(client, "es_list")
        equipment = EquipmentFactory(name="Laser Engraver Mira 9", description="Cuts and engraves.")
        _staff(equipment, "damon Eckhoff")
        _staff(equipment, "Cara Quill")
        _staff(equipment, "Ada Birch")
        content = _detail(client, equipment)
        assert '<h2 class="hub-detail-label">Staff</h2>' in content
        # Case insensitive, like the Guild Staff card.
        assert _staff_names(content) == ["Ada Birch", "Cara Quill", "damon Eckhoff"]
        assert content.count('<span class="hub-badge">Manager</span>') == 3
        assert '<div class="hub-member-avatar">D</div>' in content

    def it_shows_the_preferred_name_like_the_guild_card(client: Client):
        _login(client, "es_pref")
        equipment = EquipmentFactory(name="Quillwood Lathe", description="Turns wood.")
        _staff(equipment, "Damon Quillwood Eckhoff", preferred_name="Damon E")
        content = _detail(client, equipment)
        assert _staff_names(content) == ["Damon E"]

    def it_sits_before_the_about_card(client: Client):
        _login(client, "es_order")
        equipment = EquipmentFactory(name="Quillwood Router", description="Routes edges.")
        _staff(equipment, "Ada Birch")
        content = _detail(client, equipment)
        assert content.index(STAFF_CARD) < content.index(">About</h2>")

    def it_has_no_staff_card_when_there_is_no_staff(client: Client):
        _login(client, "es_none")
        equipment = EquipmentFactory(name="Quillwood Bench")
        content = _detail(client, equipment)
        assert STAFF_CARD not in content
        assert '<h2 class="hub-detail-label">Staff</h2>' not in content

    def it_shows_staff_on_another_item_only_there(client: Client):
        _login(client, "es_scope")
        equipment = EquipmentFactory(name="Quillwood Bench")
        _staff(EquipmentFactory(name="Quillwood Saw"), "Ada Birch")
        assert STAFF_CARD not in _detail(client, equipment)

    def it_reads_the_staff_in_one_query_however_many_there_are(client: Client):
        _login(client, "es_queries")
        equipment = EquipmentFactory(name="Quillwood Kiln")
        url = reverse("hub_equipment_detail", args=[equipment.slug])

        def count_queries() -> int:
            client.get(url)  # warm the session and per-request caches
            with CaptureQueriesContext(connection) as ctx:
                assert client.get(url).status_code == 200
            return len(ctx.captured_queries)

        _staff(equipment, "Ada Birch")
        with_one = count_queries()
        _staff(equipment, "Bea Cole")
        _staff(equipment, "Cara Quill")
        assert count_queries() == with_one

    def describe_hidden_members():
        def it_treats_a_hidden_member_as_the_guild_staff_card_does(client: Client):
            """The Guild Staff card shows a hidden staffer; so does the Staff card (#615 parity)."""
            _login(client, "es_hidden")
            guild = GuildFactory(name="Quillwood Guild")
            equipment = EquipmentFactory(name="Quillwood Saw", guild=guild, description="Saws.")
            hidden = _staff(equipment, "Hana Hidden", hide_from_directory=True)
            GuildStaffMembershipFactory(guild=guild, member=hidden)
            guild_page = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
            assert '<span class="hub-member-name">Hana Hidden</span>' in guild_page
            assert _staff_names(_detail(client, equipment)) == ["Hana Hidden"]


def describe_the_reservation_cards():
    def it_names_the_staff_on_one_line(client: Client):
        _login(client, "es_card")
        equipment = EquipmentFactory(name="Laser Engraver Mira 9")
        _staff(equipment, "Damon Eckhoff")
        _staff(equipment, "ada Birch")
        content = client.get(reverse("hub_equipment_index")).content.decode()
        assert _card_lines(content) == ["Staff: ada Birch, Damon Eckhoff"]

    def it_shows_no_line_when_there_is_no_staff(client: Client):
        _login(client, "es_card_none")
        staffed = EquipmentFactory(name="Quillwood Saw")
        _staff(staffed, "Ada Birch")
        EquipmentFactory(name="Quillwood Bench")
        content = client.get(reverse("hub_equipment_index")).content.decode()
        assert content.count(CARD_LINE) == 1
        assert _card_lines(content) == ["Staff: Ada Birch"]

    def it_names_the_staff_on_the_guild_reservations_tab(client: Client):
        _login(client, "es_guild_tab")
        guild = GuildFactory(show_reservations_tab=True)
        equipment = EquipmentFactory(name="Quillwood Bandsaw", guild=guild)
        _staff(equipment, "Damon Eckhoff")
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        pane = content[content.index("data-guild-reservations") :]
        assert _card_lines(pane) == ["Staff: Damon Eckhoff"]

    def it_keeps_the_query_count_fixed_however_many_cards_and_staff(client: Client, django_assert_num_queries):
        _login(client, "es_card_queries")
        url = reverse("hub_equipment_index")
        first = EquipmentFactory(name="Quillwood A")
        _staff(first, "Ada Birch")
        client.get(url)  # warm the session and per-request caches
        with CaptureQueriesContext(connection) as one:
            assert client.get(url).status_code == 200
        for name in ("Quillwood B", "Quillwood C", "Quillwood D"):
            equipment = EquipmentFactory(name=name)
            _staff(equipment, f"{name} Staffer One")
            _staff(equipment, f"{name} Staffer Two")
        _staff(first, "Bea Cole")
        with django_assert_num_queries(len(one.captured_queries)):
            response = client.get(url)
        assert len(_card_lines(response.content.decode())) == 4

    def it_reads_the_guild_tab_staff_in_a_fixed_number_of_queries(client: Client, django_assert_num_queries):
        _login(client, "es_tab_queries")
        guild = GuildFactory(show_reservations_tab=True)
        url = reverse("hub_guild_detail", args=[guild.slug])
        _staff(EquipmentFactory(name="Quillwood A", guild=guild), "Ada Birch")
        client.get(url)
        with CaptureQueriesContext(connection) as one:
            assert client.get(url).status_code == 200
        for name in ("Quillwood B", "Quillwood C"):
            equipment = EquipmentFactory(name=name, guild=guild)
            _staff(equipment, f"{name} Staffer One")
            _staff(equipment, f"{name} Staffer Two")
        with django_assert_num_queries(len(one.captured_queries)):
            assert client.get(url).status_code == 200


def describe_with_staff():
    def it_prefetches_the_roster_in_one_query(django_assert_num_queries):
        first = EquipmentFactory(name="Quillwood A")
        second = EquipmentFactory(name="Quillwood B")
        EquipmentFactory(name="Quillwood C")
        EquipmentStaffMembershipFactory(equipment=first, member=MemberFactory(full_legal_name="Cara Quill"))
        EquipmentStaffMembershipFactory(equipment=first, member=MemberFactory(full_legal_name="ada Birch"))
        EquipmentStaffMembershipFactory(equipment=second, member=MemberFactory(full_legal_name="Bea Cole"))
        with django_assert_num_queries(2):
            items = list(Equipment.objects.order_by("name").with_staff())
            rosters = [[staff.member.display_name for staff in item.staff_roster] for item in items]
        assert rosters == [["ada Birch", "Cara Quill"], ["Bea Cole"], []]
