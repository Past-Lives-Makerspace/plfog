"""End-to-end: on a phone, tab rows become a section picker and a class's tabs a section list (#852).

Below 640px every top level ``.pl-tabs`` row gives way to the picker ``{% tab_picker %}`` draws
beside it, and a class's own row to the list on its Overview or the back row on its other tabs.
Opening and closing the picker, an in-page row's picker following and switching its Alpine pane,
and the picker surviving a boosted arrival only happen in a browser. At 1280px the old rows are
the ones on screen. ``CAPTURE_852_SCREENSHOTS=1`` also saves the PR's pictures under
``mockups/screenshots/``. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, cast

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from membership.models import LeadershipTab, Member
from tests.membership.factories import GuildFactory, LeadershipTabFactory, MembershipPlanFactory

EMAIL = "mobile-tabs@example.com"
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}
CAPTURE = bool(os.environ.get("CAPTURE_852_SCREENSHOTS"))
SHOTS = Path("mockups/screenshots")
PICKER = ".pl-tab-picker"
SUMMARY = ".pl-tab-picker__summary"
MENU = ".pl-tab-picker__menu"
PORTAL_ROW = 'nav.pl-tabs[aria-label="Class management sections"]'
CLASS_ROW = 'nav.pl-tabs[aria-label="This class"]'
SECTIONS = "[data-class-sections]"
BACK = "[data-class-section-back]"
NO_H_SCROLL = "() => document.documentElement.scrollWidth === document.documentElement.clientWidth"
ALPINE_READY = "(sel) => !!(document.querySelector(sel) || {})._x_dataStack"


@pytest.fixture
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    """The PR's pictures are taken at 2x, like the mockups they are set beside."""
    return {**browser_context_args, "device_scale_factor": 2} if CAPTURE else browser_context_args


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the page finish its requests before teardown truncates (see class_composer_draft_spec)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _instructor_class() -> ClassOffering:
    """A live fixed class taught by the signed in instructor, so every per-class tab is offered."""
    MembershipPlanFactory()  # so the user signal provisions the member the instructor factory updates
    user = UserFactory(username=EMAIL, email=EMAIL)
    instructor = cast(Member, InstructorFactory(user=user, full_legal_name="Eve Brightwater", instructor_slug="eve"))
    return cast(
        ClassOffering,
        ClassOfferingFactory(
            instructor=instructor,
            title="Intro to Lost Wax Casting",
            price_cents=8000,
            ready=True,
            status=ClassOffering.Status.PUBLISHED,
        ),
    )


def _admin() -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=EMAIL, email=EMAIL)
    admin = user.member
    admin.fog_role = Member.FogRole.ADMIN
    admin.status = Member.Status.ACTIVE
    admin.save(update_fields=["fog_role", "status"])
    admin.sync_user_permissions()
    return user


def _go(page: Any, live_server: Any, path: str, ready: str = PICKER) -> None:
    page.goto(f"{live_server.url}{path}")
    page.wait_for_function(ALPINE_READY, arg=ready)


def _tall_enough(locator: Any) -> None:
    for index in range(locator.count()):
        box = locator.nth(index).bounding_box()
        assert box is not None and box["height"] >= 44, f"target {index} is {box and box['height']}px tall"


def describe_the_section_picker_on_a_phone():
    def it_opens_the_classes_portal_picker_and_navigates(live_server, page, login_via_code):
        _instructor_class()
        login_via_code(EMAIL)
        page.set_viewport_size(PHONE)
        _go(page, live_server, reverse("classes:teach_dashboard"))

        expect(page.locator(PORTAL_ROW)).to_be_hidden()
        summary = page.locator(SUMMARY)
        expect(summary).to_have_attribute("aria-label", re.compile(r"^Classes: My Classes, section 2 of \d$"))
        expect(page.locator(".pl-tab-picker__count")).to_have_text(re.compile(r"^2 of \d$"))
        summary.click()
        menu = page.locator(MENU)
        expect(menu).to_be_visible()
        expect(menu.locator('[aria-current="page"]')).to_contain_text("My Classes")
        _tall_enough(page.locator(f"{SUMMARY}, {MENU} .pl-tab-picker__item"))
        assert page.evaluate(NO_H_SCROLL)

        page.evaluate("() => { window.plTabsStamp = 852; }")
        menu.get_by_role("link", name="Registrations").click()
        expect(page).to_have_url(re.compile(re.escape(reverse("classes:teach_registrations")) + "$"))
        expect(page.locator(".pl-tab-picker__current")).to_have_text("Registrations")
        assert page.evaluate("() => window.plTabsStamp") == 852, "the picker's link should be a boosted arrival"

        # After the boosted arrival the new page's picker still opens, closes on Escape and on a tap outside.
        page.locator(SUMMARY).click()
        expect(page.locator(MENU)).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator(MENU)).to_be_hidden()
        expect(page.locator(SUMMARY)).to_be_focused()
        page.locator(SUMMARY).click()
        expect(page.locator(MENU)).to_be_visible()
        page.locator("h1").first.click()
        expect(page.locator(MENU)).to_be_hidden()

    def it_switches_a_guild_section_through_its_picker(live_server, page, login_via_code):
        MembershipPlanFactory()
        guild = GuildFactory(name="Woodshop Guild", slug="woodshop-guild")
        login_via_code(EMAIL)
        page.set_viewport_size(PHONE)
        _go(page, live_server, reverse("hub_guild_detail", args=[guild.slug]))

        expect(page.locator("nav.pl-tabs[role=tablist]")).to_be_hidden()
        summary = page.locator(SUMMARY)
        expect(summary).to_have_attribute("aria-label", re.compile(r"^Guild Sections: Overview, section 1 of \d+$"))
        summary.click()
        expect(page.locator(f"{MENU} [aria-current]")).to_have_text("Overview")
        page.locator(MENU).get_by_role("button", name="Guild Calendar").click()

        expect(page.locator(MENU)).to_be_hidden()
        expect(page.locator(".pl-tab-picker__current")).to_have_text("Guild Calendar")
        expect(page.locator(".pl-tab-picker__count")).to_have_text(re.compile(r"^2 of \d+$"))
        expect(summary).to_have_attribute("aria-label", re.compile(r"^Guild Sections: Guild Calendar, section 2 of"))
        expect(page.locator("[x-show=\"section === 'schedule'\"]")).to_be_visible()
        expect(page.locator("[x-show=\"section === 'overview'\"]")).to_be_hidden()
        # The row's own button switched the pane, so the hidden desktop row agrees.
        expect(page.locator("nav.pl-tabs .vote-tab--active")).to_have_text("Guild Calendar")

    def it_reaches_the_eventbrite_tab_from_the_class_overview_list(live_server, page, login_via_code):
        offering = _instructor_class()
        login_via_code(EMAIL)
        page.set_viewport_size(PHONE)
        _go(page, live_server, reverse("classes:teach_class_detail", kwargs={"pk": offering.pk}))

        expect(page.locator(CLASS_ROW)).to_be_hidden()
        expect(page.locator(".pl-tab-picker__current")).to_have_text("My Classes")
        rows = page.locator(f"{SECTIONS} .pl-section-list__row")
        expect(rows.first).to_be_visible()
        _tall_enough(rows)
        eventbrite = page.locator('[data-class-section="eventbrite"]')
        expect(eventbrite).to_contain_text("List this class on Eventbrite")
        eventbrite.click()

        expect(page).to_have_url(
            re.compile(re.escape(reverse("classes:teach_class_eventbrite", kwargs={"pk": offering.pk})))
        )
        back = page.locator(BACK)
        expect(back).to_be_visible()
        expect(back.locator('[aria-current="page"]')).to_have_text(re.compile(r"^Eventbrite · (\d) of \1$"))
        expect(page.locator(SECTIONS)).to_have_count(0)
        link = back.get_by_role("link", name="Class Overview")
        _tall_enough(link)
        link.click()
        expect(page.locator(f"{SECTIONS} .pl-section-list__row").first).to_be_visible()

    def it_keeps_the_leadership_editors_picker_in_the_strips_order_after_a_move(live_server, page, login_via_code):
        _admin()
        LeadershipTab.objects.all().delete()
        first = LeadershipTabFactory(title="Council", sort_order=0)
        LeadershipTabFactory(title="Stewards", sort_order=1)
        login_via_code(EMAIL)
        page.set_viewport_size(PHONE)
        _go(page, live_server, reverse("hub_admin_leadership"), ".pl-leadership-admin")

        items = page.locator(f"{MENU} .pl-tab-picker__item-label")
        page.locator(SUMMARY).click()
        expect(items).to_have_text(["Council", "Stewards"])
        page.keyboard.press("Escape")
        page.locator(f"[data-tab-pane='{first.pk}'] [data-move-tab='right']").click()
        page.locator(SUMMARY).click()
        expect(items).to_have_text(["Stewards", "Council"])
        expect(page.locator(".pl-tab-picker__count")).to_have_text("2 of 2")


def describe_the_desktop_rows():
    def it_shows_the_old_rows_and_no_picker_at_1280(live_server, page, login_via_code):
        offering = _instructor_class()
        guild = GuildFactory(name="Woodshop Guild", slug="woodshop-guild")
        login_via_code(EMAIL)
        page.set_viewport_size(DESKTOP)
        _go(page, live_server, reverse("classes:teach_class_detail", kwargs={"pk": offering.pk}))
        expect(page.locator(PORTAL_ROW)).to_be_visible()
        expect(page.locator(CLASS_ROW)).to_be_visible()
        expect(page.locator(CLASS_ROW).get_by_role("link", name="Eventbrite")).to_be_visible()
        expect(page.locator(PICKER)).to_be_hidden()
        expect(page.locator(SECTIONS)).to_be_hidden()

        _go(page, live_server, reverse("classes:teach_class_eventbrite", kwargs={"pk": offering.pk}))
        expect(page.locator(BACK)).to_be_hidden()

        _go(page, live_server, reverse("hub_guild_detail", args=[guild.slug]))
        expect(page.locator("nav.pl-tabs[role=tablist]")).to_be_visible()
        expect(page.locator(PICKER)).to_be_hidden()


def _theme(page: Any, theme: str) -> None:
    page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
    page.wait_for_timeout(300)  # the theme's colour transitions settle


def _shot(page: Any, name: str) -> None:
    page.screenshot(path=str(SHOTS / f"852-{name}.png"))


@pytest.mark.skipif(not CAPTURE, reason="set CAPTURE_852_SCREENSHOTS=1 to save the PR's pictures")
def describe_the_pr_pictures():
    def it_captures_each_screen_in_both_themes(live_server, page, login_via_code):
        SHOTS.mkdir(parents=True, exist_ok=True)
        offering = _instructor_class()
        guild = GuildFactory(name="Woodshop Guild", slug="woodshop-guild")
        login_via_code(EMAIL)
        overview = reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        eventbrite = reverse("classes:teach_class_eventbrite", kwargs={"pk": offering.pk})

        page.set_viewport_size(DESKTOP)
        _go(page, live_server, overview)
        _theme(page, "light")
        _shot(page, "desktop-class-1280")

        page.set_viewport_size(PHONE)
        for theme in ("light", "dark"):
            _go(page, live_server, overview)
            _theme(page, theme)
            _shot(page, f"portal-picker-closed-{theme}")
            page.locator(SUMMARY).click()
            page.wait_for_timeout(200)
            _shot(page, f"portal-picker-open-{theme}")
            page.keyboard.press("Escape")
            page.locator(SUMMARY).blur()
            page.evaluate(
                "(sel) => window.scrollTo(0, document.querySelector(sel).getBoundingClientRect().top + scrollY - 260)",
                SECTIONS,
            )
            page.wait_for_timeout(200)
            _shot(page, f"class-overview-list-{theme}")

            _go(page, live_server, eventbrite)
            _theme(page, theme)
            _shot(page, f"eventbrite-back-row-{theme}")

            _go(page, live_server, reverse("hub_guild_detail", args=[guild.slug]))
            _theme(page, theme)
            page.locator(SUMMARY).scroll_into_view_if_needed()
            page.evaluate("() => window.scrollBy(0, -140)")
            page.locator(SUMMARY).click()
            page.wait_for_timeout(200)
            _shot(page, f"guild-picker-open-{theme}")
