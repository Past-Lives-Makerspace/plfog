"""End-to-end: applying to host a class, and an admin opening the portal.

Teaching stopped being self-service. This drives the real loop in a browser: a member
who cannot teach lands on the Host a Class page (not a 403, not a dead end), follows
I'm Interested to the application page (#793), sees the conditional questions open and
close in place, gets a field error for Website yes with no address with every other
answer kept, sends it, sees the application-sent state, and stays out of the portal
until an admin grants Instructor. The Alpine reveals and prefill and the
server-rendered state flip are exactly what the unit specs cannot prove. Run with
``pytest -m e2e``.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.membership.factories import MembershipPlanFactory

HERO = "Share What You Love"
APPLY_HEADING = "Apply to Host a Class"
APPLIED_BANNER = "Thanks, We Got Your Application"
SUCCESS_MESSAGE = "Thanks. An admin will get back to you."
INTEREST_BUTTON = "I'm Interested"
SEND_BUTTON = "Send Application"
WEBSITE_ERROR = "Add your website's address, or pick No."


def _chip(page: Page, field: str, label: str) -> None:
    """Click the chip (a label around a radio or checkbox) for ``label`` in ``field``'s group."""
    page.locator(f'label.pl-chip:has(input[name="{field}"])', has_text=label).click()


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

        # #793: I'm Interested is a link straight to the application page; no modal.
        page.get_by_role("link", name=INTEREST_BUTTON).first.click()
        page.wait_for_url(lambda url: url.rstrip("/").endswith("/classes/teach/apply"))
        expect(page.get_by_role("heading", name=APPLY_HEADING)).to_be_visible()

        page.locator("#id_class_title").fill("Wheel Throwing 101")
        page.locator("#id_note").fill("I would like to run a two hour intro to wheel throwing.")
        _chip(page, "class_format", "A series of sessions")
        page.locator("#id_shop_area").fill("Ceramics studio")
        _chip(page, "practice_years", "3 to 10 years")

        # Where they have taught opens for every level except first time, in place.
        taught_where = page.locator("#id_taught_where")
        expect(taught_where).to_be_hidden()
        _chip(page, "experience", "Taught a few classes")
        expect(taught_where).to_be_visible()
        _chip(page, "experience", "First time teaching")
        expect(taught_where).to_be_hidden()
        _chip(page, "experience", "Taught a few classes")
        taught_where.fill("The community college, two terms.")

        _chip(page, "class_size", "5 to 8")
        _chip(page, "availability", "Weekday evenings")
        _chip(page, "availability", "Weekends")

        # The website address opens on Yes; the socials stay closed on No.
        website = page.locator("#id_website")
        expect(website).to_be_hidden()
        _chip(page, "has_website", "Yes")
        expect(website).to_be_visible()
        _chip(page, "has_socials", "No")
        expect(page.locator("#id_socials")).to_be_hidden()

        # Issue #536: the page also asks how to reach the member. The detail field's label
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

        # Website yes with no address: the server says so beside the field, and every
        # other answer is still there, with the address field open.
        page.get_by_role("button", name=SEND_BUTTON).click()
        expect(page.get_by_text(WEBSITE_ERROR)).to_be_visible()
        expect(page.locator("#id_website")).to_be_visible()
        expect(page.locator("#id_class_title")).to_have_value("Wheel Throwing 101")
        expect(page.locator("#id_taught_where")).to_have_value("The community college, two terms.")
        expect(page.locator('input[name="class_format"][value="series"]')).to_be_checked()
        expect(page.locator('input[name="availability"][value="weekends"]')).to_be_checked()
        expect(page.locator("#id_contact_detail")).to_have_value("503 555 0199")
        assert Member.objects.get(user__username="teach-me@example.com").teaching_applied_at is None

        page.locator("#id_website").fill("https://robin.example")
        page.get_by_role("button", name=SEND_BUTTON).click()

        # The application lands and the page comes back in the application-sent state.
        page.wait_for_url(lambda url: url.rstrip("/").endswith("/classes/teach"))
        expect(page.get_by_text(SUCCESS_MESSAGE)).to_be_visible()
        # By role, not by text: the Django success message echoes the same words, and a
        # bare text locator matches both and trips Playwright's strict mode.
        expect(page.get_by_role("heading", name=APPLIED_BANNER)).to_be_visible()

        # Still locked: the portal's own pages bounce back to the marketing page.
        page.goto(f"{live_server.url}/classes/teach/classes/new/")
        assert page.url.rstrip("/").endswith("/classes/teach")
        expect(page.get_by_role("heading", name=HERO)).to_be_visible()

        # Every answer landed.
        member = Member.objects.get(user__username="teach-me@example.com")
        assert member.teaching_contact_method == Member.TeachingContactMethod.PHONE
        assert member.teaching_contact_detail == "503 555 0199"
        assert member.teaching_experience == Member.TeachingExperience.A_FEW
        assert member.teaching_class_title == "Wheel Throwing 101"
        assert member.teaching_class_format == Member.TeachingClassFormat.SERIES
        assert member.teaching_taught_where == "The community college, two terms."
        assert member.teaching_availability == ["weekday_evenings", "weekends"]
        assert member.teaching_website == "https://robin.example"
        assert member.teaching_socials == ""

        # An admin grants Instructor, exactly as the Permissions tab does.
        member.grant_instructor(granted_by=None)

        # A reload now opens the real teaching dashboard, and the sidebar reads Teaching.
        page.goto(f"{live_server.url}/classes/teach/")
        assert page.url.rstrip("/").endswith("/classes/teach")
        expect(page.get_by_role("heading", name=HERO)).to_have_count(0)
        expect(page.locator('[data-nav="teach"]').first).to_be_visible()
        expect(page.locator('[data-nav="teach"]').first).to_contain_text("Teaching")
