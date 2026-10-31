"""What's New in a real browser (#1007, part 2).

A member presses What's New in Home's header: the body loads into the modal, the four tabs are
there with an entry, a tab switch loads that tab, and after a reload the button's dot is gone,
because opening the modal marked what it listed as seen. The changelog is patched for the live
server (same process), so the spec does not depend on the repo's fragments or the calendar.
Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import Member
from tests.membership.factories import MembershipPlanFactory


def _entries() -> list[dict]:
    today = timezone.localdate().isoformat()
    return [
        {
            "date": today,
            "title": "E2E members entry",
            "changes": ["Something for every member."],
            "slug": "e2e-members",
            "audiences": ["members"],
            "feature": "",
            "guidance": "none",
        },
        {
            "date": today,
            "title": "E2E staff entry",
            "changes": ["Something for guild staff."],
            "slug": "e2e-staff",
            "audiences": ["guild-staff"],
            "feature": "",
            "guidance": "none",
        },
        {
            "date": today,
            "title": "E2E instructors entry",
            "changes": ["Something for instructors."],
            "slug": "e2e-instructors",
            "audiences": ["instructors"],
            "feature": "",
            "guidance": "none",
        },
    ]


def _sign_in(login_via_code, email: str) -> None:
    MembershipPlanFactory()
    login_via_code(email)
    member = Member.objects.get(user=get_user_model().objects.get(username=email))
    member.welcome_dismissed_at = timezone.now()
    member.guided_tours_enabled = False
    member.save(update_fields=["welcome_dismissed_at", "guided_tours_enabled"])


def it_opens_from_home_switches_a_tab_and_clears_the_dot_on_the_next_load(
    page, live_server, login_via_code, monkeypatch
):
    monkeypatch.setattr("plfog.version.CHANGELOG", _entries())
    _sign_in(login_via_code, "whats-new-e2e@example.com")
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{live_server.url}{reverse('hub_home')}")

    button = page.locator("[data-whats-new-button]")
    expect(button).to_be_visible()
    expect(button.locator("[data-whats-new-dot]")).to_have_count(1)

    button.click()
    body = page.locator("#whats-new-body")
    expect(body).to_be_visible()
    expect(body.locator("[data-whats-new-tab]")).to_have_count(4)
    expect(body.locator("[data-whats-new-tab]")).to_have_text(
        ["For Members", "For Instructors", "For Guild Staff", "For Admins"]
    )
    expect(body.locator('[data-whats-new-pane="members"]')).to_be_visible()
    expect(body.locator('[data-whats-new-entry="e2e-members"]')).to_be_visible()
    expect(page.locator("#whats-new-title")).to_have_text("What's New")

    body.locator('[data-whats-new-tab="guild-staff"]').click()
    expect(body.locator('[data-whats-new-pane="guild-staff"]')).to_be_visible()
    expect(body.locator('[data-whats-new-entry="e2e-staff"]')).to_be_visible()
    expect(body.locator('[data-whats-new-tab="guild-staff"]')).to_have_attribute("aria-current", "page")

    page.reload()
    expect(page.locator("[data-whats-new-button]")).to_be_visible()
    expect(page.locator("[data-whats-new-dot]")).to_have_count(0)


def it_switches_a_tab_through_the_phone_picker_without_leaving_home(page, live_server, login_via_code, monkeypatch):
    monkeypatch.setattr("plfog.version.CHANGELOG", _entries())
    _sign_in(login_via_code, "whats-new-phone-e2e@example.com")
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server.url}{reverse('hub_home')}")
    page.locator("[data-whats-new-button]").click()
    body = page.locator("#whats-new-body")
    expect(body.locator('[data-whats-new-pane="members"]')).to_be_visible()
    expect(body.locator("nav.pl-wn-tabs")).to_be_hidden()

    body.locator(".pl-tab-picker__summary").click()
    menu = body.locator(".pl-tab-picker__menu")
    expect(menu.get_by_role("link")).to_have_text(["For Members", "For Instructors", "For Guild Staff", "For Admins"])
    menu.get_by_role("link", name="For Instructors").click()

    expect(body.locator('[data-whats-new-pane="instructors"]')).to_be_visible()
    expect(body.locator('[data-whats-new-entry="e2e-instructors"]')).to_be_visible()
    expect(body.locator(".pl-tab-picker__current")).to_have_text("For Instructors")
    # Swapped in place: still Home, with the modal open, not the stub page a direct link lands on.
    assert page.url.endswith(reverse("hub_home"))
    expect(page.locator("#whats-new-title")).to_have_text("What's New")
