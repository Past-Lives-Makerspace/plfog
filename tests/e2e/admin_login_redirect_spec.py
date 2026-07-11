"""End-to-end: the deprecated Django admin password login is gone.

A real browser hitting ``/admin/login/`` must land on the passwordless email-code
login — never a username/password form. A signed-in non-staff member hitting the
admin index gets a 403, not a login box. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from tests.membership.factories import MembershipPlanFactory

MEMBER_EMAIL = "non-staff@example.com"


def describe_admin_login_redirect():
    def it_sends_an_anonymous_visitor_to_the_email_code_login(live_server, page):
        # /admin/login/ 302s to the allauth login-by-code screen; the browser follows it.
        page.goto(f"{live_server.url}/admin/login/")
        expect(page).to_have_url(re.compile(r"/accounts/login/code/"))
        # No username/password form is reachable anymore — the whole point of the change.
        expect(page.locator('input[type="password"]')).to_have_count(0)

    def it_403s_a_signed_in_non_staff_member_at_the_admin_index(live_server, page, login_via_code):
        # A plain member (no is_staff) signs in through the real code flow...
        MembershipPlanFactory()
        login_via_code(MEMBER_EMAIL)

        # ...then hitting the admin index is forbidden, not a password prompt.
        response = page.goto(f"{live_server.url}/admin/")
        assert response is not None
        assert response.status == 403
        expect(page.locator('input[type="password"]')).to_have_count(0)
