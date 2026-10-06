"""End-to-end: an admin gives and takes a leadership badge from a member's Edit page (#650).

Pytest's Django client never runs the page's script, so this walks it in a real browser: Dixie
is on no Leadership Directory tab; a give that loses its connection puts the switch back with
an error toast; a real give says so in a toast and her Member Directory card shows Employee then
the badge in its color; taking it back from the Edit page takes it off the card.

Waits are on what the page shows (a toast's text, a switch's state, a pill), never a fixed
sleep. ``CAPTURE_650_SCREENSHOT=1`` also saves the PR's pictures under ``mockups/screenshots/``.
Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse

from membership.models import LeadershipBadge, LeadershipTab, Member
from tests.membership.factories import LeadershipBadgeFactory, MemberFactory, MembershipPlanFactory

ADMIN_EMAIL = "member-edit-badges-admin@example.com"
CAPTURE = bool(os.environ.get("CAPTURE_650_SCREENSHOT"))
SHOTS = Path("mockups/screenshots")


def _seed() -> tuple[Member, LeadershipBadge]:
    """An admin, Dixie (an Employee on no tab), the badge to give her and one she never gets."""
    MembershipPlanFactory()  # so the user signal provisions the member this then promotes
    user = User.objects.create_user(username=ADMIN_EMAIL, email=ADMIN_EMAIL)
    admin = user.member
    admin.fog_role = Member.FogRole.ADMIN
    admin.status = Member.Status.ACTIVE
    admin.save(update_fields=["fog_role", "status"])
    admin.sync_user_permissions()
    LeadershipTab.objects.all().delete()
    dixie = MemberFactory(
        full_legal_name="Dixie Lindqvist",
        member_type=Member.MemberType.EMPLOYEE,
        pronouns="she/her",
        about_me="Runs the front desk and the community calendar.",
    )
    badge = LeadershipBadgeFactory(label="Community Engagement Manager", color="#2F855A")
    LeadershipBadgeFactory(label="Elevator Certified", color="#EEB44B")
    return dixie, badge


def _switch(page, badge: LeadershipBadge):
    """The badge's switch: the visible slider to click, and the checkbox it drives."""
    checkbox = page.locator(f"input[name='badges-badge_{badge.pk}']")
    return page.locator("label.pl-toggle").filter(has=checkbox), checkbox


def _card(page):
    return page.locator(".directory-card").filter(has_text="Dixie Lindqvist")


def describe_member_edit_badges():
    def it_gives_and_takes_a_badge_that_follows_on_the_directory_card(live_server, page, login_via_code):
        dixie, badge = _seed()
        login_via_code(ADMIN_EMAIL)
        edit_url = f"{live_server.url}{reverse('hub_admin_member_edit', args=[dixie.pk])}"
        directory_url = f"{live_server.url}{reverse('hub_member_directory')}"
        page.goto(edit_url)
        slider, checkbox = _switch(page, badge)
        slider.wait_for(state="visible")
        assert not checkbox.is_checked()

        # A give that loses its connection: nothing saved, the switch goes back, an error toast.
        give_url = reverse("hub_admin_leadership_badge_give", args=[badge.pk, dixie.pk])
        page.route(f"**{give_url}", lambda route: route.abort())
        slider.click()
        page.get_by_text("Couldn't save that badge. Check your connection.").wait_for()
        page.wait_for_function(
            "(name) => { const box = document.querySelector(`input[name='${name}']`);"
            " return !box.checked && !box.disabled; }",
            arg=f"badges-badge_{badge.pk}",
        )
        assert badge.members.count() == 0
        page.unroute(f"**{give_url}")

        # Give it: a toast says so, and she holds it.
        slider.click()
        page.get_by_text("Gave Dixie Lindqvist the Community Engagement Manager badge.").wait_for()
        assert checkbox.is_checked()
        assert list(badge.members.all()) == [dixie]
        if CAPTURE:
            SHOTS.mkdir(parents=True, exist_ok=True)
            page.locator(".pl-leadership").screenshot(path=str(SHOTS / "650-member-edit-badges.png"))

        # Her directory card: Employee, then the badge in its own color with the text that reads on it.
        page.goto(directory_url)
        pill = _card(page).locator(".pl-directory-badges .pl-leader-badge")
        pill.wait_for(state="visible")
        assert pill.all_inner_texts() == ["Community Engagement Manager"]
        assert pill.evaluate("el => [getComputedStyle(el).backgroundColor, getComputedStyle(el).color]") == [
            "rgb(47, 133, 90)",
            "rgb(0, 0, 0)" if badge.text_color == "#000000" else "rgb(255, 255, 255)",
        ]
        meta = _card(page).locator(".directory-card__meta")
        assert meta.inner_text().index("Employee") < meta.inner_text().index("Community Engagement Manager")
        if CAPTURE:
            _card(page).screenshot(path=str(SHOTS / "650-directory-badges.png"))

        # Take it back from the Edit page: a toast, and the card loses the pill.
        page.goto(edit_url)
        slider, checkbox = _switch(page, badge)
        slider.wait_for(state="visible")
        assert checkbox.is_checked()
        slider.click()
        page.get_by_text("Took the Community Engagement Manager badge from Dixie Lindqvist.").wait_for()
        assert not checkbox.is_checked()
        assert badge.members.count() == 0
        page.goto(directory_url)
        _card(page).wait_for(state="visible")
        assert _card(page).locator(".pl-leader-badge").count() == 0
