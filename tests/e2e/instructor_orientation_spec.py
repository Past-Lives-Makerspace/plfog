"""End-to-end: applying to teach, and an admin opening the portal.

Teaching stopped being self-service. This drives the real loop in a browser: a member
who cannot teach lands on the Teach at Past Lives page (not a 403, not a dead end),
opens the Apply modal, sends a note, sees the applied state, and stays out of the
portal until an admin grants Instructor. The Alpine modal open/close and the
server-rendered state flip are exactly what the unit specs cannot prove. Run with
``pytest -m e2e``.
"""

from __future__ import annotations

from playwright.sync_api import expect

from tests.membership.factories import MembershipPlanFactory

HERO = "Teach at Past Lives"
APPLIED_BANNER = "Your Application Is In"
SUCCESS_MESSAGE = "Your application is in. An admin will get back to you."


def describe_apply_to_teach():
    def it_walks_a_locked_member_from_the_marketing_page_to_the_open_portal(live_server, page, login_via_code):
        MembershipPlanFactory()  # so login auto-provisions an ACTIVE member
        login_via_code("teach-me@example.com")

        # A locked member's click on the Teaching sidebar entry lands on the marketing
        # page, served in place at /classes/teach/ rather than bounced somewhere else.
        page.goto(f"{live_server.url}/classes/teach/")
        assert page.url.rstrip("/").endswith("/classes/teach")
        expect(page.get_by_role("heading", name=HERO)).to_be_visible()

        # The Apply modal is closed until the hero button opens it.
        send = page.get_by_role("button", name="Send My Application")
        expect(send).to_be_hidden()
        page.get_by_role("button", name="Apply to Teach").first.click()
        expect(send).to_be_visible()

        page.locator("#id_note").fill("I would like to run a two hour intro to wheel throwing.")
        send.click()

        # The application lands and the page comes back in the applied state.
        page.wait_for_url(lambda url: url.rstrip("/").endswith("/classes/teach"))
        expect(page.get_by_text(SUCCESS_MESSAGE)).to_be_visible()
        # By role, not by text: the Django success message echoes the same words, and a
        # bare text locator matches both and trips Playwright's strict mode.
        expect(page.get_by_role("heading", name=APPLIED_BANNER)).to_be_visible()

        # Still locked: the portal's own pages bounce back to the marketing page.
        page.goto(f"{live_server.url}/classes/teach/classes/new/")
        assert page.url.rstrip("/").endswith("/classes/teach")
        expect(page.get_by_role("heading", name=HERO)).to_be_visible()

        # An admin grants Instructor, exactly as the Permissions tab does.
        from membership.models import Member

        member = Member.objects.get(user__username="teach-me@example.com")
        member.grant_instructor(granted_by=None)

        # A reload now opens the real teaching dashboard.
        page.goto(f"{live_server.url}/classes/teach/")
        assert page.url.rstrip("/").endswith("/classes/teach")
        expect(page.get_by_role("heading", name=HERO)).to_have_count(0)
        expect(page.locator('[data-nav="teach"]')).to_be_visible()
