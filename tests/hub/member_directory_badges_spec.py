"""Member Directory: leadership badges on a member's card (#650).

A member's badges sit right after the member type, in the order they were made, each in its
own color with its black or white text, on both card layouts. They load for every card in one
query. Everyone who sees the directory sees the pills; the give and take switches live only on
the admin member Edit page.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from membership.models import LeadershipBadge, Member
from tests.membership.factories import (
    LeadershipBadgeFactory,
    LeadershipListingFactory,
    LeadershipTabFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

_DIRECTORY = reverse("hub_member_directory")


def _login(client: Client, username: str, role: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pw")
    member = user.member
    member.fog_role = role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    client.login(username=username, password="pw")
    return member


def _card(html: str, name: str) -> str:
    """The directory card that carries ``name``."""
    cards = html.split('<div class="directory-card"')[1:]
    matches = [card for card in cards if name in card]
    assert len(matches) == 1, name
    return matches[0]


def _pills(card: str) -> list[str]:
    return re.findall(r'<li class="pl-leader-badge"[^>]*>([^<]*)</li>', card)


def _dixie(**fields: object) -> Member:
    return MemberFactory(full_legal_name="Dixie Directory", member_type=Member.MemberType.EMPLOYEE, **fields)


def describe_badges_on_a_directory_card():
    def it_puts_each_badge_after_the_member_type_in_its_own_color(client: Client):
        _login(client, "viewer1", Member.FogRole.MEMBER)
        dixie = _dixie()
        badge = LeadershipBadgeFactory(label="Community Engagement Manager", color="#FFE066")
        badge.give(dixie)
        card = _card(client.get(_DIRECTORY).content.decode(), "Dixie Directory")
        assert card.index('class="directory-card__role">Employee<') < card.index("Community Engagement Manager")
        assert (
            '<li class="pl-leader-badge" style="background-color: #FFE066; color: #000000;">'
            "Community Engagement Manager</li>"
        ) in card
        assert '<ul class="pl-directory-badges" aria-label="Badges">' in card

    def it_lists_badges_in_the_order_they_were_made(client: Client):
        _login(client, "viewer2", Member.FogRole.MEMBER)
        dixie = _dixie()
        first = LeadershipBadgeFactory(label="Zed Made First")
        second = LeadershipBadgeFactory(label="Ada Made Second")
        second.give(dixie)
        first.give(dixie)
        card = _card(client.get(_DIRECTORY).content.decode(), "Dixie Directory")
        assert _pills(card) == ["Zed Made First", "Ada Made Second"]

    def it_shows_them_on_the_photo_layout_too_centered(client: Client):
        _login(client, "viewer3", Member.FogRole.MEMBER)
        dixie = _dixie(profile_photo="members/profile/dixie.png")
        LeadershipBadgeFactory(label="Elevator Certified", color="#092E4C").give(dixie)
        card = _card(client.get(_DIRECTORY).content.decode(), "Dixie Directory")
        assert "directory-card__avatar--photo" in card
        assert '<ul class="pl-directory-badges pl-directory-badges--center" aria-label="Badges">' in card
        assert card.index('class="directory-card__role">Employee<') < card.index("Elevator Certified")
        assert 'style="background-color: #092E4C; color: #FFFFFF;">Elevator Certified</li>' in card

    def it_shows_only_the_member_type_without_badges(client: Client):
        _login(client, "viewer4", Member.FogRole.MEMBER)
        _dixie()
        LeadershipBadgeFactory(label="Held By Nobody")
        card = _card(client.get(_DIRECTORY).content.decode(), "Dixie Directory")
        assert 'class="directory-card__role">Employee<' in card
        assert "pl-directory-badges" not in card
        assert "Held By Nobody" not in card

    def it_shows_a_badge_given_on_the_leadership_editor(client: Client):
        _login(client, "admin1", Member.FogRole.ADMIN)
        dixie = _dixie()
        LeadershipListingFactory(tab=LeadershipTabFactory(title="Staff Tab"), member=dixie)
        badge = LeadershipBadgeFactory(label="Shop Steward")
        assert client.post(reverse("hub_admin_leadership_badge_give", args=[badge.pk, dixie.pk])).status_code == 200
        card = _card(client.get(_DIRECTORY).content.decode(), "Dixie Directory")
        assert _pills(card) == ["Shop Steward"]

    def describe_for_a_member_who_is_not_an_admin():
        def it_shows_the_pills_but_no_switches_or_edit_link(client: Client):
            _login(client, "viewer5", Member.FogRole.MEMBER)
            dixie = _dixie()
            LeadershipBadgeFactory(label="Community Engagement Manager").give(dixie)
            html = client.get(_DIRECTORY).content.decode()
            assert _pills(_card(html, "Dixie Directory")) == ["Community Engagement Manager"]
            assert "data-member-badge-toggle" not in html
            assert "/give/" not in html and "/take/" not in html
            assert reverse("hub_admin_member_edit", args=[dixie.pk]) not in html

        def it_has_a_give_post_refused_and_nothing_given(client: Client):
            _login(client, "viewer6", Member.FogRole.MEMBER)
            dixie = _dixie()
            badge = LeadershipBadgeFactory()
            response = client.post(reverse("hub_admin_leadership_badge_give", args=[badge.pk, dixie.pk]))
            assert response.status_code == 403
            assert list(badge.members.all()) == []

        def it_has_a_take_post_refused_and_nothing_taken(client: Client):
            _login(client, "viewer7", Member.FogRole.GUILD_OFFICER)
            dixie = _dixie()
            badge = LeadershipBadgeFactory()
            badge.give(dixie)
            response = client.post(reverse("hub_admin_leadership_badge_take", args=[badge.pk, dixie.pk]))
            assert response.status_code == 403
            assert list(badge.members.all()) == [dixie]


def describe_directory_badge_queries():
    def it_loads_every_cards_badges_in_one_query(client: Client):
        _login(client, "admin2", Member.FogRole.ADMIN)
        badges = [LeadershipBadgeFactory(label=f"Query Badge {n}") for n in range(3)]
        for n in range(15):
            member = MemberFactory(full_legal_name=f"Holder {n}")
            for badge in badges:
                badge.give(member)
        with CaptureQueriesContext(connection) as captured:
            html = client.get(_DIRECTORY).content.decode()
        table = LeadershipBadge._meta.db_table
        badge_queries = [q for q in captured.captured_queries if table in q["sql"].lower()]
        assert len(badge_queries) == 1
        assert html.count('<li class="pl-leader-badge"') == 45
