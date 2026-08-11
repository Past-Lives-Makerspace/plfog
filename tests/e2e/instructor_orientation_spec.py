"""End-to-end: the instructor-orientation teaching unlock (Spec D).

Drives the real gate → orientation → acknowledge → unlock loop in a browser:
a locked member hitting the teaching portal is 302'd to the orientation page,
the "Unlock teaching" button only wakes up once the acknowledge toggle is
ticked (the Alpine affordance the unit specs can't prove), and completing lands
on the now-open teach overview with the success message. Run with
``pytest -m e2e``.
"""

from __future__ import annotations

from playwright.sync_api import expect

from tests.membership.factories import MembershipPlanFactory

BANNER = "One quick step before you can teach"


def describe_instructor_orientation_unlock():
    def it_walks_a_locked_member_from_gate_to_unlocked_portal(live_server, page, login_via_code):
        MembershipPlanFactory()  # so login auto-provisions an ACTIVE member
        login_via_code("teach-me@example.com")

        # A locked member's click on any teach entry point lands on the orientation.
        page.goto(f"{live_server.url}/classes/teach/")
        assert page.url.endswith("/classes/teach/orientation/")
        expect(page.get_by_text(BANNER)).to_be_visible()

        # The unlock button is disabled until the acknowledge toggle is ticked.
        unlock = page.get_by_role("button", name="Unlock teaching")
        expect(unlock).to_be_disabled()
        page.locator(".pl-toggle").click()
        expect(unlock).to_be_enabled()
        unlock.click()

        # Completion lands on the teach overview — the portal is open now.
        page.wait_for_url(lambda url: url.rstrip("/").endswith("/classes/teach"))
        expect(page.get_by_text("Teaching unlocked — welcome, instructor.")).to_be_visible()

        # The gate stays open: revisiting the portal no longer redirects.
        page.goto(f"{live_server.url}/classes/teach/")
        assert page.url.rstrip("/").endswith("/classes/teach")
