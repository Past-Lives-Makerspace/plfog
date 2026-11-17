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
    return header.split('class="pl-wp-actions__more', 1)[1]


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

    def it_hides_the_desktop_more_menu_on_a_phone_only_when_the_action_bar_carries_it(client: Client):
        _login(client, "actions_bar")
        page = WikiPageFactory(title="Ripping Boards")
        assert 'class="pl-wp-actions__more pl-wp-actions__more--bar"' in _header(client, page)

    def it_keeps_the_more_menu_for_a_phone_reader_with_no_action_bar(client: Client):
        user = _login(client, "actions_nobar")
        user.member.status = Member.Status.INVITED
        user.member.save()
        page = WikiPageFactory(title="Ripping Boards")
        header = _header(client, page)
        assert 'class="pl-wp-actions__more">' in header
        assert "Print / Save PDF" in _more_menu(header)


def describe_the_member_actions_in_the_more_menu():
    """Felix (2026-11-17): Add a Photo, Add a Tip, Still accurate and Report a Problem in More too."""

    def it_lists_all_four_for_a_member_who_may_edit(client: Client):
        _login(client, "more_four")
        page = WikiPageFactory(title="Ripping Boards")
        header = _header(client, page)
        menu = _more_menu(header)
        assert "$dispatch('open-modal', 'wiki-photo')\">Add a Photo</button>" in menu
        assert "$dispatch('open-modal', 'wiki-tip')\">Add a Tip</button>" in menu
        assert "$dispatch('open-confirm', 'wiki-confirm')\">Still accurate</button>" in menu
        assert "$dispatch('open-modal', 'wiki-report')\">Report a Problem</button>" in menu
        # The header buttons stay: Felix asked to add, not to move.
        assert "+ Add A Photo</button>" in header

    def it_puts_the_same_items_in_the_phone_menu(client: Client):
        _login(client, "more_phone")
        page = WikiPageFactory(title="Ripping Boards")
        html = client.get(page.get_absolute_url()).content.decode()
        bar = html.split('<div class="pl-wp-actionbar">', 1)[1]
        assert ">Add a Photo</button>" in bar
        assert ">Report a Problem</button>" in bar

    def it_leaves_out_photo_and_tip_where_the_member_may_not_edit(client: Client):
        _login(client, "more_official")
        page = WikiPageFactory(title="Laser Rules", official=True)
        menu = _more_menu(_header(client, page))
        assert "Add a Photo" not in menu
        assert "Add a Tip" not in menu
        assert ">Still accurate</button>" in menu

    def it_says_checked_today_instead_of_still_accurate(client: Client):
        from django.utils import timezone

        _login(client, "more_checked")
        page = WikiPageFactory(title="Ripping Boards", last_checked_at=timezone.now())
        menu = _more_menu(_header(client, page))
        assert ">Still accurate</button>" not in menu
        assert "Checked today" in menu

    def it_drops_report_once_the_member_has_an_open_report(client: Client):
        from tests.membership.factories import WikiReportFactory

        user = _login(client, "more_reported")
        page = WikiPageFactory(title="Ripping Boards")
        WikiReportFactory(page=page, reporter=user.member)
        menu = _more_menu(_header(client, page))
        assert "Report a Problem</button>" not in menu

    def it_shows_none_of_the_four_to_an_inactive_reader(client: Client):
        user = _login(client, "more_inactive")
        user.member.status = Member.Status.INVITED
        user.member.save()
        page = WikiPageFactory(title="Ripping Boards")
        menu = _more_menu(_header(client, page))
        for label in ("Add a Photo", "Add a Tip", ">Still accurate<", "Report a Problem"):
            assert label not in menu
