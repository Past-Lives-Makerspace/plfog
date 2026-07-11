"""End-to-end: a signed-in member reads and clears their Notifications page.

Exercises the real browser flow — login-by-code, then the /notifications/ page and
its "Mark all as read" action — against a live server. Proves the unread emphasis,
the topbar bell badge, and that marking-all clears both. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from tests.membership.factories import MembershipPlanFactory

MEMBER_EMAIL = "notes-member@example.com"


def describe_notifications():
    def it_shows_unread_notifications_then_marks_them_all_read(live_server, page, login_via_code):
        # A plan must exist so the login signal auto-creates the member (CI's fresh DB
        # has none by default; the notifications chrome renders best with a real member).
        MembershipPlanFactory()

        # 1. Sign in through the genuine login-by-code flow.
        login_via_code(MEMBER_EMAIL)
        user = get_user_model().objects.get(username=MEMBER_EMAIL)

        # 2. Two unread notifications waiting for this member.
        from core.models import Notification

        Notification.objects.create(
            user=user, trigger="guild.announcement", title="Spring glaze restock is in", body="Come make something."
        )
        Notification.objects.create(
            user=user, trigger="class.reminder", title="Casting starts Saturday", body="Bring closed-toe shoes."
        )

        # Signing in itself files a welcome/onboarding notification, so read the live
        # unread count rather than hardcoding — the page and badge must both agree with it.
        unread = Notification.objects.filter(user=user, read_at__isnull=True).count()

        # 3. Open the Notifications page — our rows render, every unread one carries the
        #    emphasis class, and the topbar bell shows the matching count.
        page.goto(f"{live_server.url}{reverse('notification_list')}")
        expect(page.locator("body")).to_contain_text("Spring glaze restock is in")
        expect(page.locator("body")).to_contain_text("Casting starts Saturday")
        expect(page.locator(".pl-note--unread")).to_have_count(unread)
        expect(page.locator(".pl-bell__badge")).to_have_text(str(unread))

        # 4. Mark all as read via its form (target the action URL, not the visible label —
        #    the hub "what's new" widget echoes the CHANGELOG, which can mention the label).
        read_all_action = reverse("notification_read_all")
        page.locator(f'form[action="{read_all_action}"] button[type="submit"]').click()

        # 5. The POST redirects back to the Notifications page, now with zero unread: the
        #    Mark-all form is gone and the bell badge has cleared.
        expect(page).to_have_url(re.compile(r"/notifications/$"))
        expect(page.locator(f'form[action="{read_all_action}"]')).to_have_count(0)
        expect(page.locator(".pl-note--unread")).to_have_count(0)
        expect(page.locator(".pl-bell__badge")).to_have_count(0)
