"""End-to-end: saying you're interested in hosting, and an admin opening the portal.

Teaching stopped being self-service. This drives the real loop in a browser: a member
who cannot teach lands on the Host a Class page (not a 403, not a dead end), opens
the interest modal, sends a note, sees the note-sent state, and stays out of the
portal until an admin grants Instructor. The Alpine modal open/close and the
server-rendered state flip are exactly what the unit specs cannot prove. Run with
``pytest -m e2e``.
"""

from __future__ import annotations

from playwright.sync_api import expect

from tests.membership.factories import MembershipPlanFactory

HERO = "Share What You Love"
APPLIED_BANNER = "Thanks, We Got Your Note"
SUCCESS_MESSAGE = "Thanks. An admin will get back to you."
INTEREST_BUTTON = "I'm Interested"
SEND_BUTTON = "Send It"


def describe_apply_to_teach():
    def it_walks_a_locked_member_from_the_marketing_page_to_the_open_portal(live_server, page, login_via_code):
        MembershipPlanFactory()  # so login auto-provisions an ACTIVE member
        login_via_code("teach-me@example.com")
        from membership.models import Member

        Member.objects.filter(user__username="teach-me@example.com").update(phone="503 555 0100")

        # A locked member's click on the Host a Class sidebar entry lands on the
        # marketing page, served in place at /classes/teach/ rather than bounced elsewhere.
        page.goto(f"{live_server.url}/classes/teach/")
        assert page.url.rstrip("/").endswith("/classes/teach")
        expect(page.get_by_role("heading", name=HERO)).to_be_visible()
        expect(page.locator('[data-nav="teach"]').first).to_contain_text("Host a Class")

        # The interest modal is closed until the hero button opens it.
        send = page.get_by_role("button", name=SEND_BUTTON)
        expect(send).to_be_hidden()
        page.get_by_role("button", name=INTEREST_BUTTON).first.click()
        expect(send).to_be_visible()

        page.locator("#id_note").fill("I would like to run a two hour intro to wheel throwing.")
        page.select_option("#id_experience", "a_few")  # #690: required

        # Issue #536: the modal also asks how to reach the member. The detail field's label
        # follows the pick, and the pick prefills the account email or the profile phone;
        # only a browser proves the Alpine half of that.
        detail = page.locator("#id_contact_detail")
        detail_label = page.locator('label[for="id_contact_detail"]')
        expect(detail_label).to_have_text("Where to reach you")
        page.select_option("#id_contact_method", "email")
        expect(detail_label).to_have_text("Email address")
        expect(detail).to_have_value("teach-me@example.com")
        page.select_option("#id_contact_method", "text")
        expect(detail_label).to_have_text("Phone number for texts")
        expect(detail).to_have_value("503 555 0100")  # the phone on the profile replaces the email prefill
        detail.fill("503 555 0199")
        page.select_option("#id_contact_method", "phone")
        expect(detail_label).to_have_text("Phone number for calls")
        expect(detail).to_have_value("503 555 0199")  # a number the member typed is never overwritten
        send.click()

        # The note lands and the page comes back in the note-sent state.
        page.wait_for_url(lambda url: url.rstrip("/").endswith("/classes/teach"))
        expect(page.get_by_text(SUCCESS_MESSAGE)).to_be_visible()
        # By role, not by text: the Django success message echoes the same words, and a
        # bare text locator matches both and trips Playwright's strict mode.
        expect(page.get_by_role("heading", name=APPLIED_BANNER)).to_be_visible()

        # Still locked: the portal's own pages bounce back to the marketing page.
        page.goto(f"{live_server.url}/classes/teach/classes/new/")
        assert page.url.rstrip("/").endswith("/classes/teach")
        expect(page.get_by_role("heading", name=HERO)).to_be_visible()

        # The contact pair landed with the note.
        member = Member.objects.get(user__username="teach-me@example.com")
        assert member.teaching_contact_method == Member.TeachingContactMethod.PHONE
        assert member.teaching_contact_detail == "503 555 0199"
        assert member.teaching_experience == Member.TeachingExperience.A_FEW

        # An admin grants Instructor, exactly as the Permissions tab does.
        member.grant_instructor(granted_by=None)

        # A reload now opens the real teaching dashboard, and the sidebar reads Teaching.
        page.goto(f"{live_server.url}/classes/teach/")
        assert page.url.rstrip("/").endswith("/classes/teach")
        expect(page.get_by_role("heading", name=HERO)).to_have_count(0)
        expect(page.locator('[data-nav="teach"]').first).to_be_visible()
        expect(page.locator('[data-nav="teach"]').first).to_contain_text("Teaching")
