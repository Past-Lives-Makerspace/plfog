"""Browser flow for Tour Guide's Get Set Up card (#1167).

A newly granted guide opens the Tours page and sees the card with one step open, skips nothing,
watches steps tick as hours and a calendar land, presses Got It on the reading step, marks a
first tour Attended on the Past tab (the toast names the badge), and then finds the card folded,
the Guide tab showing the wiki page, and the badge on the Member Directory card.

With ``CAPTURE_1167_DIR`` set, each moment is saved desktop light (1280 by 800, sidebar open) and
phone dark (390 by 844, drawer closed), viewport only, plus the slimmed grant email.
"""

from __future__ import annotations

import importlib
import os
from datetime import timedelta
from pathlib import Path

from django.apps import apps
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from membership import permission_grants
from membership.models import AdminCapability, Member, WikiPage
from tests.e2e.guidance import shoot
from tests.membership.factories import MembershipPlanFactory
from tests.tours.factories import TourBookingFactory, TourCalendarConnectionFactory, TourHoursFactory
from tours.setup import TOUR_GUIDE_SETUP

GUIDE_EMAIL = "tour-guide-setup-1167@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_1167_DIR")
#: Where the What's New shots go (``docs/GUIDANCE.md``'s standard frame), when set.
GUIDANCE_DIR = os.environ.get("GUIDANCE_1167_DIR")
FRAMES = (("desktop", "light", {"width": 1280, "height": 800}), ("phone", "dark", {"width": 390, "height": 844}))


def _person(email: str, name: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.fog_role = fog_role
    member.save(update_fields=["status", "full_legal_name", "fog_role"])
    member.sync_user_permissions()
    return member


def _capture(page, name: str, scroll_to: str | None = None) -> None:
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for size_name, theme, size in FRAMES:
        page.set_viewport_size(size)
        is_desktop = size_name == "desktop"
        page.evaluate("(v) => { if (window.Alpine) Alpine.$data(document.body).sidebarOpen = v; }", is_desktop)
        page.evaluate(
            "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light')"
            " : document.documentElement.removeAttribute('data-theme')",
            theme,
        )
        page.evaluate("() => document.querySelectorAll('.pl-toast, [data-toast]').forEach((el) => el.remove())")
        if scroll_to:
            page.locator(scroll_to).first.scroll_into_view_if_needed()
        else:
            page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(500)
        page.screenshot(path=str(Path(CAPTURE_DIR) / f"1167-{name}-{size_name}-{theme}.png"))
    page.set_viewport_size(FRAMES[0][2])
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")


def _guidance_shot(page, step: int, target: str | None = None) -> None:
    if GUIDANCE_DIR:
        shoot(page, Path(GUIDANCE_DIR) / f"step-{step}.png", target=target)
        page.evaluate("() => document.documentElement.removeAttribute('data-theme')")


def describe_get_set_up():
    def it_walks_a_new_guide_from_the_first_visit_to_the_badge(live_server, page, login_via_code, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = "client-id"
        settings.GOOGLE_OAUTH_CLIENT_SECRET = "client-secret"
        if WikiPage.guide_page_for(AdminCapability.Capability.TOUR_GUIDE) is None:
            # A transactional live server spec may start after another flushed the seeded page.
            importlib.import_module("membership.migrations.0278_seed_tour_guide_guide").seed(apps, None)
        granter = _person("granter-1167@example.com", "Avery Quillfeather", fog_role=Member.FogRole.ADMIN)
        guide = _person(GUIDE_EMAIL, "Rae Lindqvist")
        guide.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE, granted_by=granter.user)
        login_via_code(GUIDE_EMAIL)
        upcoming = f"{live_server.url}{reverse('tours:upcoming')}"

        page.goto(upcoming)
        card = page.locator('[data-permission-setup="tour_guide"]')
        card.wait_for()
        assert card.locator("[data-setup-open]").get_attribute("data-setup-step") == "calendar"
        assert "Questions? Ask Avery Quillfeather." in card.inner_text()
        _capture(page, "first")
        _guidance_shot(page, 1, target="tours.get-set-up")

        TourCalendarConnectionFactory(guide=guide, google_email="rae@example.org")
        TourHoursFactory(guide=guide)
        page.goto(upcoming)
        card.get_by_text("Visitors Can Book You Now").wait_for()
        assert card.locator("[data-setup-open]").get_attribute("data-setup-step") == "learn"
        _capture(page, "partial")
        _guidance_shot(page, 2, target="tours.get-set-up")

        card.get_by_role("button", name="Got It").click()
        card.locator('[data-setup-open][data-setup-step="first_tour"]').wait_for()

        starts = timezone.now() - timedelta(hours=3)
        TourBookingFactory(guide=guide, starts_at=starts, ends_at=starts + timedelta(hours=1), party_size=3)
        page.goto(f"{live_server.url}{reverse('tours:past')}")
        page.locator('[data-tour-mark="attended"]').first.click()
        page.get_by_text(TOUR_GUIDE_SETUP.celebration).first.wait_for()

        page.goto(upcoming)
        done = page.locator("[data-setup-done]")
        done.wait_for()
        assert "Tour Guide" in done.inner_text()
        _capture(page, "done")
        _guidance_shot(page, 3, target="tours.get-set-up")

        page.locator('nav.pl-tabs [data-tours-tab="guide"]').click()
        page.locator("[data-guide-page]").wait_for()
        _capture(page, "guide")
        page.evaluate(
            "() => { const card = document.querySelector('[data-guide-page]');"
            " window.scrollTo(0, card.getBoundingClientRect().top + window.scrollY - 80); }"
        )
        _guidance_shot(page, 4)

        Member.objects.filter(pk=guide.pk).update(show_in_directory=True)
        page.goto(f"{live_server.url}{reverse('hub_member_directory')}")
        badge = page.locator(".directory-card .pl-leader-badge", has_text="Tour Guide").first
        badge.wait_for()
        _capture(page, "directory", scroll_to=".directory-card:has(.pl-leader-badge)")

        if CAPTURE_DIR:
            context = permission_grants.email_context(guide, AdminCapability.Capability.TOUR_GUIDE, granter, None)
            page.set_content(render_to_string("membership/emails/permission_granted.html", context))
            for size_name, theme, size in FRAMES:
                page.set_viewport_size(size)
                page.wait_for_timeout(200)
                page.screenshot(path=str(Path(CAPTURE_DIR) / f"1167-email-{size_name}-{theme}.png"), full_page=True)
