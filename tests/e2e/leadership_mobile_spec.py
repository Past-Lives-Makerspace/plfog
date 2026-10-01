"""End-to-end: the Leadership Directory fits a phone and its contact rows are tap targets.

Cards sit two across at 393px (the Plate treatment picked on #464), so a long, space-less
address inside a half-width card is the exact token that would push the page wider than the
screen. This drives the real browser at a phone viewport with such an address on a team card
and on a guild card, proving the page never scrolls sideways and that every email and Discord
row is at least 44px tall and inside the viewport. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from django.urls import reverse

from tests.membership.factories import (
    GuildFactory,
    LeadershipListingFactory,
    LeadershipRoleFactory,
    MemberFactory,
    MembershipPlanFactory,
)

PHONE = {"width": 393, "height": 852}
NO_H_SCROLL = "() => document.documentElement.scrollWidth === document.documentElement.clientWidth"
MEMBER_EMAIL = "leadership-mobile-member@example.com"

# No whitespace to wrap on: only overflow-wrap inside the row keeps these within a half-width card.
LONG_ROLE_EMAIL = "membershipandinternaloperationsdirector@unbreakabledomainexample.com"
LONG_GUILD_EMAIL = "fiberartsguildcontactaddress@unbreakabledomainexample.com"


def _seed_world() -> None:
    """A plan (so login auto-provisions the member), one team card and one guild card.

    Each card carries a long address and a Discord handle, so the page renders four contact
    rows: two links (the addresses), one profile link and one plain-text handle.
    """
    MembershipPlanFactory()
    listing = LeadershipListingFactory(
        member=MemberFactory(
            full_legal_name="Wilhelmina Aldous-Featherington",
            discord_handle="@wilhelmina_af",
            discord_user_id="4242",
        )
    )
    LeadershipRoleFactory(
        listing=listing, title="Membership Director and Internal Operations Director", email=LONG_ROLE_EMAIL
    )
    guild = GuildFactory(name="Fiber Arts Guild", contact_email=LONG_GUILD_EMAIL)
    guild.guild_lead = MemberFactory(full_legal_name="Bartholomew Cavendish", discord_handle="bartholomew_c")
    guild.save(update_fields=["guild_lead"])


def _open(live_server, page, login_via_code) -> None:
    _seed_world()
    page.set_viewport_size(PHONE)
    login_via_code(MEMBER_EMAIL)
    page.goto(f"{live_server.url}{reverse('hub_leadership_directory')}")
    page.locator(".pl-leader-card--guild").wait_for()


def describe_leadership_directory_mobile():
    def it_never_scrolls_sideways_at_phone_width(live_server, page, login_via_code):
        _open(live_server, page, login_via_code)
        assert page.evaluate(NO_H_SCROLL), f"the directory scrolls sideways at {PHONE['width']}px"

    def it_keeps_every_contact_row_a_tap_target_inside_the_viewport(live_server, page, login_via_code):
        _open(live_server, page, login_via_code)
        rows = page.locator(".pl-leader-card__row")
        assert rows.count() == 4
        for index in range(rows.count()):
            box = rows.nth(index).bounding_box()
            assert box is not None, f"contact row {index} has no box"
            assert box["height"] >= 44, f"contact row {index} is {box['height']}px tall"
            assert box["x"] >= 0 and box["x"] + box["width"] <= PHONE["width"], (
                f"contact row {index} leaves the viewport"
            )
