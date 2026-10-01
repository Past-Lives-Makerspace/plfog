"""End-to-end: the Leadership Directory fits a phone and its contact rows are tap targets.

Cards sit two across at 393px (the Plate treatment picked on #464), so a long, space-less
address inside a half-width card is the exact token that would push the page wider than the
screen. Since #564 the cards sit in tabs, and a strip of long tab names is the other thing
that could: it has to scroll sideways inside itself while the page never does. This drives
the real browser at a phone viewport with such an address on a person card and on a guild
card, opening each tab, proving the page never scrolls sideways and that every email and
Discord row is at least 44px tall and inside the viewport. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from django.urls import reverse

from membership.models import LeadershipTab
from tests.membership.factories import (
    GuildFactory,
    LeadershipListingFactory,
    LeadershipRoleFactory,
    LeadershipTabFactory,
    MemberFactory,
    MembershipPlanFactory,
)

PHONE = {"width": 393, "height": 852}
NO_H_SCROLL = "() => document.documentElement.scrollWidth === document.documentElement.clientWidth"
STRIP_SCROLLS = (
    "() => { const s = document.querySelector('.pl-leadership__tabs'); return s.scrollWidth > s.clientWidth; }"
)
MEMBER_EMAIL = "leadership-mobile-member@example.com"

# No whitespace to wrap on: only overflow-wrap inside the row keeps these within a half-width card.
LONG_ROLE_EMAIL = "membershipandinternaloperationsdirector@unbreakabledomainexample.com"
LONG_GUILD_EMAIL = "fiberartsguildcontactaddress@unbreakabledomainexample.com"


def _seed_world() -> tuple[LeadershipTab, LeadershipTab]:
    """A plan (so login auto-provisions the member), a person card, a guild card, and a long strip.

    Each card carries a long address and a Discord handle, so each of the two tabs renders two
    contact rows: the person's address and profile link, the guild's address and the lead's
    plain-text handle. Three more People tabs with long names make the strip wider than a phone.
    """
    MembershipPlanFactory()
    LeadershipTab.objects.all().delete()
    leadership = LeadershipTabFactory(title="Leadership and Admin Team", sort_order=0)
    listing = LeadershipListingFactory(
        tab=leadership,
        member=MemberFactory(
            full_legal_name="Wilhelmina Aldous-Featherington",
            discord_handle="@wilhelmina_af",
            discord_user_id="4242",
        ),
    )
    LeadershipRoleFactory(
        listing=listing, title="Membership Director and Internal Operations Director", email=LONG_ROLE_EMAIL
    )
    for index, title in enumerate(("Council of Stewards", "Facilities and Safety", "Community Liaisons"), start=1):
        LeadershipListingFactory(tab=LeadershipTabFactory(title=title, sort_order=index))
    guild_leads = LeadershipTabFactory(kind=LeadershipTab.Kind.GUILD_LEADS, title="Guild Leads", sort_order=9)
    guild = GuildFactory(name="Fiber Arts Guild", contact_email=LONG_GUILD_EMAIL)
    guild.guild_lead = MemberFactory(full_legal_name="Bartholomew Cavendish", discord_handle="bartholomew_c")
    guild.save(update_fields=["guild_lead"])
    return leadership, guild_leads


def _open(live_server, page, login_via_code) -> tuple[LeadershipTab, LeadershipTab]:
    tabs = _seed_world()
    page.set_viewport_size(PHONE)
    login_via_code(MEMBER_EMAIL)
    page.goto(f"{live_server.url}{reverse('hub_leadership_directory')}")
    page.locator(f"#leadership-pane-{tabs[0].pk} .pl-leader-card").wait_for()
    return tabs


def _assert_rows_are_tap_targets(page, pane_id: str) -> None:
    rows = page.locator(f"#{pane_id} .pl-leader-card__row")
    assert rows.count() == 2
    for index in range(rows.count()):
        box = rows.nth(index).bounding_box()
        assert box is not None, f"contact row {index} has no box"
        assert box["height"] >= 44, f"contact row {index} is {box['height']}px tall"
        assert box["x"] >= 0 and box["x"] + box["width"] <= PHONE["width"], f"contact row {index} leaves the viewport"


def describe_leadership_directory_mobile():
    def it_scrolls_the_strip_inside_itself_and_never_the_page(live_server, page, login_via_code):
        leadership, guild_leads = _open(live_server, page, login_via_code)
        assert page.evaluate(STRIP_SCROLLS), "five long tab names should overflow a phone's strip"
        assert page.evaluate(NO_H_SCROLL), f"the directory scrolls sideways at {PHONE['width']}px"
        page.locator(f"#leadership-tab-{guild_leads.pk}").click()
        page.locator(f"#leadership-pane-{guild_leads.pk} .pl-leader-card--guild").wait_for()
        assert page.evaluate(NO_H_SCROLL), f"the Guild Leads tab scrolls sideways at {PHONE['width']}px"

    def it_keeps_every_contact_row_a_tap_target_inside_the_viewport(live_server, page, login_via_code):
        leadership, guild_leads = _open(live_server, page, login_via_code)
        _assert_rows_are_tap_targets(page, f"leadership-pane-{leadership.pk}")
        page.locator(f"#leadership-tab-{guild_leads.pk}").click()
        page.locator(f"#leadership-pane-{guild_leads.pk} .pl-leader-card--guild").wait_for()
        _assert_rows_are_tap_targets(page, f"leadership-pane-{guild_leads.pk}")
