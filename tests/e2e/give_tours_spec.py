"""End-to-end: a member who gives no tours becomes a Tour Guide from the sidebar (#1278).

The member presses Tours in the sidebar and lands on Give Tours, not a 403. Start the Tutorial opens
the five steps in the card at the foot of the page; Become a Tour Guide is nowhere until the last
step. Next walks Setting Up, When Someone Books, A Tour Day, The Lobby Kiosk and Getting Paid; Back
goes back one. Become a Tour Guide lands on My Hours with the toast and the Get Set Up card, Learn How
a Tour Day Goes already done, and the member now holds Tour Guide, granted by themself. At a phone's
width nothing scrolls sideways.

``CAPTURE_1278_DIR=<dir>`` also saves, dark and light, at desktop (1280 by 800) and phone (390) widths:
the sidebar with Tours, Give Tours full length, each tutorial step (the card), and My Hours with the
toast. ``GUIDANCE_1278_DIR=<dir>`` takes the fragment's three guidance shots to the standard frame
(``tests/e2e/guidance.py``). Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse

from membership.models import AdminCapability, Member, PermissionSetup
from tests.membership.factories import MembershipPlanFactory
from tours import setup as tour_setup
from tests.e2e.guidance import shoot
from tours.views import BECAME_GUIDE_MESSAGE

MEMBER_EMAIL = "give-tours-wren@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_1278_DIR")
GUIDANCE_DIR = os.environ.get("GUIDANCE_1278_DIR")
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
STEPS = ("Setting Up", "When Someone Books", "A Tour Day", "The Lobby Kiosk", "Getting Paid")


def _theme(page, theme: str) -> None:
    page.evaluate(
        "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
        theme,
    )


def _set_drawer(page, *, is_open: bool) -> None:
    """Open or close the hub sidebar (the body's Alpine ``sidebarOpen``) and let it settle."""
    page.evaluate("(v) => { Alpine.$data(document.body).sidebarOpen = v; }", is_open)
    page.wait_for_timeout(300)


def _capture(page, name: str, *, target: str | None = None, full_page: bool = False, drawer: bool = False) -> None:
    """Save dark and light pictures at desktop and phone widths, when CAPTURE_1278_DIR is set.

    ``target`` shoots that element alone (a tutorial step's card); ``full_page`` the whole page;
    otherwise the viewport. ``drawer`` keeps the sidebar open on the phone too.
    """
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        _set_drawer(page, is_open=size is DESKTOP or drawer)
        for theme in ("dark", "light"):
            _theme(page, theme)
            page.wait_for_timeout(500)
            path = str(Path(CAPTURE_DIR) / f"1278-{name}-{size_name}-{theme}.png")
            page.mouse.move(0, 0)  # no hover tint on the button just pressed
            if target:
                # A card taller than the phone is stitched from several scrolls; the sticky top bar would paint over it.
                page.evaluate(
                    "() => document.querySelectorAll('.pl-topbar').forEach((b) => { b.style.position = 'static'; })"
                )
                page.locator(target).scroll_into_view_if_needed()
                page.locator(target).screenshot(path=path)
                page.evaluate(
                    "() => document.querySelectorAll('.pl-topbar').forEach((b) => { b.style.position = ''; })"
                )
            else:
                page.screenshot(path=path, full_page=full_page)
    _theme(page, "dark")
    page.set_viewport_size(DESKTOP)
    _set_drawer(page, is_open=True)


def _capture_toast(page) -> None:
    """My Hours with the toast, each frame: the toast lasts 4.5 seconds, so each frame raises the same one again."""
    if not CAPTURE_DIR:
        return
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        _set_drawer(page, is_open=size is DESKTOP)
        for theme in ("dark", "light"):
            _theme(page, theme)
            page.evaluate(
                "(m) => window.dispatchEvent(new CustomEvent('show-toast', {detail: {message: m, type: 'success'}}))",
                BECAME_GUIDE_MESSAGE,
            )
            page.wait_for_timeout(500)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"1278-my-hours-toast-{size_name}-{theme}.png"))
            page.evaluate("() => document.querySelectorAll('.plt-close').forEach((b) => b.click())")
            page.wait_for_timeout(400)
    _theme(page, "dark")
    page.set_viewport_size(DESKTOP)


def _guidance_shot(page, step: int, target: str) -> None:
    """One of the fragment's guidance shots (changelog.d/1278-give-tours.toml), when GUIDANCE_1278_DIR is set."""
    if not GUIDANCE_DIR:
        return
    page.locator(f'[data-guidance-target="{target}"]').first.scroll_into_view_if_needed()
    page.mouse.move(0, 0)
    shoot(page, Path(GUIDANCE_DIR) / f"step-{step}.png", target=target)
    page.evaluate("() => document.querySelectorAll('style[data-guidance-ring]').forEach((s) => s.remove())")


def _make_member() -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=MEMBER_EMAIL, email=MEMBER_EMAIL)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Wren Halloway"
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _no_sideways_scroll(page) -> None:
    page.set_viewport_size(PHONE)
    _set_drawer(page, is_open=False)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size(DESKTOP)
    _set_drawer(page, is_open=True)


def describe_give_tours():
    def it_walks_a_member_from_the_sidebar_through_the_tutorial_to_my_hours(live_server, page, login_via_code):
        member = _make_member()
        login_via_code(MEMBER_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('hub_home')}")

        tours_entry = page.locator('.hub-sidebar__nav a[data-nav="tours"]').first
        tours_entry.wait_for()
        _capture(page, "sidebar", drawer=True)
        _guidance_shot(page, 1, "tours.sidebar-entry")
        with page.expect_navigation():
            tours_entry.click()
        page.wait_for_url(f"**{reverse('tours:give')}")
        page.get_by_role("heading", name="Give Tours", level=1).wait_for()
        assert page.locator('.hub-sidebar__nav a[data-nav="tours"].active').count() >= 1
        tutorial = page.locator("[data-give-tutorial]")
        assert not tutorial.is_visible()
        # In the page, hidden: get_by_role skips hidden elements, so look it up by its form.
        assert page.locator("[data-give-become] button").count() == 1
        assert not page.locator("[data-give-become] button").is_visible()
        _guidance_shot(page, 2, "tours.start-tutorial")
        _capture(page, "page", full_page=True)
        _no_sideways_scroll(page)

        page.locator("[data-give-start]").first.click()
        tutorial.wait_for()
        for number, title in enumerate(STEPS, start=1):
            step = page.locator(f'[data-give-step="{number}"]')
            step.wait_for()
            assert step.get_by_role("heading", name=title).is_visible()
            assert page.locator(f'.pl-wizard-nav__step[aria-current="step"]:has-text("{title}")').count() == 1
            assert page.locator("[data-give-become] button").is_visible() is (number == len(STEPS))
            assert page.locator("[data-give-back]").is_visible() is (number > 1)
            _capture(page, f"step-{number}", target="[data-give-cta]")
            if number == 2:
                page.locator("[data-give-back]").click()
                page.locator('[data-give-step="1"]').wait_for()
                page.locator("[data-give-next]").click()
                step.wait_for()
            if number < len(STEPS):
                page.locator("[data-give-next]").click()
        assert not page.locator("[data-give-next]").is_visible()
        _guidance_shot(page, 3, "tours.become-guide")
        _no_sideways_scroll(page)

        page.get_by_role("button", name="Become a Tour Guide").click()
        page.wait_for_url(f"**{reverse('tours:hours')}")
        page.locator(".plt-msg", has_text=BECAME_GUIDE_MESSAGE).wait_for()
        page.locator('[data-permission-setup="tour_guide"]').first.wait_for()
        assert page.locator('nav.pl-tabs [data-tours-tab="hours"][aria-current="page"]').count() == 1
        _capture_toast(page)

        grant = AdminCapability.objects.get(member=member, capability=AdminCapability.Capability.TOUR_GUIDE)
        assert grant.granted_by_id == member.user_id
        row = PermissionSetup.objects.get(member=member, permission=AdminCapability.Capability.TOUR_GUIDE)
        assert tour_setup.LEARN in row.read_steps
