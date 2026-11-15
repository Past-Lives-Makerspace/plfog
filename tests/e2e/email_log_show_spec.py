"""A guidance ring on the Email Log's Recipient opens the Email Log tab first, in a real browser (#1112).

Site Activity opens on the Activity Feed, and Recipient sits in the hidden Email Log pane. Each
pane carries ``data-composer-step``, so the tour engine's reveal hook asks the page for that
pane before it measures the control. Arriving with ``?show=activity.email-search`` and no tab
must end with the Email Log tab selected and Recipient ringed. Run with ``pytest -m e2e``.

A loaded machine can go seconds after a load without painting a frame (#1122): Alpine still runs,
but anything waiting on an animation frame stalls, Alpine's own tab switch included. The arrival
ring must not wait on one, and Show me is only pressed on a page that has painted.
"""

from __future__ import annotations

import re

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
        page.evaluate(
            "window.plTour.show({selector: '[data-guidance-target=\"activity.email-search\"]', title: 't', body: 'b'})"
        )

        recipient = page.locator(RECIPIENT)
        expect(recipient).to_be_visible()
        expect(recipient).to_have_class(re.compile(r"\bdriver-active-element\b"))
        expect(page.get_by_role("tab", name="Email Log")).to_have_attribute("aria-selected", "true")
