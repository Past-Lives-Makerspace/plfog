"""BDD specs for the Badges switches on the admin member Edit page (#650).

The Details tab's Leadership section lists every badge with a switch that is on when the member
holds it. Each switch carries the Leadership editor's give and take URLs, which its script posts
to at once, so a member on no tab can be given a badge here. With no badges it says so and links
to the editor. The Details form's Save never touches badges.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from hub.forms import MemberBadgesForm
from membership.models import LeadershipTab, Member
from tests.membership.factories import (
    LeadershipBadgeFactory,
    LeadershipListingFactory,
    LeadershipTabFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

PASSWORD = "pw12345!"


@pytest.fixture(autouse=True)
def _no_tabs() -> None:
    LeadershipTab.objects.all().delete()


def _login(client: Client, username: str, role: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password=PASSWORD)
    member = Member.objects.get(user=user)
    member.fog_role = role
    member.save()
    client.login(username=username, password=PASSWORD)
    return member


def _dixie() -> Member:
    return MemberFactory(full_legal_name="Dixie Edited", member_type=Member.MemberType.EMPLOYEE)


def _edit_url(member: Member) -> str:
    return reverse("hub_admin_member_edit", kwargs={"pk": member.pk})


def _badges(html: str) -> str:
    """The Leadership section's Badges block, up to the Details form's actions."""
    start = html.index('<h3 class="hub-detail-label pl-leadership__subheading">Badges</h3>')
    return html[start : html.index('<div class="pl-person-actions">', start)]


def _toggle(block: str, badge_pk: int) -> str:
    """One badge's checkbox tag."""
    start = block.index(f'name="badges-badge_{badge_pk}"')
    return block[block.rindex("<input", 0, start) : block.index(">", start) + 1]


def describe_the_badges_section():
    def it_gives_every_badge_a_switch_on_for_the_ones_held(client: Client):
        _login(client, "admin1", Member.FogRole.ADMIN)
        dixie = _dixie()
        held = LeadershipBadgeFactory(label="Community Engagement Manager", color="#FFE066")
        not_held = LeadershipBadgeFactory(label="Elevator Certified")
        held.give(dixie)
        block = _badges(client.get(_edit_url(dixie)).content.decode())
        assert block.index("Community Engagement Manager") < block.index("Elevator Certified")
        assert "checked" in _toggle(block, held.pk)
        assert "checked" not in _toggle(block, not_held.pk)
        assert 'style="background-color: #FFE066; color: #000000;">Community Engagement Manager</span>' in block
        assert 'data-member-name="Dixie Edited"' in block

    def it_points_each_switch_at_the_give_and_take_endpoints(client: Client):
        _login(client, "admin2", Member.FogRole.ADMIN)
        dixie = _dixie()
        badge = LeadershipBadgeFactory(label="Shop Steward")
        tag = _toggle(_badges(client.get(_edit_url(dixie)).content.decode()), badge.pk)
        assert "data-member-badge-toggle" in tag
        assert 'data-badge-label="Shop Steward"' in tag
        assert f'data-give-url="{reverse("hub_admin_leadership_badge_give", args=[badge.pk, dixie.pk])}"' in tag
        assert f'data-take-url="{reverse("hub_admin_leadership_badge_take", args=[badge.pk, dixie.pk])}"' in tag

    def it_says_no_badges_yet_and_links_to_the_editor(client: Client):
        _login(client, "admin3", Member.FogRole.ADMIN)
        block = _badges(client.get(_edit_url(_dixie())).content.decode())
        assert "No badges yet." in block
        assert f'href="{reverse("hub_admin_leadership")}"' in block
        assert "data-member-badge-toggle" not in block

    def it_denies_a_plain_member_the_page(client: Client):
        _login(client, "plain", Member.FogRole.MEMBER)
        assert client.get(_edit_url(_dixie())).status_code == 403


def describe_giving_from_the_edit_page():
    def it_gives_a_member_on_no_tab_a_badge_that_then_shows_on_their_directory_card(client: Client):
        _login(client, "admin4", Member.FogRole.ADMIN)
        dixie = _dixie()
        badge = LeadershipBadgeFactory(label="Community Engagement Manager")
        tag = _toggle(_badges(client.get(_edit_url(dixie)).content.decode()), badge.pk)
        give = reverse("hub_admin_leadership_badge_give", args=[badge.pk, dixie.pk])
        assert give in tag
        response = client.post(give)
        assert response.status_code == 200
        assert response.json()["held"] is True
        assert list(dixie.leadership_badges.all()) == [badge]
        assert "checked" in _toggle(_badges(client.get(_edit_url(dixie)).content.decode()), badge.pk)
        assert ">Community Engagement Manager</li>" in client.get(reverse("hub_member_directory")).content.decode()

    def it_takes_it_off_both_directory_cards(client: Client):
        _login(client, "admin5", Member.FogRole.ADMIN)
        dixie = _dixie()
        LeadershipListingFactory(tab=LeadershipTabFactory(title="Staff Tab"), member=dixie)
        badge = LeadershipBadgeFactory(label="Community Engagement Manager")
        badge.give(dixie)
        assert ">Community Engagement Manager</li>" in client.get(reverse("hub_leadership_directory")).content.decode()
        assert client.post(reverse("hub_admin_leadership_badge_take", args=[badge.pk, dixie.pk])).status_code == 200
        # Anchored on the pill markup: the changelog renders on every page and may name a badge.
        assert ">Community Engagement Manager</li>" not in client.get(reverse("hub_member_directory")).content.decode()
        assert (
            ">Community Engagement Manager</li>" not in client.get(reverse("hub_leadership_directory")).content.decode()
        )

    def it_never_changes_badges_from_the_details_save(client: Client):
        _login(client, "admin6", Member.FogRole.ADMIN)
        dixie = _dixie()
        held = LeadershipBadgeFactory()
        other = LeadershipBadgeFactory()
        held.give(dixie)
        payload = {
            "full_legal_name": "Dixie Renamed",
            "preferred_name": "",
            "pronouns": "",
            "discord_handle": "",
            "about_me": "",
            "status": Member.Status.ACTIVE,
            "member_type": Member.MemberType.EMPLOYEE,
            "role": Member.FogRole.MEMBER,
            "show_in_directory": "on",
            # The switches sit inside the Details form, so a checked one rides along: ignored.
            f"badges-badge_{other.pk}": "on",
        }
        assert client.post(_edit_url(dixie), payload).status_code == 302
        dixie.refresh_from_db()
        assert dixie.full_legal_name == "Dixie Renamed"
        assert list(dixie.leadership_badges.all()) == [held]


def describe_MemberBadgesForm():
    def it_has_no_toggles_without_badges():
        assert MemberBadgesForm(_dixie()).toggles() == []

    def it_pairs_each_badge_with_its_field_in_the_order_made():
        dixie = _dixie()
        first, second = LeadershipBadgeFactory(label="First Made"), LeadershipBadgeFactory(label="Second Made")
        second.give(dixie)
        toggles = MemberBadgesForm(dixie).toggles()
        assert [toggle.badge for toggle in toggles] == [first, second]
        assert [toggle.field.value() for toggle in toggles] == [False, True]
        assert [toggle.field.label for toggle in toggles] == ["First Made", "Second Made"]
        assert toggles[0].field.html_name == f"badges-badge_{first.pk}"
