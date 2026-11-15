"""A guidance ring on the Email Log's Recipient opens the Email Log tab first, in a real browser (#1112).

Site Activity opens on the Activity Feed, and Recipient sits in the hidden Email Log pane. Each
pane carries ``data-composer-step``, so the tour engine's reveal hook asks the page for that
pane before it measures the control. Arriving with ``?show=activity.email-search`` and no tab
must end with the Email Log tab selected and Recipient ringed. Run with ``pytest -m e2e``.

A loaded machine can go seconds after a load without painting a frame (#1122): Alpine still runs,
but anything waiting on an animation frame stalls, Alpine's own tab switch included. The arrival
ring must not wait on one, and Show me is only pressed on a page that has painted. The engine's
4 second cap starts only once Alpine runs, and runs out only after a frame has painted past it;
the slow load cases pass that time on Playwright's clock, never by waiting.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

EMAIL = "email-log-show@example.com"
TITLE = "E2E search the Email Log"
RECIPIENT = '[data-guidance-target="activity.email-search"]'

# Holds every animation frame until the spec lets them go, the way a loaded machine paints nothing
# for a while after a load (#1122). Held ids start high so they never match a real frame's id.
HOLD_FRAMES = """
(() => {
  const paint = window.requestAnimationFrame.bind(window);
  const held = [];
  window.requestAnimationFrame = (callback) => 1000000 + held.push(callback);
  window.plReleaseFrames = () => {
    window.requestAnimationFrame = paint;
    held.splice(0).forEach((callback) => paint(callback));
  };
})();
"""

# Where Playwright's clock starts; the page's timers then run only when the spec moves it on.
CLOCK_START_MS = int(datetime(2026, 1, 5, 17, 0, tzinfo=UTC).timestamp() * 1000)
SHOW_IN_PLACE = (
    "window.plTour.show({selector: '[data-guidance-target=\"activity.email-search\"]', title: 't', body: 'b'})"
)


def _entries() -> list[dict]:
    return [
        {
            "date": timezone.localdate().isoformat(),
            "title": TITLE,
            "changes": ["Type part of an address into Recipient."],
            "slug": "e2e-email-log-search",
            "audiences": ["admins"],
            "feature": "admin.email-log",
            "guidance": "link",
            "target": "activity.email-search",
        },
    ]


def _sign_in_as_admin(login_via_code) -> None:
    MembershipPlanFactory()
    login_via_code(EMAIL)
    user = get_user_model().objects.get(username=EMAIL)
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])
    member = Member.objects.get(user=user)
    member.welcome_dismissed_at = timezone.now()
    member.guided_tours_enabled = False
    member.save(update_fields=["welcome_dismissed_at", "guided_tours_enabled"])


def describe_email_log_show():
    def it_opens_the_email_log_tab_and_rings_recipient_on_arrival(page, live_server, login_via_code, monkeypatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", _entries())
        _sign_in_as_admin(login_via_code)
        page.goto(f"{live_server.url}{reverse('manage_activity')}?show=activity.email-search")

        recipient = page.locator(RECIPIENT)
        expect(recipient).to_be_visible()
        expect(recipient).to_have_class(re.compile(r"\bdriver-active-element\b"))
        expect(page.get_by_role("tab", name="Email Log")).to_have_attribute("aria-selected", "true")

    def it_rings_recipient_on_arrival_before_the_browser_paints_a_frame(page, live_server, login_via_code, monkeypatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", _entries())
        _sign_in_as_admin(login_via_code)
        page.add_init_script(HOLD_FRAMES)
        page.goto(f"{live_server.url}{reverse('manage_activity')}?show=activity.email-search")

        recipient = page.locator(RECIPIENT)
        expect(recipient).to_be_visible()
        expect(recipient).to_have_class(re.compile(r"\bdriver-active-element\b"))
        expect(page.get_by_role("tab", name="Email Log")).to_have_attribute("aria-selected", "true")
        page.evaluate("window.plReleaseFrames()")

    def it_switches_from_the_feed_to_the_email_log_to_ring_in_place(page, live_server, login_via_code, monkeypatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", _entries())
        _sign_in_as_admin(login_via_code)
        page.goto(f"{live_server.url}{reverse('manage_activity')}")
        expect(page.get_by_role("tab", name="Activity Feed")).to_have_attribute("aria-selected", "true")
        # Show me is pressed on a page the member can see, and Alpine switches the pane on a frame.
        page.evaluate("() => new Promise((painted) => requestAnimationFrame(() => painted()))")

        # What Show me calls once the modal closes (guidance.js), with Alpine already running.
        page.evaluate(SHOW_IN_PLACE)

        recipient = page.locator(RECIPIENT)
        expect(recipient).to_be_visible()
        expect(recipient).to_have_class(re.compile(r"\bdriver-active-element\b"))
        expect(page.get_by_role("tab", name="Email Log")).to_have_attribute("aria-selected", "true")

    def describe_on_a_slow_load():
        @pytest.fixture
        def browser_context_args(browser_context_args):
            """Block the service worker: it answers /static/ itself, past ``page.route``."""
            return {**browser_context_args, "service_workers": "block"}

        def it_rings_recipient_when_alpine_arrives_after_the_cap(page, live_server, login_via_code, monkeypatch):
            monkeypatch.setattr("plfog.version.CHANGELOG", _entries())
            _sign_in_as_admin(login_via_code)
            held = []
            page.route("**/static/js/alpine.min.js", lambda route: held.append(route))
            page.clock.install(time=CLOCK_START_MS)
            page.clock.pause_at(CLOCK_START_MS + 1000)
            page.goto(f"{live_server.url}{reverse('manage_activity')}?show=activity.email-search", wait_until="commit")
            # guidance.js strips ?show= as it hands the ring to the engine, which then waits on Alpine.
            expect(page).to_have_url(re.compile(r"/manage/activity/$"))
            assert len(held) == 1

            page.clock.run_for(5000)  # past the 4 second cap, with Alpine still on its way
            held[0].continue_()
            recipient = page.locator(RECIPIENT)
            expect(recipient).to_be_visible()
            page.clock.run_for(1000)

            expect(recipient).to_have_class(re.compile(r"\bdriver-active-element\b"))
            expect(page.get_by_role("tab", name="Email Log")).to_have_attribute("aria-selected", "true")

        def it_rings_in_place_when_the_first_frame_comes_after_the_cap(page, live_server, login_via_code, monkeypatch):
            monkeypatch.setattr("plfog.version.CHANGELOG", _entries())
            _sign_in_as_admin(login_via_code)
            page.clock.install(time=CLOCK_START_MS)
            page.clock.pause_at(CLOCK_START_MS + 1000)
            page.goto(f"{live_server.url}{reverse('manage_activity')}")
            expect(page.get_by_role("tab", name="Activity Feed")).to_have_attribute("aria-selected", "true")

            # Alpine switches the pane on the next frame, and none paints for 4.6 seconds.
            page.evaluate(HOLD_FRAMES)
            page.evaluate(SHOW_IN_PLACE)
            page.clock.run_for(4600)
            page.evaluate("window.plReleaseFrames()")
            page.clock.run_for(100)

            recipient = page.locator(RECIPIENT)
            expect(recipient).to_be_visible()
            expect(recipient).to_have_class(re.compile(r"\bdriver-active-element\b"))
            expect(page.get_by_role("tab", name="Email Log")).to_have_attribute("aria-selected", "true")
