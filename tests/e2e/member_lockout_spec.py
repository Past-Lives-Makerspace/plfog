"""A former member who signs in on the members site lands on the lockout page (#409).

Drives the real login-by-code screens, so the allauth ``pre_login`` gate is exercised the way
a member meets it, not through the test client.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth import get_user_model

from membership.models import Member
from tests.membership.factories import MemberFactory

EMAIL = "former-e2e@example.com"


def describe_member_lockout():
    def it_turns_a_former_member_away_at_sign_in(page, live_server, login_via_code):
        user, _ = get_user_model().objects.get_or_create(username=EMAIL, defaults={"email": EMAIL})
        # Built explicitly rather than trusting the User post_save signal: later in the e2e
        # lane that signal can leave the user without a Member, and then there is nothing to
        # lock out and the spec signs straight in.
        member = Member.objects.filter(user=user).first() or MemberFactory(user=user)
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        assert Member.objects.get(user=user).status == Member.Status.FORMER

        login_via_code(EMAIL)

        assert "/accounts/locked/" in page.url
        assert "no longer active" in page.locator("#account-locked-message").inner_text()
        if os.environ.get("CAPTURE_409_SCREENSHOT"):
            Path("mockups/screenshots").mkdir(parents=True, exist_ok=True)
            page.set_viewport_size({"width": 1100, "height": 700})
            page.screenshot(path="mockups/screenshots/409-account-locked.png")
