"""BDD specs for Guild Tools (#921): the tools page as guild officers, leads and staff see it.

An admin (view as aware) keeps Admin Tools. Anyone else who runs a guild sees the same page as
Guild Tools, with a section for one guild they run and a picker when they run more. Instructors
and capability holders who run no guild keep Admin Tools as it was, and a plain member sees no
entry and is sent home. Every card only links to a page the viewer could already open.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.http import HttpRequest
from django.test import Client, RequestFactory
from django.urls import reverse

from classes.factories import CategoryFactory, ClassOfferingFactory
from hub.guild_tools import ADMIN_TOOLS_TITLE, GUILD_TOOLS_TITLE, GuildToolsGuilds, runs_a_guild
from hub.view_as import ViewAs
from membership.models import AdminCapability, Guild, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

pytestmark = pytest.mark.django_db

_TOOLS = reverse("hub_admin_tools")


def _login(client: Client, username: str, *, superuser: bool = False) -> Member:
    """Sign in a fresh user and return the member the signup signal linked to it."""
    if superuser:
        User.objects.create_superuser(username=username, password="pass", email=f"{username}@example.com")
    else:
        User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return User.objects.get(username=username).member  # type: ignore[attr-defined]


def _view_as(client: Client, role: str) -> None:
    session = client.session
    session["view_as_role"] = role
    session.save()


def _grid(body: str) -> str:
    """The tool sections alone: the changelog rides on every page, so copy checks stay inside the grid."""
    found = re.search(r'<div class="pl-tools-sections".*?\n</div>', body, re.S)
    assert found is not None, "the tools sections did not render"
    return found.group(0)


def _sections(client: Client, query: str = "") -> list[tuple[str, str, list[str]]]:
    """Each rendered section as (key, heading, card titles in order), in page order."""
    grid = _grid(client.get(_TOOLS + query).content.decode())
    found = re.findall(
        r'<section class="pl-tools-section" data-tools-section="(\w+)".*?>([^<]+)</h2>(.*?)</section>', grid, re.S
    )
    return [(key, heading, re.findall(r'pl-tool-card__title">([^<]+)<', cards)) for key, heading, cards in found]


def _sidebar_label(body: str) -> str | None:
    """The tools entry's label in the sidebar, or None when the sidebar has no entry."""
    found = re.search(rf'<a href="{_TOOLS}" class="hub-sidebar__link[^"]*">.*?</svg>\s*([^<]+?)\s*</a>', body, re.S)
    return found.group(1) if found else None


def _page_title(body: str) -> str:
    found = re.search(r"<h1 class=\"hub-page-title\" data-tools-page-title>([^<]+)</h1>", body)
    assert found is not None, "the tools page title did not render"
    return found.group(1)


def _guild_cards(guild: Guild) -> list[str]:
    """The four guild section card targets for ``guild``, in page order."""
    edit = reverse("hub_guild_edit", args=[guild.pk])
    return [edit, reverse("hub_guild_orientations", args=[guild.pk]), f"{edit}?tab=reservations", f"{edit}?tab=events"]


def _guild_section_hrefs(client: Client, query: str = "") -> list[str]:
    grid = _grid(client.get(_TOOLS + query).content.decode())
    section = re.search(r'data-tools-section="guild".*?</section>', grid, re.S)
    assert section is not None, "the guild section did not render"
    return re.findall(r'class="hub-card pl-tool-card" href="([^"]+)"', section.group(0))


_GUILD_CARDS = ["Guild Settings", "Orientations", "Reservations", "Events"]


def describe_guild_tools():
    def describe_an_admin():
        def it_keeps_admin_tools_in_the_sidebar_and_the_page_title(client: Client):
            _login(client, "gt_admin", superuser=True)
            body = client.get(_TOOLS).content.decode()
            assert _sidebar_label(body) == "Admin Tools"
            assert _page_title(body) == "Admin Tools"
            assert "<title>Admin Tools" in body

        def it_shows_no_guild_section_and_no_guild_reports(client: Client):
            member = _login(client, "gt_admin_cards", superuser=True)
            GuildFactory(guild_lead=member)
            ClassOfferingFactory(category=CategoryFactory(guild=member.led_guilds.get()))
            sections = _sections(client)
            assert "guild" not in [key for key, _, _ in sections]
            reports = dict((key, cards) for key, _, cards in sections)["reports"]
            assert reports == ["Payments", "Reports", "Growth"]
            assert "Orientations" in dict((key, cards) for key, _, cards in sections)["tools"]

    def describe_an_admin_viewing_as_a_guild_lead():
        def it_sees_guild_tools_for_the_guild_they_lead(client: Client):
            member = _login(client, "gt_admin_lead", superuser=True)
            guild = GuildFactory(name="Admin Led Guild", guild_lead=member)
            _view_as(client, "member")
            body = client.get(_TOOLS).content.decode()
            assert _page_title(body) == "Guild Tools"
            assert _sidebar_label(body) == "Guild Tools"
            assert _guild_section_hrefs(client) == _guild_cards(guild)

        def it_sees_exactly_the_sections_that_guild_s_staff_member_sees(client: Client):
            admin = _login(client, "gt_admin_same", superuser=True)
            guild = GuildFactory(name="Shared View Guild", guild_lead=admin)
            _view_as(client, "member")
            as_admin = _sections(client)
            staff_client = Client()
            staffer = _login(staff_client, "gt_same_staffer")
            GuildStaffMembershipFactory(guild=guild, member=staffer)
            assert as_admin == _sections(staff_client)
            assert as_admin[0] == ("guild", "Shared View Guild", _GUILD_CARDS)

        def it_is_still_sent_home_when_leading_no_guild(client: Client):
            _login(client, "gt_admin_nolead", superuser=True)
            _view_as(client, "member")
            response = client.get(_TOOLS)
            assert response.status_code == 302
            assert response.url == reverse("hub_home")

    def describe_an_admin_viewing_as_a_guild_officer():
        def it_sees_guild_tools_with_every_active_guild_in_the_picker(client: Client):
            _login(client, "gt_admin_officer", superuser=True)
            first = GuildFactory(name="Aa Officer Guild")
            GuildFactory(name="Bb Officer Guild")
            GuildFactory(name="Hidden Officer Guild", is_active=False)
            _view_as(client, "guild_officer")
            body = client.get(_TOOLS).content.decode()
            assert _page_title(body) == "Guild Tools"
            picker = re.search(r"data-tools-guild-picker.*?</form>", body, re.S)
            assert picker is not None
            assert "Aa Officer Guild" in picker.group(0)
            assert "Bb Officer Guild" in picker.group(0)
            assert "Hidden Officer Guild" not in picker.group(0)
            assert _guild_section_hrefs(client) == _guild_cards(first)

    def describe_a_guild_lead():
        def it_says_guild_tools_in_the_sidebar_the_home_quick_link_and_the_page(client: Client):
            member = _login(client, "gt_lead")
            GuildFactory(guild_lead=member)
            body = client.get(_TOOLS).content.decode()
            assert _sidebar_label(body) == "Guild Tools"
            assert _page_title(body) == "Guild Tools"
            assert "<title>Guild Tools" in body
            home = client.get(reverse("hub_home")).content.decode()
            assert '<span class="pl-quicklink__label">Guild Tools</span>' in home

        def it_shows_its_guild_s_cards_under_its_name_with_no_picker(client: Client):
            member = _login(client, "gt_lead_cards")
            guild = GuildFactory(name="Lead Only Guild", guild_lead=member)
            body = client.get(_TOOLS).content.decode()
            assert "data-tools-guild-picker" not in body
            assert _sections(client)[0] == ("guild", "Lead Only Guild", _GUILD_CARDS)
            assert _guild_section_hrefs(client) == _guild_cards(guild)

        def it_opens_every_guild_card(client: Client):
            GuildFactory(guild_lead=_login(client, "gt_lead_opens"))
            for href in _guild_section_hrefs(client):
                assert client.get(href).status_code == 200, href

        def it_shows_registrations_only_while_its_guild_has_a_class(client: Client):
            member = _login(client, "gt_lead_regs")
            guild = GuildFactory(guild_lead=member)
            reports = dict((key, cards) for key, _, cards in _sections(client))["reports"]
            assert reports == ["Orientation Bookings"]
            ClassOfferingFactory(category=CategoryFactory(guild=guild))
            reports = dict((key, cards) for key, _, cards in _sections(client))["reports"]
            assert reports == ["Registrations", "Orientation Bookings"]

        def it_opens_both_reports(client: Client):
            member = _login(client, "gt_lead_reports")
            ClassOfferingFactory(category=CategoryFactory(guild=GuildFactory(guild_lead=member)))
            grid = _grid(client.get(_TOOLS).content.decode())
            for marker in ("data-tool-registrations", "data-tool-orientation-bookings"):
                href = re.search(rf'href="([^"]+)" {marker}', grid)
                assert href is not None, marker
                assert client.get(href.group(1).replace("&amp;", "&")).status_code == 200, marker

        def it_keeps_a_capability_s_cards_beside_the_guild_ones(client: Client):
            # No door closes: a Billing Administrator who also leads a guild still reaches Payments.
            member = _login(client, "gt_lead_biller")
            GuildFactory(guild_lead=member)
            member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
            reports = dict((key, cards) for key, _, cards in _sections(client))["reports"]
            assert reports == ["Payments", "Reports", "Orientation Bookings"]

    def describe_guild_staff_with_two_guilds():
        def _staff_two(client: Client, username: str) -> tuple[Guild, Guild]:
            member = _login(client, username)
            first = GuildFactory(name=f"Aa {username}")
            second = GuildFactory(name=f"Bb {username}")
            GuildStaffMembershipFactory(guild=second, member=member)
            GuildStaffMembershipFactory(guild=first, member=member)
            return first, second

        def it_offers_a_picker_with_both_guilds_and_opens_the_first(client: Client):
            first, second = _staff_two(client, "gt_two")
            body = client.get(_TOOLS).content.decode()
            options = re.findall(r'<option value="(\d+)"( selected)?>([^<]+)</option>', body)
            assert options == [(str(first.pk), " selected", first.name), (str(second.pk), "", second.name)]
            assert _guild_section_hrefs(client) == _guild_cards(first)

        def it_shows_the_guild_the_picker_names(client: Client):
            _first, second = _staff_two(client, "gt_two_pick")
            assert _sections(client, f"?guild={second.pk}")[0] == ("guild", second.name, _GUILD_CARDS)
            assert _guild_section_hrefs(client, f"?guild={second.pk}") == _guild_cards(second)

        def it_says_guild_tools(client: Client):
            _staff_two(client, "gt_two_title")
            assert _page_title(client.get(_TOOLS).content.decode()) == "Guild Tools"

    def describe_a_guild_officer():
        def it_sees_guild_tools_for_every_active_guild(client: Client):
            member = _login(client, "gt_officer")
            member.fog_role = Member.FogRole.GUILD_OFFICER
            member.save(update_fields=["fog_role"])
            first = GuildFactory(name="Aa Real Officer Guild")
            GuildFactory(name="Bb Real Officer Guild")
            body = client.get(_TOOLS).content.decode()
            assert _sidebar_label(body) == "Guild Tools"
            assert "data-tools-guild-picker" in body
            assert _guild_section_hrefs(client) == _guild_cards(first)

        def it_shows_no_guild_section_when_there_is_no_active_guild(client: Client):
            member = _login(client, "gt_officer_none")
            member.fog_role = Member.FogRole.GUILD_OFFICER
            member.save(update_fields=["fog_role"])
            sections = _sections(client)
            assert _page_title(client.get(_TOOLS).content.decode()) == "Guild Tools"
            assert sections == [("reports", "Reports", ["Orientation Bookings"])]

    def describe_an_instructor_who_is_not_guild_staff():
        def it_keeps_admin_tools_exactly_as_before(client: Client):
            member = _login(client, "gt_instructor")
            member.instructor_slug = "gt-instructor"
            member.save(update_fields=["instructor_slug"])
            body = client.get(_TOOLS).content.decode()
            assert _sidebar_label(body) == "Admin Tools"
            assert _page_title(body) == "Admin Tools"
            assert _sections(client) == [("tools", "Tools", ["Email Members"])]

    def describe_an_instructor_who_is_guild_staff():
        def it_sees_guild_tools(client: Client):
            member = _login(client, "gt_instructor_staff")
            member.instructor_slug = "gt-instructor-staff"
            member.save(update_fields=["instructor_slug"])
            GuildStaffMembershipFactory(member=member)
            body = client.get(_TOOLS).content.decode()
            assert _sidebar_label(body) == "Guild Tools"
            assert _page_title(body) == "Guild Tools"

    def describe_a_plain_member():
        def it_has_no_sidebar_entry_and_no_quick_link(client: Client):
            _login(client, "gt_member")
            home = client.get(reverse("hub_home")).content.decode()
            assert _sidebar_label(home) is None
            assert f'href="{_TOOLS}"' not in home

        def it_is_sent_home(client: Client):
            _login(client, "gt_member_url")
            response = client.get(_TOOLS)
            assert response.status_code == 302
            assert response.url == reverse("hub_home")

        def it_is_sent_home_when_it_leads_a_guild_but_is_no_longer_active(client: Client):
            # Guild authority follows an active membership (can_edit_guild), so a former member's
            # leftover lead row opens nothing and the page is not offered.
            member = _login(client, "gt_former_lead")
            GuildFactory(guild_lead=member)
            member.status = Member.Status.FORMER
            member.save(update_fields=["status"])
            response = client.get(_TOOLS)
            assert response.status_code == 302


def describe_crafted_requests():
    """A guild staffer cannot reach another guild's tools, through the page or the cards' targets."""

    def _staffer_of(client: Client, username: str) -> tuple[Guild, Guild]:
        member = _login(client, username)
        mine = GuildFactory(name=f"Mine {username}")
        theirs = GuildFactory(name=f"Theirs {username}")
        GuildStaffMembershipFactory(guild=mine, member=member)
        return mine, theirs

    def it_ignores_a_guild_query_naming_a_guild_they_do_not_run(client: Client):
        mine, theirs = _staffer_of(client, "gt_crafted_query")
        grid = _grid(client.get(f"{_TOOLS}?guild={theirs.pk}").content.decode())
        assert _guild_section_hrefs(client, f"?guild={theirs.pk}") == _guild_cards(mine)
        assert f"/guilds/{theirs.pk}/" not in grid
        assert theirs.name not in grid

    def it_ignores_a_guild_query_that_is_not_a_number(client: Client):
        mine, _theirs = _staffer_of(client, "gt_crafted_junk")
        assert _guild_section_hrefs(client, "?guild=abc") == _guild_cards(mine)

    def it_ignores_a_unicode_digit_rather_than_erroring(client: Client):
        mine, _theirs = _staffer_of(client, "gt_crafted_sup")
        assert client.get(f"{_TOOLS}?guild=%C2%B2").status_code == 200
        assert _guild_section_hrefs(client, "?guild=%C2%B2") == _guild_cards(mine)

    def it_refuses_every_card_target_on_another_guild(client: Client):
        _mine, theirs = _staffer_of(client, "gt_crafted_targets")
        for href in _guild_cards(theirs):
            assert client.get(href).status_code == 403, href

    def it_opens_every_card_target_on_their_own_guild(client: Client):
        mine, _theirs = _staffer_of(client, "gt_crafted_own")
        for href in _guild_cards(mine):
            assert client.get(href).status_code == 200, href


def describe_runs_a_guild():
    def _request(member: Member, role: str | None = None) -> HttpRequest:
        request = RequestFactory().get("/")
        request.user = member.user
        request.session = {"view_as_role": role} if role else {}
        request.view_as = ViewAs.for_request(request)
        return request

    def it_is_true_for_a_guild_officer_without_a_query(django_assert_num_queries, client: Client):
        member = _login(client, "gt_runs_officer")
        member.fog_role = Member.FogRole.GUILD_OFFICER
        member.save(update_fields=["fog_role"])
        request = _request(member)
        with django_assert_num_queries(0):
            assert runs_a_guild(request) is True

    def it_is_true_for_guild_staff(client: Client):
        member = _login(client, "gt_runs_staff")
        GuildStaffMembershipFactory(member=member)
        assert runs_a_guild(_request(member)) is True

    def it_is_false_for_a_plain_member(client: Client):
        assert runs_a_guild(_request(_login(client, "gt_runs_member"))) is False


def describe_guild_tools_guilds():
    def _request(member: Member, query: str = "") -> HttpRequest:
        request = RequestFactory().get("/" + query)
        request.user = member.user
        request.session = {}
        request.view_as = ViewAs.for_request(request)
        return request

    def it_has_no_selection_and_no_picker_with_no_guild(client: Client):
        picked = GuildToolsGuilds.for_request(_request(_login(client, "gt_none")))
        assert picked.guilds == []
        assert picked.selected is None
        assert picked.shows_picker is False

    def it_shows_no_picker_for_one_guild(client: Client):
        member = _login(client, "gt_one")
        guild = GuildFactory(guild_lead=member)
        picked = GuildToolsGuilds.for_request(_request(member))
        assert picked.selected == guild
        assert picked.shows_picker is False

    def it_picks_the_asked_guild_from_two(client: Client):
        member = _login(client, "gt_two_model")
        GuildFactory(name="Aa Model", guild_lead=member)
        second = GuildFactory(name="Bb Model", guild_lead=member)
        picked = GuildToolsGuilds.for_request(_request(member, f"?guild={second.pk}"))
        assert picked.selected == second
        assert picked.shows_picker is True


def describe_the_titles():
    def it_names_the_two_pages_in_felix_s_words():
        assert ADMIN_TOOLS_TITLE == "Admin Tools"
        assert GUILD_TOOLS_TITLE == "Guild Tools"
