"""End-to-end: what a guest account (#654) looks like to the guest and to an admin.

The guest's lockout page carries the guest sentence, not the former member one, and Manage
Members shows the guest with a Guest label under the Guest filter. ``CAPTURE_654_SCREENSHOT=1``
also saves the PR's pictures under ``mockups/screenshots/``. Run with ``pytest -m e2e`` on
PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import SiteConfiguration
from membership.models import Member
from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

ADMIN_EMAIL = "guest-screens-admin@example.com"
CAPTURE = bool(os.environ.get("CAPTURE_654_SCREENSHOT"))
SHOTS = Path("mockups/screenshots")


def _seed_admin() -> None:
    MembershipPlanFactory()  # so the user signal provisions the member this then promotes
    user = User.objects.create_user(username=ADMIN_EMAIL, email=ADMIN_EMAIL)
    admin = user.member
    admin.fog_role = Member.FogRole.ADMIN
    admin.status = Member.Status.ACTIVE
    admin.save(update_fields=["fog_role", "status"])
    admin.sync_user_permissions()


def _seed_guest() -> Member:
    guest = MemberFactory(full_legal_name="Gail Guestbooker", _pre_signup_email="gail@example.com")
    provision_user_for_member(guest)
    guest.status = Member.Status.GUEST
    guest.save(update_fields=["status"])
    return guest


def describe_guest_account_screens():
    def it_shows_the_guest_sentence_on_the_lockout_page(live_server, page):
        page.goto(f"{live_server.url}{reverse('account_locked')}?reason=guest")
        page.get_by_text(SiteConfiguration.load().guest_member_signin_message).wait_for()
        if CAPTURE:
            page.screenshot(path=str(SHOTS / "654-guest-lockout.png"), full_page=True)

    def it_lists_a_guest_under_the_guest_filter_on_manage_members(live_server, page, login_via_code):
        _seed_admin()
        _seed_guest()
        login_via_code(ADMIN_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_admin_members')}?status=guest")
        page.get_by_text("Gail Guestbooker").first.wait_for()
        if CAPTURE:
            page.screenshot(path=str(SHOTS / "654-manage-members-guest.png"), full_page=True)
