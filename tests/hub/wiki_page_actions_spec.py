"""BDD specs: the reading page leads with Edit and keeps the rest behind More.

The header used to show up to ten equal buttons. Edit is now the primary button beside the
quick contributions (photo, tip, Still accurate, Report); Print, History and the staff
actions sit in a More menu that reuses the phone menu's items.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.test import Client

from membership.models import Member
from tests.features import turn_on
from tests.membership.factories import MembershipPlanFactory, WikiPageFactory


@pytest.fixture(autouse=True)
def _wiki_on(db):
    turn_on("wiki")


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _header(client: Client, page) -> str:
    html = client.get(page.get_absolute_url()).content.decode()
    return html.split('class="pl-wp-actions"', 1)[1].split('<p class="pl-wp-printhead"', 1)[0]


def _more_menu(header: str) -> str:
    return header.split('class="pl-wp-actions__more"', 1)[1]


def describe_the_reading_page_header():
    def it_makes_edit_the_primary_button(client: Client):
        _login(client, "actions_member")
        page = WikiPageFactory(title="Ripping Boards")
        header = _header(client, page)
        assert re.search(r'class="pl-btn pl-btn--primary pl-btn--sm">Edit</a>', header)

    def it_puts_print_and_history_in_the_more_menu_for_a_member(client: Client):
        _login(client, "actions_more")
        page = WikiPageFactory(title="Ripping Boards")
        menu = _more_menu(_header(client, page))
        assert "Print / Save PDF" in menu
        assert f"/wiki/p/{page.slug}/history/" in menu
        assert "Archive" not in menu

    def it_moves_the_staff_actions_into_the_more_menu(client: Client):
        _login(client, "actions_admin", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(title="Ripping Boards")
        header = _header(client, page)
        menu = _more_menu(header)
        assert "Add an official note" in menu
        assert "open-confirm', 'wiki-archive'" in menu
        # No second row of staff buttons in front of the member's own actions.
        assert "pl-wp-mod__actions" not in header
        assert "Add an Official Note" not in header

    def it_still_renders_the_archive_confirmation_both_menus_open(client: Client):
        _login(client, "actions_confirm", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(title="Ripping Boards")
        html = client.get(page.get_absolute_url()).content.decode()
        assert "Type ARCHIVE to confirm" in html
