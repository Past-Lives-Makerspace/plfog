"""BDD specs for a wiki page's printable flyer (#1224).

The Flyer link in the Share This Page card, the flyer view's gate (the QR download's
``can_edit_wiki_page``), the rule that only a published, unarchived page reaches paper, and
what the sheet carries. The one page print is measured in tests/e2e/wiki_flyer_spec.py.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Member, WikiPage
from tests.features import turn_on
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    MembershipPlanFactory,
    WikiPageFactFactory,
    WikiPageFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _wiki_on(db):
    return turn_on("wiki")


def _login(
    client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER, status: str = Member.Status.ACTIVE
) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = status
    member.full_legal_name = username.split("@")[0].title()
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _flyer(page: WikiPage) -> str:
    return reverse("hub_wiki_flyer", args=[page.slug])


def _machine_page(**kwargs: object) -> WikiPage:
    tool = EquipmentFactory(name="SawStop PCS", guild=GuildFactory(name="Woodworking"))
    return WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=tool, guild=tool.guild, **kwargs)


def describe_is_printable():
    def it_is_true_for_a_live_page():
        assert WikiPageFactory().is_printable is True

    def it_is_false_for_an_archived_page():
        assert WikiPageFactory(archived=True).is_printable is False

    def it_is_false_for_a_page_the_safety_gate_holds():
        assert WikiPageFactory(official=True, is_published=False).is_printable is False


def describe_the_flyer_link_in_the_share_card():
    def it_links_the_flyer_in_a_new_tab_at_both_widths(client):
        page = WikiPageFactory(title="Table Saw")
        _login(client, "editor@example.com")
        body = client.get(page.get_absolute_url()).content.decode()
        link = f'href="{_flyer(page)}" target="_blank" data-pl-new-window rel="noopener" data-qr-share-flyer'
        # Once in the desktop card and once in the phone Details disclosure.
        assert body.count(link) == 2

    def it_leaves_the_link_off_a_page_the_safety_gate_holds(client):
        # An admin still gets the card (and its downloads) on a held page; it may not print.
        page = WikiPageFactory(title="Kiln Safety", official=True, is_published=False)
        _login(client, "admin@example.com", fog_role=Member.FogRole.ADMIN)
        body = client.get(page.get_absolute_url()).content.decode()
        assert "Share This Page" in body
        assert "data-qr-share-flyer" not in body
        assert _flyer(page) not in body

    def it_leaves_the_link_off_an_archived_page(client):
        page = WikiPageFactory(title="Old Saw", archived=True)
        _login(client, "admin@example.com", fog_role=Member.FogRole.ADMIN)
        body = client.get(page.get_absolute_url()).content.decode()
        assert _flyer(page) not in body

    def it_leaves_the_link_off_for_a_member_who_may_not_edit(client):
        page = WikiPageFactory(title="Shop Rules", official=True)
        _login(client, "member@example.com")
        body = client.get(page.get_absolute_url()).content.decode()
        assert _flyer(page) not in body


def describe_the_flyer_gate():
    def it_opens_for_a_member_who_may_edit(client):
        page = WikiPageFactory(title="Table Saw")
        _login(client, "editor@example.com")
        response = client.get(_flyer(page))
        assert response.status_code == 200
        assert [t.name for t in response.templates][0] == "hub/wiki_flyer.html"

    def it_forbids_a_member_on_an_official_page(client):
        page = WikiPageFactory(title="Shop Rules", official=True)
        _login(client, "member@example.com")
        response = client.get(_flyer(page))
        assert response.status_code == 403
        assert response.content == b"Forbidden"

    def it_forbids_a_member_who_is_not_active(client):
        page = WikiPageFactory(title="Table Saw")
        _login(client, "lapsed@example.com", status=Member.Status.INVITED)
        assert client.get(_flyer(page)).status_code == 403

    def it_answers_the_wikis_not_found_for_an_unknown_slug(client):
        _login(client, "editor@example.com")
        response = client.get(reverse("hub_wiki_flyer", args=["no-such-page"]))
        assert response.status_code == 404
        assert "hub/wiki_not_found.html" in [t.name for t in response.templates]

    def it_sends_a_signed_out_visitor_to_login(client):
        page = WikiPageFactory(title="Table Saw")
        response = client.get(_flyer(page))
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("account_login"))

    def it_answers_not_found_for_an_archived_page_even_to_an_admin(client):
        page = WikiPageFactory(title="Old Saw", archived=True)
        _login(client, "admin@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(_flyer(page))
        assert response.status_code == 404
        assert "hub/wiki_not_found.html" in [t.name for t in response.templates]

    def it_answers_not_found_for_a_held_page_even_to_an_admin(client):
        page = WikiPageFactory(title="Kiln Safety", official=True, is_published=False)
        _login(client, "admin@example.com", fog_role=Member.FogRole.ADMIN)
        assert client.get(_flyer(page)).status_code == 404

    def it_answers_not_found_for_a_proposal_to_its_own_author(client):
        user = _login(client, "author@example.com")
        page = WikiPageFactory(title="Router Safety", is_published=False, created_by=user.member)
        assert client.get(_flyer(page)).status_code == 404


def describe_the_flyer_sheet():
    @pytest.fixture
    def body(client) -> str:
        page = _machine_page(last_checked_at=timezone.make_aware(datetime(2026, 11, 3, 12, 0)))
        WikiPageFactFactory(page=page, label="Blade", value="10 inch, 40 tooth", sort_order=1)
        WikiPageFactFactory(page=page, label="Max rip", value="30 inches", sort_order=2)
        WikiPageFactFactory(page=page, label="Dust port", value="", sort_order=3)
        WikiPageFactFactory(page=page, label="Riving knife", value="   ", sort_order=4)
        _login(client, "editor@example.com")
        return client.get(_flyer(page)).content.decode()

    def it_carries_the_title_guild_and_machine(body):
        assert '<h1 class="pl-wkfl__title" data-wkfl-title>Table Saw</h1>' in body
        assert '<p class="pl-wkfl__scope" data-wkfl-scope>Woodworking</p>' in body
        assert '<p class="pl-wkfl__machine" data-wkfl-equipment>SawStop PCS</p>' in body

    def it_lists_the_answered_quick_answers_and_leaves_blank_ones_off(body):
        assert body.count("data-wkfl-fact>") == 2
        assert "10 inch, 40 tooth" in body
        assert "30 inches" in body
        assert "Dust port" not in body
        assert "Riving knife" not in body
        assert 'class="pl-wkfl__more pl-wkfl__more--none" data-wkfl-more="0"' in body

    def it_prints_a_qr_to_the_sticker_link_and_the_typed_link(body):
        page = WikiPage.objects.get(title="Table Saw")
        assert page.qr_svg() in body
        assert f"data-wkfl-url>{page.sticker_url_typed}</p>" in body
        assert "Scan for the full page" in body

    def it_dates_the_sheet_by_its_last_check(body):
        assert "data-wkfl-checked>Last checked 3 Nov 2026</p>" in body

    def it_has_a_print_toolbar_and_a_way_back_to_the_page(body):
        page = WikiPage.objects.get(title="Table Saw")
        assert 'onclick="window.print()" data-wkfl-print>Print / Save as PDF</button>' in body
        assert f'href="{page.get_absolute_url()}" data-wkfl-page-link>Open the wiki page</a>' in body
        assert 'class="pl-evfl-page__toolbar no-print"' in body

    def it_is_its_own_document_with_its_own_stylesheet(body):
        assert "css/wiki-flyer.css" in body
        assert "hub-sidebar" not in body

    def describe_a_space_wide_page_never_checked():
        def it_says_space_wide_and_not_checked_yet(client):
            page = WikiPageFactory(title="Front Door", guild=None)
            _login(client, "editor@example.com")
            body = client.get(_flyer(page)).content.decode()
            assert "data-wkfl-scope>Space wide</p>" in body
            assert "data-wkfl-equipment" not in body
            assert "data-wkfl-checked>Not checked yet</p>" in body

        def it_leaves_the_quick_answers_section_off_with_none_answered(client):
            page = WikiPageFactory(title="Front Door", guild=None)
            WikiPageFactFactory(page=page, label="Code", value="")
            _login(client, "editor@example.com")
            body = client.get(_flyer(page)).content.decode()
            assert "data-wkfl-facts" not in body

    def describe_a_long_quick_answers_list():
        def it_sends_eight_and_says_how_many_more_wait_on_the_page(client):
            page = WikiPageFactory(title="Laser Cutter")
            for index in range(11):
                WikiPageFactFactory(page=page, label=f"Row {index:02}", value=f"Answer {index:02}", sort_order=index)
            _login(client, "editor@example.com")
            body = client.get(_flyer(page)).content.decode()
            assert body.count("data-wkfl-fact>") == 8
            assert "Answer 07" in body
            assert "Answer 08" not in body
            assert 'class="pl-wkfl__more" data-wkfl-more="3">3 more Quick Answers on the full page</p>' in body

        def it_sends_exactly_eight_with_the_count_line_out_of_sight(client):
            page = WikiPageFactory(title="Laser Cutter")
            for index in range(8):
                WikiPageFactFactory(page=page, sort_order=index)
            _login(client, "editor@example.com")
            body = client.get(_flyer(page)).content.decode()
            assert body.count("data-wkfl-fact>") == 8
            assert 'class="pl-wkfl__more pl-wkfl__more--none" data-wkfl-more="0"' in body

        def it_names_one_left_off_in_the_singular(client):
            page = WikiPageFactory(title="Laser Cutter")
            for index in range(9):
                WikiPageFactFactory(page=page, sort_order=index)
            _login(client, "editor@example.com")
            body = client.get(_flyer(page)).content.decode()
            assert 'data-wkfl-more="1">1 more Quick Answer on the full page</p>' in body

    def it_steps_a_long_title_down(client):
        page = WikiPageFactory(title="A" * 150)
        _login(client, "editor@example.com")
        body = client.get(_flyer(page)).content.decode()
        assert 'class="pl-wkfl__title pl-wkfl__title--xlong"' in body
