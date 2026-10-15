"""The Door Code card on Home in a real browser (#813), at phone width.

An admin with no code set opens Set Code, saves one through the modal and lands back on Home
with the toast and the new code on the card; a member reads the code with nothing pushing the
page sideways. The repo is public, so the code is an obviously fake value.
Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from core.models import SiteConfiguration
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

PHONE = {"width": 390, "height": 844}
CARD = "[data-door-code-card]"


def _sign_in(login_via_code, email: str, *, fog_role: str = Member.FogRole.MEMBER) -> None:
    MembershipPlanFactory()
    login_via_code(email)
    user = get_user_model().objects.get(username=email)
    member = Member.objects.get(user=user)
    member.fog_role = fog_role
    member.welcome_dismissed_at = timezone.now()
    member.save(update_fields=["fog_role", "welcome_dismissed_at"])
    member.sync_user_permissions()


def _home(page, live_server) -> None:
    page.goto(f"{live_server.url}{reverse('hub_home')}")


def describe_the_door_code_card():
    def it_lets_an_admin_set_the_code_from_the_modal(page, live_server, login_via_code):
        _sign_in(login_via_code, "door-admin-e2e@example.com", fog_role=Member.FogRole.ADMIN)
        page.set_viewport_size(PHONE)
        _home(page, live_server)
        expect(page.locator(f"{CARD} [data-door-code-empty]")).to_be_visible()

        page.locator(f"{CARD} [data-door-code-edit]").click()
        form = page.locator("[data-door-code-form]")
        expect(form).to_be_visible()
        form.get_by_label("Door code").fill("  4242#  ")
        form.get_by_role("button", name="Save").click()

        expect(page.locator(".plt-toast", has_text="Door code saved.")).to_be_visible()
        expect(page.locator(f"{CARD} [data-door-code]")).to_have_text("4242#")
        expect(page.locator(f"{CARD} [data-door-code-changed]")).to_contain_text("Changed")
        expect(page.locator(f"{CARD} [data-door-code-edit]")).to_have_text("Change")
        assert SiteConfiguration.load().door_code == "4242#"

    def it_shows_a_member_the_code_without_pushing_the_page_sideways(page, live_server, login_via_code):
        config = SiteConfiguration.load()
        config.door_code = "1234567890#ABCDEFGH#"
        config.door_code_changed_at = datetime(2026, 10, 3, 18, 0, tzinfo=dt_timezone.utc)
        config.save()
        _sign_in(login_via_code, "door-member-e2e@example.com")
        page.set_viewport_size(PHONE)
        _home(page, live_server)

        expect(page.locator(f"{CARD} [data-door-code]")).to_have_text("1234567890#ABCDEFGH#")
        expect(page.locator(f"{CARD} [data-door-code-changed]")).to_have_text("Changed Oct 3, 2026")
        expect(page.locator(f"{CARD} [data-door-code-edit]")).to_have_count(0)
        overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 0
