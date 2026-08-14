"""End-to-end: the Manage Members roster stays inside a phone viewport.

The admin roster used to force horizontal page scroll on narrow screens: each
labelled cell was a flex row whose value child hit the flex ``min-width: auto``
shrink trap, so an unbreakable email or name (no spaces to wrap on) pushed the
page wider than the screen. The fix stacks each cell as a block with
``overflow-wrap: anywhere`` and keeps the filter bar within the viewport.

These drive the real browser at a 393px viewport with a member whose name and
email are long and space-less (the exact token that reproduced the bug) to prove
the page never scrolls sideways, the filter bar fits, and the long value stays
visible rather than being clipped or hidden. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from tests.membership.factories import MemberFactory

PHONE = {"width": 393, "height": 852}
NO_H_SCROLL = "() => document.documentElement.scrollWidth === document.documentElement.clientWidth"
ADMIN_EMAIL = "manage-members-admin@example.com"

# Long, space-less name and email: no whitespace to wrap on, so the old flex
# layout overflowed the viewport. The value is what the roster must still show.
LONG_NAME = "Wolfeschlegelsteinhausenbergerdorffwelchevoralternwarengewissenhaftschaferswessen"
LONG_EMAIL = "wolfeschlegelsteinhausenbergerdorffwelchevoralternwarengewissenhaft@unbreakabledomainexample.com"


def _seed_long_member() -> str:
    """Create an unlinked member whose name and email are long and unbreakable.

    An unlinked ``MemberFactory`` member (``user=None``) resolves ``primary_email``
    straight from ``_pre_signup_email`` — no allauth EmailAddress setup needed — so
    the roster renders exactly this long token. The SubFactory also creates a plan,
    which the later login uses to auto-provision the admin's own member.
    """
    MemberFactory(full_legal_name=LONG_NAME, _pre_signup_email=LONG_EMAIL)
    return LONG_EMAIL


def _elevate_to_admin(email: str) -> None:
    """Grant the just-signed-in user site-admin so ``@fog_admin_required`` passes.

    ``compute_actual_roles`` grants admin from ``is_superuser``; this mirrors the
    elevation pattern in ``signage_admin_spec``.
    """
    user = get_user_model().objects.get(username=email)
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])


def describe_manage_members_mobile():
    def it_never_lets_the_roster_scroll_sideways(live_server, page, login_via_code):
        _seed_long_member()
        page.set_viewport_size(PHONE)
        login_via_code(ADMIN_EMAIL)
        _elevate_to_admin(ADMIN_EMAIL)

        page.goto(f"{live_server.url}{reverse('hub_admin_members')}")
        assert page.evaluate(NO_H_SCROLL), f"roster scrolls sideways at {PHONE['width']}px"

    def it_keeps_the_filter_bar_within_the_viewport(live_server, page, login_via_code):
        _seed_long_member()
        page.set_viewport_size(PHONE)
        login_via_code(ADMIN_EMAIL)
        _elevate_to_admin(ADMIN_EMAIL)

        page.goto(f"{live_server.url}{reverse('hub_admin_members')}")
        box = page.locator(".members-filters").bounding_box()
        assert box is not None, "filter bar not found on the roster"
        assert box["width"] <= PHONE["width"], f"filter bar is {box['width']}px wide at {PHONE['width']}px"

    def it_keeps_the_long_email_visible(live_server, page, login_via_code):
        long_email = _seed_long_member()
        page.set_viewport_size(PHONE)
        login_via_code(ADMIN_EMAIL)
        _elevate_to_admin(ADMIN_EMAIL)

        page.goto(f"{live_server.url}{reverse('hub_admin_members')}")
        # A future "fix" that clips or hides the value instead of wrapping it fails here.
        expect(page.get_by_text(long_email)).to_be_visible()
