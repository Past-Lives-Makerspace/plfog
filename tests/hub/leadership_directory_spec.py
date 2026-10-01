"""BDD specs for the Leadership Directory page (#464, tabs #564).

Under the hero, one tab per LeadershipTab in admin order: the first open, or the one
``?tab=<id>`` names. People tabs show the admins' cards with their role lines; the Guild Leads
tab is derived from each visible guild's own lead, Co-Lead staff and contact address; a People
tab with nobody on it never reaches a member. Assertions anchor on markup (ids, classes,
hrefs) or on factory strings, never on copy the changelog could also carry. Guild assertions
read the Guild Leads pane only, because the sidebar lists the same guilds alphabetically and
would pass for the wrong reason.

The data migration makes two tabs in every migrated test database, so each spec starts from
none (``_no_tabs``) and builds exactly the tabs it needs.
"""

from __future__ import annotations

import io
import re
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.template.defaultfilters import date as date_filter
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core.models import SiteConfiguration
from hub.templatetags.hub_tags import email_breaks, initials
from membership.models import EXAMPLE_GUILD_SLUG, LeadershipListing, LeadershipPage, LeadershipTab, Member
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    LeadershipBadgeFactory,
    LeadershipListingFactory,
    LeadershipRoleFactory,
    LeadershipTabFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

PASSWORD = "pw12345!"
_URL = reverse("hub_leadership_directory")


@pytest.fixture(autouse=True)
def _no_tabs() -> None:
    LeadershipTab.objects.all().delete()


def _login(client: Client) -> Member:
    user = User.objects.create_user(username="leader-viewer", email="leader-viewer@x.com", password=PASSWORD)
    member = Member.objects.get(user=user)
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    client.login(username="leader-viewer", password=PASSWORD)
    return member


def _page(client: Client, query: str = "") -> bytes:
    _login(client)
    response = client.get(_URL + query)
    assert response.status_code == 200
    return response.content


def _admin_page(client: Client, query: str = "") -> bytes:
    member = _login(client)
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    response = client.get(_URL + query)
    assert response.status_code == 200
    return response.content


def _pane(body: bytes, tab: LeadershipTab) -> bytes:
    """One tab's pane: from its opening tag to the next pane, or to the end of the page."""
    start = body.index(f'id="leadership-pane-{tab.pk}"'.encode())
    following = body.find(b'id="leadership-pane-', start + 1)
    return body[start : following if following != -1 else len(body)]


def _tab_button(body: bytes, tab: LeadershipTab) -> bytes:
    """One tab's button in the strip, opening tag through its label."""
    match = re.search(rb'<button[^>]*id="leadership-tab-' + str(tab.pk).encode() + rb'".*?</button>', body, re.S)
    assert match is not None
    return match.group(0)


def _guild_leads() -> LeadershipTab:
    return LeadershipTabFactory(kind=LeadershipTab.Kind.GUILD_LEADS, title="Guild Leads")


def _photo() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (30, 60, 90)).save(buffer, format="PNG")
    return SimpleUploadedFile("face.png", buffer.getvalue(), content_type="image/png")


def describe_leadership_directory():
    def it_sends_a_signed_out_visitor_to_login(client: Client):
        response = client.get(_URL)
        assert response.status_code == 302
        assert "next=/leadership/" in response["Location"]

    def it_renders_the_hero_then_the_strip_then_the_panes(client: Client):
        page = LeadershipPage.load()
        page.hero_title = "Who Runs This Place"
        page.hero_lead = "Every name in one place."
        page.save()
        tab = LeadershipTabFactory()
        LeadershipListingFactory(tab=tab)
        body = _page(client)
        assert b"Who Runs This Place" in body
        assert b"Every name in one place." in body
        hero = body.index(b'class="pl-guild-hero pl-guild-hero--noimg pl-teach-hero"')
        strip = body.index(b'class="pl-tabs pl-leadership__tabs" role="tablist"')
        pane = body.index(f'id="leadership-pane-{tab.pk}"'.encode())
        assert hero < strip < pane

    def it_marks_its_own_sidebar_entry_active(client: Client):
        assert b'href="/leadership/" class="hub-sidebar__link active"' in _page(client)

    def it_shows_the_tabs_in_admin_order_and_opens_the_first(client: Client):
        second = LeadershipTabFactory(title="Council Tab", sort_order=2)
        first = LeadershipTabFactory(title="Leadership Tab", sort_order=1)
        LeadershipListingFactory(tab=first)
        LeadershipListingFactory(tab=second)
        guilds = _guild_leads()
        LeadershipTab.objects.filter(pk=guilds.pk).update(sort_order=0)
        body = _page(client)
        strip = [int(pk) for pk in re.findall(rb'id="leadership-tab-(\d+)"', body)]
        assert strip == [guilds.pk, first.pk, second.pk]
        assert b'class="vote-tab vote-tab--active"' in _tab_button(body, guilds)
        assert b'class="vote-tab"' in _tab_button(body, first)
        assert b'x-data="{ open: ' + str(guilds.pk).encode() + b' }"' in body
        assert b"x-cloak" not in _pane(body, guilds).split(b">", 1)[0]
        assert b"x-cloak" in _pane(body, first).split(b">", 1)[0]

    def it_opens_the_tab_the_query_names(client: Client):
        first = LeadershipTabFactory(sort_order=0)
        second = LeadershipTabFactory(sort_order=1)
        LeadershipListingFactory(tab=first)
        LeadershipListingFactory(tab=second)
        body = _page(client, f"?tab={second.pk}")
        assert b'class="vote-tab vote-tab--active"' in _tab_button(body, second)
        assert b'class="vote-tab"' in _tab_button(body, first)
        assert b"x-cloak" not in _pane(body, second).split(b">", 1)[0]

    def it_opens_the_first_tab_for_an_unknown_or_hidden_tab_id(client: Client):
        first = LeadershipTabFactory(sort_order=0)
        LeadershipListingFactory(tab=first)
        empty = LeadershipTabFactory(sort_order=1)
        for query in ("?tab=999999", f"?tab={empty.pk}", "?tab=nope"):
            client.logout()
            User.objects.filter(username="leader-viewer").delete()
            body = _page(client, query)
            assert b'class="vote-tab vote-tab--active"' in _tab_button(body, first)

    def it_hides_a_people_tab_with_nobody_on_it_from_a_member(client: Client):
        shown = LeadershipTabFactory(title="Shown Tab")
        LeadershipListingFactory(tab=shown)
        empty = LeadershipTabFactory(title="Empty Tab")
        hidden_only = LeadershipTabFactory(title="Hidden Only Tab")
        LeadershipListingFactory(tab=hidden_only, is_listed=False)
        body = _page(client)
        assert f'id="leadership-tab-{shown.pk}"'.encode() in body
        assert f'id="leadership-tab-{empty.pk}"'.encode() not in body
        assert f'id="leadership-pane-{hidden_only.pk}"'.encode() not in body

    def it_shows_an_admin_the_empty_tab_with_a_note(client: Client):
        empty = LeadershipTabFactory(title="Empty Tab")
        body = _admin_page(client)
        assert b'class="pl-leadership__empty"' in _pane(body, empty)

    def it_renders_no_strip_without_tabs(client: Client):
        body = _page(client)
        assert b'role="tablist"' not in body
        assert b'x-data="{ open: null }"' in body

    def it_puts_each_card_on_its_own_tab_and_a_person_on_two_tabs_twice(client: Client):
        leadership = LeadershipTabFactory(title="Leadership Tab")
        board = LeadershipTabFactory(title="Board Tab")
        morlock = MemberFactory(full_legal_name="Morlock Mender")
        LeadershipRoleFactory(listing=LeadershipListingFactory(tab=leadership, member=morlock), title="Guild Executor")
        LeadershipRoleFactory(listing=LeadershipListingFactory(tab=board, member=morlock), title="Board Advisor")
        body = _page(client)
        assert b"Guild Executor" in _pane(body, leadership)
        assert b"Board Advisor" not in _pane(body, leadership)
        assert b"Board Advisor" in _pane(body, board)
        assert body.count(b"Morlock Mender") == 2

    def it_lists_only_listed_members_in_the_admins_order(client: Client):
        tab = LeadershipTabFactory()
        LeadershipListingFactory(tab=tab, sort_order=2, member=MemberFactory(full_legal_name="Zed Zephyr"))
        LeadershipListingFactory(tab=tab, sort_order=1, member=MemberFactory(full_legal_name="Ada Aldous"))
        LeadershipListingFactory(tab=tab, is_listed=False, member=MemberFactory(full_legal_name="Hidden Hank"))
        pane = _pane(_page(client), tab)
        assert b"Hidden Hank" not in pane
        assert pane.index(b"Ada Aldous") < pane.index(b"Zed Zephyr")
        assert pane.count(b'<article class="pl-leader-card">') == 2

    def it_shows_two_role_lines_each_with_its_own_mailto(client: Client):
        listing = LeadershipListingFactory()
        LeadershipRoleFactory(
            listing=listing, title="Membership Director", email="membership@example.com", sort_order=0
        )
        LeadershipRoleFactory(listing=listing, title="Class Administrator", email="classes@example.com", sort_order=1)
        body = _page(client)
        assert body.count(b'class="pl-leader-card__title"') == 2
        assert b'href="mailto:membership@example.com"' in body
        assert b'href="mailto:classes@example.com"' in body
        # The visible address carries its wrap opportunity; the mailto: target stays untouched.
        assert b"<span>membership@<wbr>example.com</span>" in body
        assert body.index(b"Membership Director") < body.index(b"Class Administrator")

    def it_omits_the_email_row_for_a_role_with_no_address(client: Client):
        LeadershipRoleFactory(title="Guild Voting Contact", email="")
        body = _page(client)
        assert b"Guild Voting Contact" in body
        assert b"mailto:" not in body

    def it_shows_the_photo_and_pronouns_when_the_member_allows_it(client: Client):
        LeadershipListingFactory(member=MemberFactory(pronouns="they/them", profile_photo=_photo()))
        body = _page(client)
        assert b'class="pl-leader-card__photo"' in body
        # The photo sits inside the round medallion, the Member Directory's centre-square crop.
        assert body.index(b'class="pl-leader-card__avatar"') < body.index(b'class="pl-leader-card__photo"')
        assert b'<span class="pl-leader-card__pronouns">they/them</span>' in body
        assert b'class="pl-leader-card__initials"' not in body

    def it_hides_a_private_photo_and_pronouns_and_falls_back_to_initials(client: Client):
        member = MemberFactory(
            full_legal_name="Quiet Quill",
            pronouns="they/them",
            profile_photo=_photo(),
            directory_visibility={"profile_photo": False, "pronouns": False},
        )
        LeadershipListingFactory(member=member)
        body = _page(client)
        assert b'class="pl-leader-card__photo"' not in body
        assert b"they/them" not in body
        assert b'<span class="pl-leader-card__initials" aria-hidden="true">QQ</span>' in body
        assert body.index(b'class="pl-leader-card__avatar"') < body.index(b'aria-hidden="true">QQ</span>')

    def it_shows_an_admin_the_edit_link_on_the_open_tab(client: Client):
        tab = LeadershipTabFactory()
        LeadershipListingFactory(tab=tab)
        body = _admin_page(client)
        hero = body[: body.index(b'role="tablist"')]
        assert b'class="pl-leadership__admin"' in hero
        assert f'href="{reverse("hub_admin_leadership")}?tab={tab.pk}"'.encode() in hero

    def it_links_an_admin_to_the_editor_with_no_tabs(client: Client):
        body = _admin_page(client)
        assert f'href="{reverse("hub_admin_leadership")}"'.encode() in body

    def it_hides_the_edit_link_from_a_member(client: Client):
        body = _page(client)
        assert b'class="pl-leadership__admin"' not in body
        assert reverse("hub_admin_leadership").encode() not in body

    def it_links_the_discord_profile_only_when_the_account_is_verified(client: Client):
        tab = LeadershipTabFactory()
        LeadershipListingFactory(tab=tab, member=MemberFactory(discord_handle="@linked", discord_user_id="123456"))
        LeadershipListingFactory(tab=tab, member=MemberFactory(discord_handle="@typed"))
        body = _page(client)
        assert b'href="https://discord.com/users/123456" target="_blank" rel="noopener"' in body
        assert b"@typed" in body
        assert body.count(b'class="pl-leader-card__row pl-leader-card__row--text"') == 1

    def it_hides_a_private_discord_handle(client: Client):
        LeadershipListingFactory(
            member=MemberFactory(discord_handle="@secret", directory_visibility={"discord_handle": False})
        )
        assert b"@secret" not in _page(client)

    def it_dates_the_page_from_the_newest_listing_change(client: Client):
        listing = LeadershipListingFactory()
        stamp = timezone.now() - timedelta(days=3)
        LeadershipListing.objects.filter(pk=listing.pk).update(updated_at=stamp)
        body = _page(client)
        expected = f"Updated {date_filter(timezone.localtime(stamp))}".encode()
        assert b'<span class="pl-leadership__updated">' + expected in body

    def it_ignores_changes_to_profiles_nobody_can_see(client: Client):
        shown = LeadershipListingFactory()
        old = timezone.now() - timedelta(days=30)
        LeadershipListing.objects.filter(pk=shown.pk).update(updated_at=old)
        LeadershipListingFactory(is_listed=False)
        body = _page(client)
        expected = f"Updated {date_filter(timezone.localtime(old))}".encode()
        assert b'<span class="pl-leadership__updated">' + expected in body

    def it_shows_no_updated_line_when_nobody_is_listed(client: Client):
        _guild_leads()
        body = _page(client)
        assert b"pl-leadership__updated" not in body
        assert b'<article class="pl-leader-card">' not in body

    def it_shows_a_tabs_intro_and_omits_a_blank_one(client: Client):
        with_intro = LeadershipTabFactory(intro="Who keeps the lights on.")
        without = LeadershipTabFactory(intro="")
        LeadershipListingFactory(tab=with_intro)
        LeadershipListingFactory(tab=without)
        body = _page(client)
        assert b'<p class="pl-leadership__intro">Who keeps the lights on.</p>' in _pane(body, with_intro)
        assert b"pl-leadership__intro" not in _pane(body, without)

    def it_keeps_its_query_count_flat_as_tabs_people_lines_and_badges_grow(client: Client):
        """No N+1: one query for the tabs, one for the cards with members, one for the lines, one for the badges."""
        _login(client)
        badge = LeadershipBadgeFactory()

        def count() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert client.get(_URL).status_code == 200
            return len(queries.captured_queries)

        tab = LeadershipTabFactory()
        first = LeadershipListingFactory(tab=tab)
        LeadershipRoleFactory(listing=first)
        badge.give(first.member)
        _guild_leads()
        GuildStaffMembershipFactory(guild=GuildFactory(name="Only Guild", guild_lead=MemberFactory()))
        count()  # the first request after login warms the session and the site settings
        small = count()
        for _ in range(3):
            more = LeadershipTabFactory()
            for _ in range(3):
                listing = LeadershipListingFactory(tab=more)
                LeadershipRoleFactory(listing=listing)
                LeadershipRoleFactory(listing=listing)
                badge.give(listing.member)
                LeadershipBadgeFactory().give(listing.member)
        for name in ("Second Guild", "Third Guild"):
            guild = GuildFactory(name=name, guild_lead=MemberFactory())
            GuildStaffMembershipFactory(guild=guild)
        assert count() == small


def describe_badges_on_cards():
    """#571: a member's badges as pills under the name plate on every People tab card they have."""

    def _card(body: bytes, name: str) -> bytes:
        start = body.rindex(b'<article class="pl-leader-card">', 0, body.index(name.encode()))
        return body[start : body.index(b"</article>", start)]

    def it_shows_the_badges_under_the_name_plate_in_the_order_they_were_made(client: Client):
        tab = LeadershipTabFactory()
        member = LeadershipListingFactory(tab=tab, member=MemberFactory(full_legal_name="Dixie Doorman")).member
        elevator = LeadershipBadgeFactory(label="Elevator Certified", color="#FFE066")
        forklift = LeadershipBadgeFactory(label="Forklift Trained", color="#092E4C")
        forklift.give(member)
        elevator.give(member)
        card = _card(_page(client), "Dixie Doorman")
        plate, badges, body = (
            card.index(b"pl-leader-card__plate"),
            card.index(b'<ul class="pl-leader-card__badges"'),
            card.index(b"pl-leader-card__body"),
        )
        assert plate < badges < body
        light = (
            b'<li class="pl-leader-badge" style="background-color: #FFE066; color: #000000;">Elevator Certified</li>'
        )
        dark = b'<li class="pl-leader-badge" style="background-color: #092E4C; color: #FFFFFF;">Forklift Trained</li>'
        assert card.index(light) < card.index(dark)

    def it_shows_a_badge_on_every_card_the_member_has(client: Client):
        member = MemberFactory(full_legal_name="Morlock Mender")
        first = LeadershipListingFactory(member=member)
        second = LeadershipListingFactory(member=member)
        LeadershipBadgeFactory(label="Elevator Certified").give(member)
        body = _page(client)
        for listing in (first, second):
            assert b">Elevator Certified</li>" in _pane(body, listing.tab)

    def it_renders_a_card_with_no_badges_as_before(client: Client):
        LeadershipListingFactory(member=MemberFactory(full_legal_name="Plain Pat"))
        LeadershipBadgeFactory(label="Held By Nobody")
        card = _card(_page(client), "Plain Pat")
        assert b"pl-leader-card__badges" not in card
        assert b"Held By Nobody" not in card
        assert re.search(rb'</h3>\s*<div class="pl-leader-card__body">', card)

    def it_keeps_badges_off_the_guild_cards(client: Client):
        lead = MemberFactory(full_legal_name="Guild Gus")
        GuildFactory(name="Only Guild", guild_lead=lead)
        LeadershipBadgeFactory(label="Elevator Certified").give(lead)
        tab = _guild_leads()
        assert b"pl-leader-card__badges" not in _pane(_page(client), tab)


def describe_guild_leads_tab():
    def it_lists_active_guilds_alphabetically_and_skips_inactive_ones(client: Client):
        tab = _guild_leads()
        GuildFactory(name="Woodworking Guild")
        GuildFactory(name="Ceramics Guild")
        GuildFactory(name="Retired Guild", is_active=False)
        pane = _pane(_page(client), tab)
        assert b"Retired Guild" not in pane
        assert pane.index(b"Ceramics Guild") < pane.index(b"Woodworking Guild")
        assert pane.count(b'<article class="pl-leader-card pl-leader-card--guild">') == 2

    def it_keeps_the_medallion_off_the_guild_cards(client: Client):
        tab = _guild_leads()
        GuildFactory(name="Unbadged Guild")
        assert b"pl-leader-card__avatar" not in _pane(_page(client), tab)

    def it_shows_the_lead_and_co_leads_with_their_discord(client: Client):
        tab = _guild_leads()
        guild = GuildFactory(name="Tech Guild")
        guild.guild_lead = MemberFactory(
            full_legal_name="Patricia Lead", discord_handle="@patricia", discord_user_id="777"
        )
        guild.save(update_fields=["guild_lead"])
        GuildStaffMembershipFactory(
            guild=guild, member=MemberFactory(full_legal_name="Cole Colead", discord_handle="cole_typed")
        )
        GuildStaffMembershipFactory(guild=guild, member=MemberFactory(full_legal_name="Sam Staff"), custom=True)
        pane = _pane(_page(client), tab)
        assert b"Patricia Lead" in pane
        assert b'href="https://discord.com/users/777"' in pane
        assert pane.index(b"Patricia Lead") < pane.index(b"Cole Colead")
        assert b"cole_typed" in pane
        assert b"Sam Staff" not in pane
        assert pane.count(b'class="pl-leader-card__eyebrow"') == 2

    def it_says_when_a_guild_has_no_lead(client: Client):
        tab = _guild_leads()
        GuildFactory(name="Leaderless Guild")
        pane = _pane(_page(client), tab)
        assert b"Leaderless Guild" in pane
        assert b"pl-leader-card__person--none" in pane

    def it_links_the_guild_address_when_it_has_one(client: Client):
        tab = _guild_leads()
        GuildFactory(name="Glass Guild", contact_email="glass@example.com")
        GuildFactory(name="Quiet Guild")
        pane = _pane(_page(client), tab)
        assert b'href="mailto:glass@example.com"' in pane
        assert pane.count(b"mailto:") == 1

    def it_uses_the_guild_logo_when_the_name_matches_one(client: Client):
        tab = _guild_leads()
        GuildFactory(name="Ceramics Guild")
        GuildFactory(name="Cartography Guild")
        pane = _pane(_page(client), tab)
        assert b"img/guild_logos/ceramics_color.svg" in pane
        assert b'<span class="pl-leader-card__initials" aria-hidden="true">CG</span>' in pane

    def it_shows_the_example_guild_only_while_the_demo_setting_is_on(client: Client):
        """The cards use Guild.objects.visible(), the member-facing gate the sidebar and the guild
        directory use: active guilds, plus the inactive example guild only while display_demo_guild
        is on. So the example guild is on the cards exactly when it is in the sidebar."""
        tab = _guild_leads()
        GuildFactory(name="Cartographers Guild", slug=EXAMPLE_GUILD_SLUG, is_active=False)
        _login(client)
        assert b"Cartographers Guild" not in _pane(client.get(_URL).content, tab)
        config = SiteConfiguration.load()
        config.display_demo_guild = True
        config.save()
        body = client.get(_URL).content
        sidebar = re.search(rb'<nav class="hub-sidebar__nav".*?</nav>', body, re.S)
        assert sidebar is not None
        assert b"Cartographers Guild" in sidebar.group(0)
        assert b"Cartographers Guild" in _pane(body, tab)

    def it_links_each_nameplate_to_the_guild_page(client: Client):
        tab = _guild_leads()
        guild = GuildFactory(name="Writers Guild")
        pane = _pane(_page(client), tab)
        href = reverse("hub_guild_detail", args=[guild.slug]).encode()
        assert b'class="pl-leader-card__plate-link" href="' + href + b'"' in pane

    def it_shows_even_when_no_guild_is_visible(client: Client):
        tab = _guild_leads()
        assert f'id="leadership-tab-{tab.pk}"'.encode() in _page(client)


def describe_initials_filter():
    def it_takes_the_first_letter_of_the_first_two_words_upper_cased():
        assert initials("Lee Mendelsohn") == "LM"
        assert initials("ada lovelace byron") == "AL"

    def it_skips_punctuation_tokens_and_copes_with_one_word():
        assert initials("Sam / Samuel Rook") == "SS"
        assert initials("Morlock") == "M"

    def it_is_blank_for_a_blank_name():
        assert initials("") == ""


def describe_email_breaks_filter():
    def it_adds_a_break_opportunity_after_the_at_sign():
        assert email_breaks("lee@pastlives.space") == "lee@<wbr>pastlives.space"

    def it_escapes_the_address_and_trusts_only_the_break():
        assert email_breaks("<b>@x") == "&lt;b&gt;@<wbr>x"
