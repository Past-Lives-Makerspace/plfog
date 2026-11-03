"""End-to-end: a garden bed reserved by season (#1095), from the Season card to a reserved bed.

A manager types a season start and end on Hours & Limits and saves. A member opens the bed's page,
sees it open for the season with the dates and no time pickers, presses Reserve This Season, confirms
in "Reserve this season?" and sees the bed reserved for the season and theirs under Your Reservations
Here. Waits are on what the page shows. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import Equipment, EquipmentReservation, Member
from tests.membership.factories import EquipmentFactory, EquipmentStaffMembershipFactory, MembershipPlanFactory

MANAGER_EMAIL = "equipment-season-manager@example.com"
MEMBER_EMAIL = "equipment-season-member@example.com"


def _active_member(email: str, name: str) -> Member:
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["status", "full_legal_name"])
    return member


def describe_season_reservations():
    def it_takes_season_dates_from_the_season_card_to_a_reserved_bed(live_server, page, login_via_code):
        MembershipPlanFactory()  # so the user signal provisions each member
        manager = _active_member(MANAGER_EMAIL, "Odalys Furrowmere")
        member = _active_member(MEMBER_EMAIL, "Tamsin Rootwhistle")
        bed = EquipmentFactory(name="Wickerfen Bed 5A", kind=Equipment.Kind.SPACE)
        EquipmentStaffMembershipFactory(equipment=bed, member=manager)
        today = timezone.localdate()
        starts_on, ends_on = today + timedelta(days=10), today + timedelta(days=220)
        manage_url = f"{live_server.url}{reverse('hub_equipment_manage', args=[bed.slug])}"
        detail_url = f"{live_server.url}{reverse('hub_equipment_detail', args=[bed.slug])}"
        page.set_viewport_size({"width": 1100, "height": 900})

        # The manager sets the season on Hours & Limits and saves.
        login_via_code(MANAGER_EMAIL)
        page.goto(f"{manage_url}?tab=hours")
        card = page.locator("[data-season-card]")
        expect(card).to_be_visible()
        card.get_by_label("Season start").fill(starts_on.isoformat())
        card.get_by_label("Season end").fill(ends_on.isoformat())
        with page.expect_navigation():
            page.get_by_role("button", name="Save", exact=True).click()
        expect(page.locator("[data-season-card]").get_by_label("Season start")).to_have_value(starts_on.isoformat())
        bed.refresh_from_db()
        assert (bed.season_starts_on, bed.season_ends_on) == (starts_on, ends_on)

        # The member sees the season open, with the dates and no time pickers, and reserves it.
        page.context.clear_cookies()
        login_via_code(MEMBER_EMAIL)
        page.goto(detail_url)
        expect(page.locator("[data-season-line]")).to_have_text(f"Open for the season, {bed.season_label}")
        expect(page.locator("select[name=starts_at]")).to_have_count(0)
        page.get_by_role("button", name=f"Reserve This Season, {bed.season_label}").click()
        dialog = page.get_by_role("dialog").filter(has_text="Reserve this season?")
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text(f"The Wickerfen Bed 5A, {bed.season_label}.")
        dialog.get_by_role("button", name="Reserve", exact=True).click()

        expect(page.locator("[data-season-line]")).to_have_text(f"Reserved for the season, {bed.season_label}")
        expect(page.locator("[data-season-yours]")).to_be_visible()
        expect(page.locator("[data-reserve-season]")).to_have_count(0)
        expect(page.locator("[data-season-row]").first).to_have_text(f"Season, {bed.season_label}")
        reservation = EquipmentReservation.objects.get(equipment=bed)
        assert (reservation.member, reservation.is_season) == (member, True)
        assert (reservation.starts_at, reservation.ends_at) == bed.season_span()
