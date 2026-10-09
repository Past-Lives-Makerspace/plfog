"""Member Directory: Leadership Directory titles under a member's name, and the Staff label.

A member's titles come from their role lines on the Leadership Directory cards on show, in tab
order and then line order, each title once, in one line under the name on both card layouts.
A member with none shows no line. The member type stored as ``employee`` reads "Staff". The
titles load for every card in a fixed number of queries.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from membership.models import LeadershipListing, LeadershipRole, Member
from tests.membership.factories import (
    LeadershipListingFactory,
    LeadershipRoleFactory,
    LeadershipTabFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

_DIRECTORY = reverse("hub_member_directory")


def _login(client: Client, username: str = "viewer") -> None:
    MembershipPlanFactory()
    User.objects.create_user(username=username, password="pw")
    client.login(username=username, password="pw")


def _card(html: str, name: str) -> str:
    """The directory card that carries ``name``."""
    cards = html.split('<div class="directory-card"')[1:]
    matches = [card for card in cards if name in card]
    assert len(matches) == 1, name
    return matches[0]


def _lee(**fields: object) -> Member:
    return MemberFactory(full_legal_name="Lee Titleholder", member_type=Member.MemberType.EMPLOYEE, **fields)


def _give_titles(member: Member) -> None:
    """Two tabs, the later one made first, with a title repeated across them in another case."""
    board = LeadershipListingFactory(member=member, tab=LeadershipTabFactory(sort_order=7))
    staff = LeadershipListingFactory(member=member, tab=LeadershipTabFactory(sort_order=2))
    LeadershipRoleFactory(listing=board, title="Board Advisor", sort_order=0)
    LeadershipRoleFactory(listing=staff, title="Director of Operations", sort_order=1)
    LeadershipRoleFactory(listing=staff, title="Co-Executive Director", sort_order=0)
    LeadershipRoleFactory(listing=board, title="director of operations", sort_order=1)


_TITLES = '<div class="pl-directory-titles">Co-Executive Director · Director of Operations · Board Advisor</div>'


def describe_titles_on_a_directory_card():
    def it_shows_the_titles_under_the_name_in_tab_then_line_order_once_each(client: Client):
        _login(client)
        lee = _lee()
        _give_titles(lee)
        card = _card(client.get(_DIRECTORY).content.decode(), "Lee Titleholder")
        assert _TITLES in card
        assert card.index('class="directory-card__name">Lee Titleholder<') < card.index(_TITLES)
        assert card.index(_TITLES) < card.index('class="directory-card__role">Staff<')

    def it_shows_them_on_the_photo_layout_too(client: Client):
        _login(client)
        lee = _lee(profile_photo="members/profile/lee.png")
        _give_titles(lee)
        card = _card(client.get(_DIRECTORY).content.decode(), "Lee Titleholder")
        assert "directory-card__avatar--photo" in card
        line = (
            '<div class="pl-directory-titles pl-directory-titles--photo">'
            "Co-Executive Director · Director of Operations · Board Advisor</div>"
        )
        assert card.index("Lee Titleholder</div>") < card.index(line) < card.index("directory-card__avatar--photo")

    def it_shows_a_title_for_a_member_who_is_not_staff(client: Client):
        _login(client)
        member = MemberFactory(full_legal_name="Vera Volunteer", member_type=Member.MemberType.VOLUNTEER)
        LeadershipRoleFactory(listing=LeadershipListingFactory(member=member), title="Membership Chair")
        card = _card(client.get(_DIRECTORY).content.decode(), "Vera Volunteer")
        assert '<div class="pl-directory-titles">Membership Chair</div>' in card

    def it_leaves_out_the_titles_of_a_card_taken_off_its_tab(client: Client):
        _login(client)
        lee = _lee()
        LeadershipRoleFactory(listing=LeadershipListingFactory(member=lee, is_listed=False), title="Former Treasurer")
        LeadershipRoleFactory(listing=LeadershipListingFactory(member=lee), title="Shop Steward")
        card = _card(client.get(_DIRECTORY).content.decode(), "Lee Titleholder")
        assert '<div class="pl-directory-titles">Shop Steward</div>' in card
        assert "Former Treasurer" not in card

    def it_shows_no_line_for_a_member_with_no_titles(client: Client):
        _login(client)
        lee = _lee()
        LeadershipRoleFactory(listing=LeadershipListingFactory(member=lee, is_listed=False), title="Former Treasurer")
        card = _card(client.get(_DIRECTORY).content.decode(), "Lee Titleholder")
        assert "pl-directory-titles" not in card
        assert "Former Treasurer" not in card

    def it_labels_the_employee_type_staff(client: Client):
        _login(client)
        _lee()
        card = _card(client.get(_DIRECTORY).content.decode(), "Lee Titleholder")
        assert '<span class="directory-card__role">Staff</span>' in card
        assert Member.MemberType.EMPLOYEE.value == "employee"
        assert Member.MemberType.EMPLOYEE.label == "Staff"


def describe_directory_title_queries():
    def _title_queries(client: Client) -> int:
        """How many queries the directory sends to the listing and role tables."""
        tables = (LeadershipListing._meta.db_table, LeadershipRole._meta.db_table)
        with CaptureQueriesContext(connection) as captured:
            assert client.get(_DIRECTORY).status_code == 200
        return sum(1 for query in captured.captured_queries if any(t in query["sql"].lower() for t in tables))

    def _holders(start: int, count: int) -> None:
        for n in range(start, start + count):
            listing = LeadershipListingFactory(member=MemberFactory(full_legal_name=f"Holder {n}"))
            LeadershipRoleFactory(listing=listing, title=f"Role A {n}")
            LeadershipRoleFactory(listing=listing, title=f"Role B {n}")

    def it_loads_every_cards_titles_in_the_same_two_queries_for_2_members_as_for_14(client: Client):
        _login(client)
        _holders(0, 2)
        few = _title_queries(client)
        _holders(2, 12)
        many = _title_queries(client)
        assert few == many == 2
        assert client.get(_DIRECTORY).content.decode().count('class="pl-directory-titles"') == 14
