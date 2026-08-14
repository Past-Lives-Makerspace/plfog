"""End-to-end: the mobile top bar folds its actions into the avatar menu.

On a narrow (phone) viewport the topbar no longer runs its theme and settings
controls off the edge of the screen — they collapse into the profile-avatar
dropdown, and nothing lets the page scroll sideways. The notification bell now
stays directly visible in the topbar rather than folding into the menu. These
drive the real browser at a 393px viewport to prove the fold works from its new
home: the page never scrolls horizontally, the bell badge tracks real
notifications, Dark Mode still works from the menu, and Notifications opens from
the topbar bell. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from tests.membership.factories import GuildFactory, MembershipPlanFactory

PHONE = {"width": 393, "height": 852}
DARK = re.compile(r"(^|\s)dark(\s|$)")
NO_H_SCROLL = "() => document.documentElement.scrollWidth === document.documentElement.clientWidth"
MEMBER_EMAIL = "mobile-topbar-member@example.com"


def _seed_member_world() -> None:
    """A plan (so login auto-creates the member) and a guild for the voting page."""
    MembershipPlanFactory()
    GuildFactory(name="Fiber Arts Guild")


def describe_mobile_topbar():
    def it_never_lets_the_page_scroll_sideways(live_server, page, login_via_code):
        _seed_member_world()
        page.set_viewport_size(PHONE)
        login_via_code(MEMBER_EMAIL)

        for name in ("hub_home", "hub_guild_voting"):
            page.goto(f"{live_server.url}{reverse(name)}")
            assert page.evaluate(NO_H_SCROLL), f"{name} scrolls sideways at {PHONE['width']}px"

    def it_shows_the_bell_badge_only_while_a_notification_is_unread(live_server, page, login_via_code):
        _seed_member_world()
        page.set_viewport_size(PHONE)
        login_via_code(MEMBER_EMAIL)
        user = get_user_model().objects.get(username=MEMBER_EMAIL)

        from core.models import Notification

        # Signing in can file a welcome notification, so start from a known-read
        # slate, then add exactly one unread one.
        Notification.objects.filter(user=user).update(read_at=timezone.now())
        Notification.objects.create(
            user=user, trigger="guild.announcement", title="Kiln fired", body="Come collect your pieces."
        )

        page.goto(f"{live_server.url}{reverse('hub_home')}")
        expect(page.locator(".pl-bell__badge")).to_be_visible()

        # Once every notification is read, the badge is gone.
        Notification.objects.filter(user=user).update(read_at=timezone.now())
        page.goto(f"{live_server.url}{reverse('hub_home')}")
        expect(page.locator(".pl-bell__badge")).to_have_count(0)

    def it_folds_theme_into_the_avatar_menu(live_server, page, login_via_code):
        _seed_member_world()
        page.set_viewport_size(PHONE)
        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_home')}")

        html = page.locator("html")
        expect(html).to_have_class(DARK)  # default theme before any toggle

        page.click(".pl-profile__avatar")
        dropdown = page.locator(".pl-profile__dropdown")
        expect(dropdown).to_be_visible()

        # Dark Mode row flips the <html> theme + pl_theme cookie and leaves the menu open.
        dropdown.get_by_text(re.compile(r"Switch to (Light|Dark) Mode")).click()
        expect(html).to_have_attribute("data-theme", "light")
        expect(html).not_to_have_class(DARK)
        theme_cookie = next(c for c in page.context.cookies() if c["name"] == "pl_theme")
        assert theme_cookie["value"] == "light"
        expect(dropdown).to_be_visible()

    def it_opens_notifications_from_the_topbar_bell(live_server, page, login_via_code):
        _seed_member_world()
        page.set_viewport_size(PHONE)
        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_home')}")

        notifications_path = reverse("notification_list")
        page.locator(".pl-bell__btn").click()
        page.wait_for_url(f"**{notifications_path}")
