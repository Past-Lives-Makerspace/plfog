"""End-to-end: a member becomes an instructor from Host a Class's tutorial (#1295).

The member presses Host a Class in the sidebar and lands on the page, served in place at
/classes/teach/. Start the Tutorial opens the three steps in the card at the foot of the page;
Become an Instructor is nowhere until the last step. Next walks Your Instructor Page, Creating a
Class and How Review Goes; Back goes back one. Become an Instructor lands on the Teaching Overview
with the toast and the Get Set Up to Teach card, the sidebar entry now reads Teaching, and the
member can open the new class form. At a phone's width nothing scrolls sideways.

``CAPTURE_1295_DIR=<dir>`` also saves, dark and light, at desktop (1280 by 800) and phone (390)
widths: Host a Class full length, each tutorial step (the card), and the Teaching Overview with the
toast. ``GUIDANCE_1295_DIR=<dir>`` takes the fragment's three guidance shots to the standard frame
(``tests/e2e/guidance.py``). Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse

from classes.views import BECAME_INSTRUCTOR_MESSAGE
from core.models import SiteActivity
from membership.models import Member
from tests.e2e.guidance import shoot
from tests.membership.factories import MembershipPlanFactory

MEMBER_EMAIL = "host-a-class-orrin@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_1295_DIR")
GUIDANCE_DIR = os.environ.get("GUIDANCE_1295_DIR")
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
STEPS = ("Your Instructor Page", "Creating a Class", "How Review Goes")


def _theme(page, theme: str) -> None:
    page.evaluate(
        "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
        theme,
    )


def _set_drawer(page, *, is_open: bool) -> None:
    """Open or close the hub sidebar (the body's Alpine ``sidebarOpen``) and let it settle."""
    page.evaluate("(v) => { Alpine.$data(document.body).sidebarOpen = v; }", is_open)
    page.wait_for_timeout(300)


def _capture(page, name: str, *, target: str | None = None, full_page: bool = False) -> None:
    """Save dark and light pictures at desktop and phone widths, when CAPTURE_1295_DIR is set.

    ``target`` shoots that element alone (the tutorial's card); ``full_page`` the whole page;
    otherwise the viewport.
    """
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        _set_drawer(page, is_open=size is DESKTOP)
        for theme in ("dark", "light"):
            _theme(page, theme)
            page.wait_for_timeout(500)
            path = str(Path(CAPTURE_DIR) / f"1295-{name}-{size_name}-{theme}.png")
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
    """The Teaching Overview with the toast, each frame: it lasts 4.5 seconds, so each frame raises it again."""
    if not CAPTURE_DIR:
        return
    page.evaluate("() => document.querySelectorAll('.plt-close').forEach((b) => b.click())")  # the redirect's own
    page.wait_for_timeout(400)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        _set_drawer(page, is_open=size is DESKTOP)
        for theme in ("dark", "light"):
            _theme(page, theme)
            page.evaluate(
                "(m) => window.dispatchEvent(new CustomEvent('show-toast', {detail: {message: m, type: 'success'}}))",
                BECAME_INSTRUCTOR_MESSAGE,
            )
            page.wait_for_timeout(500)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"1295-teaching-overview-toast-{size_name}-{theme}.png"))
            page.evaluate("() => document.querySelectorAll('.plt-close').forEach((b) => b.click())")
            page.wait_for_timeout(400)
    _theme(page, "dark")
    page.set_viewport_size(DESKTOP)


def _guidance_shot(page, step: int, target: str | None, *, at: str | None = None) -> None:
    """One of the fragment's guidance shots (changelog.d/1295-instructor-self-serve.toml), when GUIDANCE_1295_DIR is set.

    ``target`` is the control the shot rings; with none, ``at`` is what the shot scrolls to.
    """
    if not GUIDANCE_DIR:
        return
    page.locator(at or f'[data-guidance-target="{target}"]').first.scroll_into_view_if_needed()
    page.mouse.move(0, 0)
    shoot(page, Path(GUIDANCE_DIR) / f"step-{step}.png", target=target)
    page.evaluate("() => document.querySelectorAll('style[data-guidance-ring]').forEach((s) => s.remove())")


def _make_member() -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=MEMBER_EMAIL, email=MEMBER_EMAIL)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Orrin Valecourt"
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _no_sideways_scroll(page) -> None:
    page.set_viewport_size(PHONE)
    _set_drawer(page, is_open=False)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size(DESKTOP)
    _set_drawer(page, is_open=True)


def describe_host_a_class():
    def it_walks_a_member_from_host_a_class_through_the_tutorial_to_the_teaching_overview(
        live_server, page, login_via_code
    ):
        member = _make_member()
        login_via_code(MEMBER_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('hub_home')}")

        entry = page.locator('.hub-sidebar__nav a[data-nav="teach"]').first
        entry.wait_for()
        assert "Host a Class" in entry.inner_text()
        with page.expect_navigation():
            entry.click()
        assert page.url.rstrip("/").endswith("/classes/teach")
        page.locator(".pl-guild-hero__title").first.wait_for()
        tutorial = page.locator("[data-tutorial]")
        assert not tutorial.is_visible()
        # In the page, hidden: get_by_role skips hidden elements, so look it up by its form.
        assert page.locator("[data-tutorial-confirm] button").count() == 1
        assert not page.locator("[data-tutorial-confirm] button").is_visible()
        _guidance_shot(page, 1, "classes.start-tutorial")
        _capture(page, "page", full_page=True)
        _no_sideways_scroll(page)

        page.locator("[data-teach-start]").first.click()
        tutorial.wait_for()
        for number, title in enumerate(STEPS, start=1):
            step = page.locator(f'[data-tutorial-step="{number}"]')
            step.wait_for()
            assert step.get_by_role("heading", name=title).is_visible()
            assert page.locator(f'.pl-wizard-nav__step[aria-current="step"]:has-text("{title}")').count() == 1
            assert page.locator("[data-tutorial-confirm] button").is_visible() is (number == len(STEPS))
            assert page.locator("[data-tutorial-back]").is_visible() is (number > 1)
            _capture(page, f"step-{number}", target="[data-teach-cta]")
            if number == 2:
                _guidance_shot(page, 2, None, at="[data-teach-cta]")
                page.locator("[data-tutorial-back]").click()
                page.locator('[data-tutorial-step="1"]').wait_for()
                page.locator("[data-tutorial-next]").click()
                step.wait_for()
            if number < len(STEPS):
                page.locator("[data-tutorial-next]").click()
        assert not page.locator("[data-tutorial-next]").is_visible()
        _guidance_shot(page, 3, "classes.become-instructor")
        _no_sideways_scroll(page)

        page.get_by_role("button", name="Become an Instructor").click()
        page.wait_for_url(lambda url: url.rstrip("/").endswith("/classes/teach"))
        page.locator(".plt-msg", has_text=BECAME_INSTRUCTOR_MESSAGE).wait_for()
        page.locator('[data-permission-setup="instructor"]').first.wait_for()
        assert "Teaching" in page.locator('.hub-sidebar__nav a[data-nav="teach"]').first.inner_text()
        _capture_toast(page)

        member.refresh_from_db()
        assert member.can_create_classes
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.INSTRUCTOR_SELF_SERVE, actor=member.user).count() == 1
        page.goto(f"{live_server.url}{reverse('classes:teach_class_create')}")
        assert page.url.endswith(reverse("classes:teach_class_create"))
