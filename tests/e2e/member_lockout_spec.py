"""A former member who signs in on the members site lands on the lockout page (#409).

Drives the real login-by-code screens, so the allauth ``pre_login`` gate is exercised the way
a member meets it, not through the test client.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth import get_user_model

from membership.models import Member

EMAIL = "former-e2e@example.com"


def describe_member_lockout():
    def it_turns_a_former_member_away_at_sign_in(page, live_server, login_via_code):
        user, _ = get_user_model().objects.get_or_create(username=EMAIL, defaults={"email": EMAIL})
        Member.objects.filter(user=user).update(status=Member.Status.FORMER)

        login_via_code(EMAIL)

        assert "/accounts/locked/" in page.url
        assert "no longer active" in page.locator("#account-locked-message").inner_text()
        if os.environ.get("CAPTURE_409_SCREENSHOT"):
            Path("mockups/screenshots").mkdir(parents=True, exist_ok=True)
            page.set_viewport_size({"width": 1100, "height": 700})
            page.screenshot(path="mockups/screenshots/409-account-locked.png")
