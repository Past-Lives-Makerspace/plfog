"""BDD specs for the Leadership Directory list on the admin member edit Details tab (#464, read only since #564).

The Details tab shows the member's tabs and role lines, each tab linking to its pane on the
editor, plus a link that opens Add a person there with the member chosen. Editing happens on
the editor only, so a Details save never writes a listing.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import LeadershipListing, LeadershipTab, Member
from tests.membership.factories import LeadershipListingFactory, LeadershipRoleFactory, LeadershipTabFactory

pytestmark = pytest.mark.django_db

PASSWORD = "pw12345!"
_EDITOR = reverse("hub_admin_leadership")


@pytest.fixture(autouse=True)
def _no_tabs() -> None:
    LeadershipTab.objects.all().delete()


def _login(client: Client, username: str, role: str) -> Member:
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password=PASSWORD)
    member = Member.objects.get(user=user)
    member.fog_role = role
    member.save()
    client.login(username=username, password=PASSWORD)
    return member


def _target() -> Member:
    user = User.objects.create_user(username="lead-target", email="lead-target@x.com", password=PASSWORD)
    member = Member.objects.get(user=user)
    member.full_legal_name = "Target Member"
    member.save()
    return member


def _edit_url(member: Member) -> str:
    return reverse("hub_admin_member_edit", kwargs={"pk": member.pk})


def _section(html: str) -> str:
    """The Details tab's Leadership Directory block."""
    start = html.index('<h2 class="hub-detail-label pl-leadership__heading">')
    return html[start : html.index('<div class="pl-person-actions">', start)]


def describe_admin_member_edit_leadership():
    def it_denies_a_plain_member(client: Client):
        _login(client, "plain", Member.FogRole.MEMBER)
        assert client.get(_edit_url(_target())).status_code == 403

    def it_lists_each_tab_the_member_is_on_with_its_lines_linking_to_the_editor(client: Client):
        _login(client, "admin1", Member.FogRole.ADMIN)
        target = _target()
        board = LeadershipTabFactory(title="Board Tab", sort_order=2)
        leadership = LeadershipTabFactory(title="Leadership Tab", sort_order=1)
        LeadershipRoleFactory(listing=LeadershipListingFactory(tab=board, member=target), title="Board Advisor")
        on_leadership = LeadershipListingFactory(tab=leadership, member=target)
        LeadershipRoleFactory(listing=on_leadership, title="Guild Executor")
        LeadershipRoleFactory(listing=on_leadership, title="Treasurer Line", sort_order=1)
        LeadershipListingFactory(tab=LeadershipTabFactory(title="Hidden Tab"), member=target, is_listed=False)
        section = _section(client.get(_edit_url(target)).content.decode())
        assert section.index("Leadership Tab") < section.index("Board Tab")
        assert f'href="{_EDITOR}?tab={leadership.pk}"' in section
        assert f'href="{_EDITOR}?tab={board.pk}"' in section
        assert section.index("Guild Executor") < section.index("Treasurer Line") < section.index("Board Advisor")
        assert "Hidden Tab" not in section

    def it_says_a_member_with_no_lines_on_a_tab_has_none(client: Client):
        _login(client, "admin2", Member.FogRole.ADMIN)
        target = _target()
        LeadershipListingFactory(tab=LeadershipTabFactory(title="Bare Tab"), member=target)
        section = _section(client.get(_edit_url(target)).content.decode())
        assert "Bare Tab" in section
        assert 'class="pl-slide-badge"' not in section

    def it_shows_the_empty_state_and_the_add_link_for_a_member_on_no_tab(client: Client):
        _login(client, "admin3", Member.FogRole.ADMIN)
        target = _target()
        section = _section(client.get(_edit_url(target)).content.decode())
        assert 'class="hub-text-muted pl-leadership__none"' in section
        assert "pl-leadership__lines" not in section
        assert f'href="{_EDITOR}?add={target.pk}"' in section

    def it_edits_nothing_here(client: Client):
        _login(client, "admin4", Member.FogRole.ADMIN)
        target = _target()
        LeadershipListingFactory(member=target)
        html = client.get(_edit_url(target)).content.decode()
        assert 'name="leadership-is_listed"' not in html
        assert "roles-TOTAL_FORMS" not in html
        assert "<input" not in _section(html)

    def it_saves_the_details_without_touching_a_listing(client: Client):
        _login(client, "admin5", Member.FogRole.ADMIN)
        target = _target()
        listing = LeadershipListingFactory(member=target)
        stamp = listing.updated_at
        payload = {
            "full_legal_name": "Renamed Member",
            "preferred_name": "",
            "pronouns": "",
            "discord_handle": "",
            "about_me": "",
            "status": Member.Status.ACTIVE,
            "member_type": Member.MemberType.STANDARD,
            "role": Member.FogRole.MEMBER,
            "show_in_directory": "on",
            # The old Details controls, posted by a page still open from before #564: ignored.
            "leadership-is_listed": "",
        }
        response = client.post(_edit_url(target), payload)
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.full_legal_name == "Renamed Member"
        listing.refresh_from_db()
        assert (listing.is_listed, listing.updated_at) == (True, stamp)
        assert LeadershipListing.objects.count() == 1

    def it_re_renders_a_refused_details_save_with_the_list(client: Client):
        _login(client, "admin6", Member.FogRole.ADMIN)
        target = _target()
        LeadershipListingFactory(tab=LeadershipTabFactory(title="Still Shown Tab"), member=target)
        response = client.post(_edit_url(target), {"full_legal_name": ""})
        assert response.status_code == 200
        html = response.content.decode()
        assert 'class="pl-field-error"' in html
        assert "Still Shown Tab" in _section(html)
