"""BDD specs for the Leadership Directory page (#464, part 2).

The team section is the admin's curated, ordered list of flagged profiles with their role
lines; the guild section is derived from each active guild's own lead, Co-Lead staff and
contact address. Assertions anchor on markup (ids, classes, hrefs) or on factory strings,
never on copy the changelog could also carry. Guild assertions read the guild section only,
because the sidebar lists the same guilds alphabetically and would pass for the wrong reason.
"""

from __future__ import annotations

import io
import re
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.defaultfilters import date as date_filter
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core.models import SiteConfiguration
from hub.templatetags.hub_tags import email_breaks, initials
from membership.models import EXAMPLE_GUILD_SLUG, LeadershipListing, LeadershipPage, Member
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    LeadershipListingFactory,
    LeadershipRoleFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

PASSWORD = "pw12345!"


def _login(client: Client) -> Member:
    user = User.objects.create_user(username="leader-viewer", email="leader-viewer@x.com", password=PASSWORD)
    member = Member.objects.get(user=user)
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    client.login(username="leader-viewer", password=PASSWORD)
    return member


def _page(client: Client) -> bytes:
    _login(client)
    response = client.get(reverse("hub_leadership_directory"))
    assert response.status_code == 200
    return response.content


def _section(body: bytes, section_id: str) -> bytes:
    """The body from the given section's opening tag onward."""
    return body[body.index(f'id="{section_id}"'.encode()) :]


def _photo() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (30, 60, 90)).save(buffer, format="PNG")
    return SimpleUploadedFile("face.png", buffer.getvalue(), content_type="image/png")


def describe_leadership_directory():
    def it_sends_a_signed_out_visitor_to_login(client: Client):
        response = client.get(reverse("hub_leadership_directory"))
        assert response.status_code == 302
        assert "next=/leadership/" in response["Location"]

    def it_renders_the_hero_then_the_team_then_the_guilds(client: Client):
        page = LeadershipPage.load()
        page.hero_title = "Who Runs This Place"
        page.hero_lead = "Every name in one place."
        page.team_heading = "The Crew"
        page.guilds_heading = "Shop Leads"
        page.save()
        body = _page(client)
        assert b"Who Runs This Place" in body
        assert b"Every name in one place." in body
        hero = body.index(b'class="pl-guild-hero pl-guild-hero--noimg pl-teach-hero"')
        team = body.index(b'id="leadership-team"')
        guilds = body.index(b'id="leadership-guilds"')
        assert hero < team < guilds
        assert body.index(b"The Crew") < body.index(b"Shop Leads")

    def it_marks_its_own_sidebar_entry_active(client: Client):
        assert b'href="/leadership/" class="hub-sidebar__link active"' in _page(client)

    def it_lists_only_listed_members_in_the_admins_order(client: Client):
        LeadershipListingFactory(sort_order=2, member=MemberFactory(full_legal_name="Zed Zephyr"))
        LeadershipListingFactory(sort_order=1, member=MemberFactory(full_legal_name="Ada Aldous"))
        LeadershipListingFactory(is_listed=False, member=MemberFactory(full_legal_name="Hidden Hank"))
        body = _page(client)
        assert b"Hidden Hank" not in body
        assert body.index(b"Ada Aldous") < body.index(b"Zed Zephyr")
        assert body.count(b'<article class="pl-leader-card">') == 2

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

    def it_links_the_discord_profile_only_when_the_account_is_verified(client: Client):
        LeadershipListingFactory(member=MemberFactory(discord_handle="@linked", discord_user_id="123456"))
        LeadershipListingFactory(member=MemberFactory(discord_handle="@typed"))
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

    def it_shows_no_updated_line_and_no_team_cards_when_nobody_is_listed(client: Client):
        body = _page(client)
        assert b"pl-leadership__updated" not in body
        assert b'<article class="pl-leader-card">' not in body
        assert b'id="leadership-team"' in body

    def it_omits_a_blank_intro(client: Client):
        page = LeadershipPage.load()
        page.team_intro = ""
        page.guilds_intro = "Straight from each guild."
        page.save()
        body = _page(client)
        assert body.count(b'class="pl-leadership__intro"') == 1
        assert b"Straight from each guild." in body


def describe_guild_leader_cards():
    def it_lists_active_guilds_alphabetically_and_skips_inactive_ones(client: Client):
        GuildFactory(name="Woodworking Guild")
        GuildFactory(name="Ceramics Guild")
        GuildFactory(name="Retired Guild", is_active=False)
        section = _section(_page(client), "leadership-guilds")
        assert b"Retired Guild" not in section
        assert section.index(b"Ceramics Guild") < section.index(b"Woodworking Guild")
        assert section.count(b'<article class="pl-leader-card pl-leader-card--guild">') == 2

    def it_shows_the_lead_and_co_leads_with_their_discord(client: Client):
        guild = GuildFactory(name="Tech Guild")
        guild.guild_lead = MemberFactory(
            full_legal_name="Patricia Lead", discord_handle="@patricia", discord_user_id="777"
        )
        guild.save(update_fields=["guild_lead"])
        GuildStaffMembershipFactory(
            guild=guild, member=MemberFactory(full_legal_name="Cole Colead", discord_handle="cole_typed")
        )
        GuildStaffMembershipFactory(guild=guild, member=MemberFactory(full_legal_name="Sam Staff"), custom=True)
        section = _section(_page(client), "leadership-guilds")
        assert b"Patricia Lead" in section
        assert b'href="https://discord.com/users/777"' in section
        assert section.index(b"Patricia Lead") < section.index(b"Cole Colead")
        assert b"cole_typed" in section
        assert b"Sam Staff" not in section
        assert section.count(b'class="pl-leader-card__eyebrow"') == 2

    def it_says_when_a_guild_has_no_lead(client: Client):
        GuildFactory(name="Leaderless Guild")
        section = _section(_page(client), "leadership-guilds")
        assert b"Leaderless Guild" in section
        assert b"pl-leader-card__person--none" in section

    def it_links_the_guild_address_when_it_has_one(client: Client):
        GuildFactory(name="Glass Guild", contact_email="glass@example.com")
        GuildFactory(name="Quiet Guild")
        section = _section(_page(client), "leadership-guilds")
        assert b'href="mailto:glass@example.com"' in section
        assert section.count(b"mailto:") == 1

    def it_uses_the_guild_logo_when_the_name_matches_one(client: Client):
        GuildFactory(name="Ceramics Guild")
        GuildFactory(name="Cartography Guild")
        section = _section(_page(client), "leadership-guilds")
        assert b"img/guild_logos/ceramics_color.svg" in section
        assert b'<span class="pl-leader-card__initials" aria-hidden="true">CG</span>' in section

    def it_shows_the_example_guild_only_while_the_demo_setting_is_on(client: Client):
        """The cards use Guild.objects.visible(), the member-facing gate the sidebar and the guild
        directory use: active guilds, plus the inactive example guild only while display_demo_guild
        is on. So the example guild is on the cards exactly when it is in the sidebar."""
        GuildFactory(name="Cartographers Guild", slug=EXAMPLE_GUILD_SLUG, is_active=False)
        _login(client)
        url = reverse("hub_leadership_directory")
        assert b"Cartographers Guild" not in _section(client.get(url).content, "leadership-guilds")
        config = SiteConfiguration.load()
        config.display_demo_guild = True
        config.save()
        body = client.get(url).content
        sidebar = re.search(rb'<nav class="hub-sidebar__nav".*?</nav>', body, re.S)
        assert sidebar is not None
        assert b"Cartographers Guild" in sidebar.group(0)
        assert b"Cartographers Guild" in _section(body, "leadership-guilds")

    def it_links_each_nameplate_to_the_guild_page(client: Client):
        guild = GuildFactory(name="Writers Guild")
        section = _section(_page(client), "leadership-guilds")
        href = reverse("hub_guild_detail", args=[guild.slug]).encode()
        assert b'class="pl-leader-card__plate-link" href="' + href + b'"' in section


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
