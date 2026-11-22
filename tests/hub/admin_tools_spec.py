"""BDD specs for the Admin Tools hub — the relocated Payments and Reports cards.

Payments and Reports moved off the sidebar into Admin Tools as two cards gated by
``has_billing_admin_access`` (fog admin OR ``BILLING_APPROVER``). A capability-only
member now also gets into Admin Tools at all (the new ``_can_use_admin_tools`` arm),
closing the "could open the pages by URL but had no navigation" gap.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from django.test import Client
from django.urls import reverse

from membership.models import AdminCapability, Member
from classes.factories import CategoryFactory, ClassOfferingFactory
from hub.views import ADMIN_TOOL_SECTIONS, ToolCard, admin_tool_sections
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

_PAYMENTS_HREF = reverse("billing_admin_dashboard")
_REPORTS_HREF = reverse("billing_admin_reports")
_MEMBERS_HREF = reverse("hub_admin_members")
_SETTINGS_HREF = reverse("hub_admin_site_settings")


def _login_user(client: Client, username: str) -> User:
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user


def _login_billing_approver(client: Client, username: str = "biller") -> User:
    user = _login_user(client, username)
    user.member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)  # type: ignore[attr-defined]
    return user


def _login_events_approver(client: Client, username: str = "calendar_admin") -> User:
    user = _login_user(client, username)
    user.member.admin_capabilities.create(capability=AdminCapability.Capability.EVENTS_APPROVER)  # type: ignore[attr-defined]
    return user


def _login_guild_lead(client: Client, username: str = "lead") -> User:
    user = _login_user(client, username)
    GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
    return user


def _login_tour_guide(client: Client, username: str = "tour_guide") -> User:
    user = _login_user(client, username)
    user.member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)  # type: ignore[attr-defined]
    return user


def _login_superuser(client: Client, username: str = "fogadmin") -> User:
    User.objects.create_superuser(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return User.objects.get(username=username)


def _login_webmaster(client: Client, username: str = "webmaster") -> User:
    """An admin holding the Webmaster capability, the only viewer of the Webmaster tiles (#1291)."""
    user = _login_superuser(client, username)
    user.member.admin_capabilities.create(capability=AdminCapability.Capability.WEBMASTER)  # type: ignore[attr-defined]
    return user


def _grant_teaching(user: User) -> None:
    """An ACTIVE member with a profile and the teaching grant, what the teaching portal's gate admits."""
    Member.objects.filter(user=user).update(
        instructor_slug=user.username,
        instructor_oriented_at=timezone.now(),
        status=Member.Status.ACTIVE,
    )


def _login_instructor(client: Client, username: str = "instructor") -> User:
    user = _login_user(client, username)
    _grant_teaching(user)
    return user


_TOURS_HREF = reverse("tours:upcoming")


def describe_admin_tools_without_tours():
    """#1278: Tours moved to the sidebar, so the Tools page has no Tours card and Tour Guide earns no Tools entry."""

    def it_gives_a_tour_guide_no_tools_entry_and_the_tours_entry(client: Client):
        _login_tour_guide(client, "tours_guide")
        content = client.get(reverse("hub_member_directory")).content.decode()
        assert f'href="{reverse("hub_admin_tools")}"' not in content
        assert f'href="{_TOURS_HREF}" class="hub-sidebar__link " data-nav="tours"' in content
        assert client.get(reverse("hub_admin_tools")).url == reverse("hub_home")
        assert client.get(_TOURS_HREF).status_code == 200

    def it_shows_an_admin_no_tours_card(client: Client):
        _login_superuser(client, "tours_admin")
        content = client.get(reverse("hub_admin_tools")).content.decode()
        assert f'class="hub-card pl-tool-card" href="{_TOURS_HREF}"' not in content
        assert "data-tool-tours" not in content

    def it_sends_a_plain_member_from_tours_to_give_tours(client: Client):
        _login_user(client, "tours_plain")
        assert reverse("hub_admin_tools") not in client.get(reverse("hub_member_directory")).content.decode()
        assert client.get(_TOURS_HREF).url == reverse("tours:give")

    def it_keeps_the_tours_page_open_to_an_admin_previewing_as_a_member(client: Client):
        user = _login_superuser(client, "tours_preview")
        GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
        session = client.session
        session["view_as_role"] = "member"
        session.save()
        assert client.get(_TOURS_HREF).status_code == 200


def describe_admin_tools_billing_cards():
    def describe_a_billing_approver_only_member():
        def it_sees_the_admin_tools_sidebar_entry(client: Client):
            _login_billing_approver(client, "appr_sidebar")
            body = client.get(reverse("hub_member_directory")).content.decode()
            assert reverse("hub_admin_tools") in body

        def it_reaches_the_admin_tools_page(client: Client):
            _login_billing_approver(client, "appr_page")
            assert client.get(reverse("hub_admin_tools")).status_code == 200

        def it_sees_the_payments_and_reports_cards(client: Client):
            _login_billing_approver(client, "appr_cards")
            content = client.get(reverse("hub_admin_tools")).content.decode()
            assert _PAYMENTS_HREF in content
            assert _REPORTS_HREF in content

        def it_sees_none_of_the_admin_only_cards(client: Client):
            _login_billing_approver(client, "appr_noadmin")
            content = client.get(reverse("hub_admin_tools")).content.decode()
            assert _MEMBERS_HREF not in content
            assert _SETTINGS_HREF not in content

    def describe_a_guild_lead_without_the_capability():
        def it_reaches_the_page_and_sees_only_its_guild_s_scoped_billing_cards(client: Client):
            # #1294: the guild section's Payments and Reports open the same pages, scoped; the Admin cards stay away.
            _login_guild_lead(client, "lead_nocards")
            response = client.get(reverse("hub_admin_tools"))
            assert response.status_code == 200
            content = response.content.decode()
            assert f'href="{_PAYMENTS_HREF}">' not in content
            assert f'href="{_REPORTS_HREF}">' not in content
            assert f'href="{_PAYMENTS_HREF}?tab=payments" data-tool-scoped-payments' in content
            assert f'href="{_REPORTS_HREF}" data-tool-scoped-reports' in content

    def describe_a_fog_admin():
        def it_sees_both_billing_cards(client: Client):
            _login_superuser(client, "admin_cards")
            content = client.get(reverse("hub_admin_tools")).content.decode()
            assert _PAYMENTS_HREF in content
            assert _REPORTS_HREF in content

        def it_is_bounced_home_when_previewing_as_a_plain_member(client: Client):
            _login_superuser(client, "admin_preview")
            session = client.session
            session["view_as_role"] = "member"
            session.save()
            response = client.get(reverse("hub_admin_tools"))
            assert response.status_code == 302
            assert response.url == reverse("hub_home")

    def describe_card_access_parity():
        """A billing card shows iff its target view admits the viewer (billing_admin_access_required)."""

        def it_holds_for_an_approver(client: Client):
            _login_billing_approver(client, "parity_appr")
            has_card = _PAYMENTS_HREF in client.get(reverse("hub_admin_tools")).content.decode()
            admitted = client.get(_PAYMENTS_HREF).status_code == 200
            assert has_card is True
            assert has_card == admitted

        def it_holds_for_a_guild_lead(client: Client):
            # #1294: a lead's card is the guild section's scoped one, and the page admits them.
            _login_guild_lead(client, "parity_lead")
            has_card = _PAYMENTS_HREF in client.get(reverse("hub_admin_tools")).content.decode()
            admitted = client.get(_PAYMENTS_HREF).status_code == 200
            assert has_card is True
            assert has_card == admitted

        def it_holds_for_a_fog_admin(client: Client):
            _login_superuser(client, "parity_admin")
            has_card = _PAYMENTS_HREF in client.get(reverse("hub_admin_tools")).content.decode()
            admitted = client.get(_PAYMENTS_HREF).status_code == 200
            assert has_card is True
            assert has_card == admitted


def describe_admin_tools_manage_events_card():
    """Manage Events (#831) is a card for admins and Calendar Administrators, gated by
    ``has_events_admin_access`` like the billing cards; holding only that capability opens
    Admin Tools at all (the ``_can_use_admin_tools`` arm the Billing Administrator already had)."""

    _EVENTS_HREF = reverse("hub_admin_events")

    def describe_a_calendar_administrator_only_member():
        def it_sees_the_admin_tools_sidebar_entry(client: Client):
            _login_events_approver(client, "cal_sidebar")
            body = client.get(reverse("hub_member_directory")).content.decode()
            assert reverse("hub_admin_tools") in body

        def it_sees_the_manage_events_card_and_may_open_it(client: Client):
            _login_events_approver(client, "cal_card")
            content = client.get(reverse("hub_admin_tools")).content.decode()
            assert "data-tool-manage-events" in content
            assert _EVENTS_HREF in content
            assert client.get(_EVENTS_HREF).status_code == 200

        def it_sees_none_of_the_admin_only_cards(client: Client):
            _login_events_approver(client, "cal_noadmin")
            content = client.get(reverse("hub_admin_tools")).content.decode()
            assert _MEMBERS_HREF not in content
            assert _PAYMENTS_HREF not in content

    def describe_card_access_parity():
        def it_holds_for_a_guild_lead(client: Client):
            _login_guild_lead(client, "events_parity_lead")
            has_card = "data-tool-manage-events" in client.get(reverse("hub_admin_tools")).content.decode()
            admitted = client.get(_EVENTS_HREF).status_code == 200
            assert has_card is False
            assert has_card == admitted

        def it_holds_for_a_fog_admin(client: Client):
            _login_superuser(client, "events_parity_admin")
            has_card = "data-tool-manage-events" in client.get(reverse("hub_admin_tools")).content.decode()
            admitted = client.get(_EVENTS_HREF).status_code == 200
            assert has_card is True
            assert has_card == admitted


def describe_admin_tools_holiday_bizarre_card():
    """Admins reach the Holiday Bizarre HQ from Admin Tools, as well as from the sidebar."""

    _HQ_HREF = reverse("hq:index")

    def it_shows_to_an_admin(client: Client):
        _login_superuser(client, "hq_card_admin")
        grid = _tools_grid(client)
        assert "data-tool-holiday-bizarre" in grid
        assert f'href="{_HQ_HREF}"' in grid

    def it_is_absent_for_a_calendar_administrator(client: Client):
        _login_events_approver(client, "hq_card_cal")
        assert "data-tool-holiday-bizarre" not in _tools_grid(client)


def describe_admin_tools_polls_card():
    """#925 AC 1: admins open Admin Tools > Polls from a card in the Tools section."""

    _POLLS_HREF = reverse("polls:admin")

    def it_shows_to_an_admin(client: Client):
        _login_superuser(client, "polls_card_admin")
        grid = _tools_grid(client)
        assert "data-tool-polls" in grid
        assert f'href="{_POLLS_HREF}"' in grid

    def it_is_absent_for_a_guild_lead(client: Client):
        _login_guild_lead(client, "polls_card_lead")
        assert "data-tool-polls" not in _tools_grid(client)


def _tools_grid(client: Client) -> str:
    """Just the tool card sections from /manage/tools/, not the whole page.

    Scoped on purpose. The rendered changelog rides in every hub page's context, and a
    v1.3.0 entry still says Admin Tools links each role's Quickstart guide — true when it
    shipped, and not something to rewrite. A negative assertion against the whole body
    would be reading release history instead of the cards.
    """
    body = client.get(reverse("hub_admin_tools")).content.decode()
    grid = re.search(r'<div class="pl-tools-sections".*?\n</div>', body, re.S)
    assert grid is not None, "the tools sections did not render"
    return grid.group(0)


def _card_titles(client: Client) -> list[str]:
    """The tool card titles on /manage/tools/, in the order the page renders them."""
    return re.findall(r'pl-tool-card__title">([^<]+)<', _tools_grid(client))


def _sections(client: Client) -> list[tuple[str, str, list[str]]]:
    """Each rendered section as (key, heading, card titles in order), in page order."""
    found = re.findall(
        r'<section class="pl-tools-section" data-tools-section="(\w+)".*?>([^<]+)</h2>(.*?)</section>',
        _tools_grid(client),
        re.S,
    )
    return [(key, heading, re.findall(r'pl-tool-card__title">([^<]+)<', cards)) for key, heading, cards in found]


_ADMIN_SECTION = (
    "admin",
    "Admin",
    [
        "All Classes",
        "Cubbies",
        "Events",
        "Growth",
        "Holiday Bizarre HQ",
        "Members",
        "Orientations",
        "Payments",
        "Polls",
        "Reports",
        "Slideshow",
        "Spotlight",
        "Usage",
    ],
)
_WEBMASTER_SECTION = (
    "webmaster",
    "Webmaster",
    [
        "Activity Log",
        "Automations",
        "CMS",
        "Features",
        "Feedback Inbox",
        "Guilds",
        "Leadership Directory",
        "Locations",
        "Member Agreement",
        "Notification Settings",
        "Push Notification Test",
        "Site Settings",
    ],
)
_GUILD_CARDS = [
    "Cubbies",
    "Email Members",
    "Events",
    "Guild Settings",
    "Mailing Lists",
    "Orientation Bookings",
    "Orientations",
    "Payments",
    "Reports",
    "Reservations",
]
_CLASSES_SECTION = ("classes", "Classes", ["Email Members", "My Classes", "Registrations"])


def describe_admin_tools_sections():
    """#1291 (Felix): Admin, Webmaster, the viewer's guild and Classes, in that order, each section's cards
    alphabetical by title (#1286); a viewer sees every section they qualify for, and a section with no card
    they may open is not rendered."""

    def it_shows_a_webmaster_admin_the_admin_and_webmaster_sections(client: Client):
        _login_webmaster(client, "sections_webmaster")
        assert _sections(client) == [_ADMIN_SECTION, _WEBMASTER_SECTION]

    def it_shows_an_admin_without_webmaster_the_admin_section_alone(client: Client):
        _login_superuser(client, "sections_admin")
        assert _sections(client) == [_ADMIN_SECTION]

    def it_hides_the_webmaster_tiles_from_a_webmaster_previewing_as_a_member_who_leads_a_guild(client: Client):
        user = _login_webmaster(client, "sections_webmaster_preview")
        GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
        session = client.session
        session["view_as_role"] = "member"
        session.save()
        assert [key for key, _, _ in _sections(client)] == ["guild"]

    def it_puts_every_card_in_exactly_one_section(client: Client):
        _login_webmaster(client, "sections_once")
        titles = _card_titles(client)
        assert len(titles) == len(set(titles)) == 25

    def it_never_offers_instructor_inquiries(client: Client):
        _login_webmaster(client, "sections_no_inquiries")
        assert reverse("classes:admin_instructor_inquiries") not in _tools_grid(client)

    def it_keeps_the_help_key_on_the_wrapper_the_admin_tour_targets(client: Client):
        _login_superuser(client, "sections_help_key")
        assert 'class="pl-tools-sections" data-help-key="admin.tools-grid"' in _tools_grid(client)

    def it_gives_a_guild_lead_their_guild_alone(client: Client):
        user = _login_guild_lead(client, "sections_lead")
        guild_name = user.member.led_guilds.get().name  # type: ignore[attr-defined]
        assert _sections(client) == [("guild", guild_name, _GUILD_CARDS)]

    def it_gives_an_instructor_the_classes_section_alone(client: Client):
        _login_instructor(client, "sections_instructor")
        assert _sections(client) == [_CLASSES_SECTION]

    def it_gives_a_guild_lead_who_teaches_their_guild_then_classes(client: Client):
        user = _login_guild_lead(client, "sections_lead_teacher")
        _grant_teaching(user)
        guild_name = user.member.led_guilds.get().name  # type: ignore[attr-defined]
        assert _sections(client) == [("guild", guild_name, _GUILD_CARDS), _CLASSES_SECTION]

    def it_gives_a_revoked_instructor_email_members_without_the_portal_tiles(client: Client):
        # Revoking teaching keeps the profile slug, which still reaches the composer, but the portal 302s away.
        user = _login_instructor(client, "sections_revoked")
        Member.objects.filter(user=user).update(instructor_oriented_at=None)
        assert _sections(client) == [("classes", "Classes", ["Email Members"])]

    def it_gives_a_guild_lead_named_on_a_class_without_the_grant_no_portal_tiles(client: Client):
        user = _login_guild_lead(client, "sections_lead_named")
        Member.objects.filter(user=user).update(status=Member.Status.ACTIVE)
        ClassOfferingFactory(
            category=CategoryFactory(guild=user.member.led_guilds.get()),  # type: ignore[attr-defined]
            instructor=user.member,  # type: ignore[attr-defined]
        )
        grid = _tools_grid(client)
        assert "data-tool-my-classes" not in grid
        assert "data-tool-teach-registrations" not in grid

    def it_gives_an_invited_member_with_the_grant_email_members_without_the_portal_tiles(client: Client):
        # The portal admits only ACTIVE members; an invited one still reaches this page as an instructor.
        user = _login_instructor(client, "sections_invited")
        Member.objects.filter(user=user).update(status=Member.Status.INVITED)
        assert _sections(client) == [("classes", "Classes", ["Email Members"])]

    def it_links_the_classes_cards_to_the_teaching_pages(client: Client):
        _login_instructor(client, "sections_instructor_links")
        grid = _tools_grid(client)
        assert f'href="{reverse("classes:teach_dashboard")}" data-tool-my-classes' in grid
        assert f'href="{reverse("classes:teach_registrations")}" data-tool-teach-registrations' in grid

    def it_gives_a_billing_approver_only_payments_and_reports(client: Client):
        _login_billing_approver(client, "sections_biller")
        assert _sections(client) == [("admin", "Admin", ["Payments", "Reports"])]

    def it_gives_a_guild_lead_who_guides_no_tours_card(client: Client):
        # #1278: Tours lives in the sidebar now, never on this page.
        user = _login_guild_lead(client, "sections_lead_guide")
        user.member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)  # type: ignore[attr-defined]
        assert _TOURS_HREF not in _tools_grid(client)

    def it_gives_a_calendar_administrator_only_events(client: Client):
        _login_events_approver(client, "sections_calendar")
        assert _sections(client) == [("admin", "Admin", ["Events"])]

    def describe_admin_tool_sections():
        """The helper the view uses: each section's shown cards, alphabetical by title."""

        def _flags(on: set[str]) -> dict[str, bool]:
            return {card.flag: card.flag in on for _, cards in ADMIN_TOOL_SECTIONS for card in cards}

        def _titles(sections: dict[str, list[ToolCard]]) -> dict[str, list[str]]:
            return {key: [card.title for card in cards] for key, cards in sections.items()}

        def it_keeps_only_the_cards_that_show():
            assert _titles(admin_tool_sections(_flags({"tool_usage", "tool_growth"}))) == {
                "admin": ["Growth", "Usage"],
                "webmaster": [],
                "guild": [],
                "classes": [],
            }

        def it_leaves_every_section_empty_when_no_card_shows():
            assert admin_tool_sections(_flags(set())) == dict.fromkeys(("admin", "webmaster", "guild", "classes"), [])

        def it_sorts_ignoring_case(monkeypatch):
            table = (("tools", (ToolCard("b", "beta", "tool_b"), ToolCard("a", "Alpha", "tool_a"))),)
            monkeypatch.setattr("hub.views.ADMIN_TOOL_SECTIONS", table)
            assert _titles(admin_tool_sections({"tool_a": True, "tool_b": True})) == {"tools": ["Alpha", "beta"]}

        def it_refuses_flags_missing_a_card():
            flags = _flags(set())
            del flags["tool_growth"]
            with pytest.raises(KeyError):
                admin_tool_sections(flags)

        def it_names_every_flag_the_view_hands_the_template(client: Client):
            # A card added to the view but to no section would never render under a heading.
            _login_webmaster(client, "sections_flags")
            response = client.get(reverse("hub_admin_tools"))
            context_flags = {
                key for key in response.context.keys() if key.startswith("tool_") and key != "tool_sections"
            }
            section_flags = {card.flag for _, cards in ADMIN_TOOL_SECTIONS for card in cards}
            assert section_flags == context_flags


def describe_admin_tools_cards():
    """Per card gates, unchanged by the sections, and the two Quickstart guide cards stay gone."""

    def describe_the_cards_split_from_site_settings():
        """#922: Member Agreement, Manage Features, Manage CMS and Automations, each its own card."""

        _CARDS = {
            "data-tool-member-agreement": "hub_admin_member_agreement",
            "data-tool-features": "hub_admin_features",
            "data-tool-cms": "hub_admin_cms",
            "data-tool-automations": "hub_admin_automations",
        }

        def it_links_each_card_to_its_page(client: Client):
            _login_webmaster(client, "split_cards_admin")
            grid = _tools_grid(client)
            for marker, url_name in _CARDS.items():
                assert f'href="{reverse(url_name)}" {marker}' in grid

        def it_shows_none_of_them_to_a_guild_lead(client: Client):
            _login_guild_lead(client, "split_cards_lead")
            grid = _tools_grid(client)
            assert not [marker for marker in _CARDS if marker in grid]

        def it_describes_what_site_settings_still_holds(client: Client):
            _login_webmaster(client, "split_cards_copy")
            assert "brand, sign in and registration, Calendar feeds, emails and the Discord connection" in _tools_grid(
                client
            )

    def describe_the_slideshow_tile():
        def it_shows_for_a_fog_admin(client: Client):
            _login_superuser(client, "slideshow_admin")
            assert reverse("hub_admin_slideshow") in _tools_grid(client)

        def it_does_not_show_for_a_guild_lead(client: Client):
            _login_guild_lead(client, "slideshow_lead")
            assert reverse("hub_admin_slideshow") not in _tools_grid(client)

    def describe_the_leadership_directory_tile():
        def it_shows_for_a_webmaster(client: Client):
            _login_webmaster(client, "leadership_admin")
            assert reverse("hub_admin_leadership") in _tools_grid(client)

        def it_does_not_show_for_an_admin_without_webmaster(client: Client):
            _login_superuser(client, "leadership_plain_admin")
            assert reverse("hub_admin_leadership") not in _tools_grid(client)

        def it_does_not_show_for_a_guild_lead(client: Client):
            _login_guild_lead(client, "leadership_lead")
            assert reverse("hub_admin_leadership") not in _tools_grid(client)

    def describe_the_quickstart_guide_cards():
        def it_does_not_show_them_to_a_fog_admin(client: Client):
            _login_superuser(client, "qs_admin")
            grid = _tools_grid(client)
            assert "Quickstart" not in grid
            assert "/help/teaching/instructor-quickstart/" not in grid
            assert "/help/running-a-guild/guild-lead-quickstart/" not in grid

        def it_does_not_show_them_to_a_guild_lead(client: Client):
            # The guild-lead card was gated on can_orient, which a guild lead has.
            _login_guild_lead(client, "qs_lead")
            grid = _tools_grid(client)
            assert "Quickstart" not in grid
            assert "/help/running-a-guild/guild-lead-quickstart/" not in grid

    def it_leaves_the_guides_reachable_in_the_help_center(client: Client):
        # Removing the tiles must not unpublish the articles behind them.
        from membership.help_content import ARTICLES

        slugs = {article["slug"] for article in ARTICLES}
        assert "instructor-quickstart" in slugs
        assert "guild-lead-quickstart" in slugs


def describe_admin_tools_usage_card():
    """#1078: the Usage card shows exactly when its page admits the viewer."""

    _USAGE_HREF = reverse("hub_admin_usage")

    def it_shows_an_admin_the_card_in_reports(client: Client):
        _login_superuser(client, "usage_card_admin")
        content = client.get(reverse("hub_admin_tools")).content.decode()
        assert "data-tool-usage" in content
        assert client.get(_USAGE_HREF).status_code == 200

    def it_hides_the_card_from_a_billing_approver_the_page_refuses(client: Client):
        _login_billing_approver(client, "usage_card_biller")
        assert "data-tool-usage" not in client.get(reverse("hub_admin_tools")).content.decode()
        assert client.get(_USAGE_HREF).status_code == 403
