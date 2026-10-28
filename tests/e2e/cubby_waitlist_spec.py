"""End-to-end: guild cubbies part 4 (#980), the waitlist when every cubby is taken.

A member opens a full guild's Cubbies tab and taps Notify Me When One Opens: the toast says they are
on the list and the card shows their place. Leave the List opens its confirm and takes them off, and
the Notify button comes back. A guild lead opens Rentals > Waitlist, sees the line in order and
removes someone through the confirm; the member is not told. At 390px the member's card and its
button fit, the staff list is one card per member, and nothing scrolls sideways. Waits are on what
the page shows. Run with ``pytest -m e2e``, on PostgreSQL in CI.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from core.models import Notification
from membership.models import CubbyWaitlistEntry, Guild, Member
from tests.membership.factories import (
    CubbyFactory,
    CubbyRentalFactory,
    GuildFactory,
    MembershipPlanFactory,
    ShelfUnitFactory,
)

LEAD_EMAIL = "cubbies-waitlist-lead@example.com"
MEMBER_EMAIL = "cubbies-waitlist-member@example.com"


def _person(email: str, name: str) -> Member:
    MembershipPlanFactory()
    member = User.objects.create_user(username=email, email=email).member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _full_guild() -> Guild:
    """Two cubbies, both rented, and one member already waiting."""
    guild = GuildFactory(
        name="Marlowe Kiln Guild", guild_lead=_person(LEAD_EMAIL, "Hollis Emberley"), show_cubbies_tab=True
    )
    unit = ShelfUnitFactory(guild=guild, name="Bisque Wall", rows=1, columns=2)
    for column, label in ((1, "K1"), (2, "K2")):
        CubbyRentalFactory(cubby=CubbyFactory(unit=unit, label=label, column=column))
    CubbyWaitlistEntry.objects.create(
        guild=guild,
        member=_person("waiting-first@example.com", "Odile Thornquist"),
        joined_at=timezone.now() - timedelta(days=1),
    )
    _person(MEMBER_EMAIL, "Perrin Saltmarsh")
    return guild


def _tab_url(live_server, guild: Guild) -> str:
    return f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}?tab=cubbies"


def _no_sideways_scroll(page) -> None:
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def describe_cubby_waitlist():
    def it_joins_and_leaves_the_list(live_server, page, login_via_code):
        guild = _full_guild()
        page.set_viewport_size({"width": 1280, "height": 900})
        login_via_code(MEMBER_EMAIL)
        page.goto(_tab_url(live_server, guild))

        full = page.locator("[data-cubbies-full]")
        expect(full).to_contain_text("Every Cubby Is Taken")
        expect(full).to_contain_text("All 2 cubbies are rented right now.")
        full.get_by_role("button", name="Notify Me When One Opens").click()

        expect(page.locator("#plt-container").get_by_text("You're on the list.")).to_be_visible()
        waiting = page.locator("[data-cubbies-waiting]")
        expect(waiting).to_contain_text("You're on the List")
        expect(waiting.locator("[data-waitlist-place]")).to_have_text("number 2")
        expect(page.locator("[data-cubbies-full]")).to_have_count(0)
        assert CubbyWaitlistEntry.objects.waiting().filter(member__full_legal_name="Perrin Saltmarsh").exists()

        waiting.locator("[data-waitlist-leave]").click()
        dialog = page.get_by_role("dialog").filter(has_text="Leave the List?")
        expect(dialog).to_be_visible()
        dialog.get_by_role("button", name="Leave the List").click()

        expect(page.locator("#plt-container").get_by_text("You've left the list.")).to_be_visible()
        expect(page.locator("[data-cubbies-full] [data-waitlist-join]")).to_be_visible()
        assert not CubbyWaitlistEntry.objects.waiting().filter(member__full_legal_name="Perrin Saltmarsh").exists()

    def it_lets_staff_see_the_line_and_remove_someone_quietly(live_server, page, login_via_code):
        guild = _full_guild()
        later = CubbyWaitlistEntry.objects.create(
            guild=guild, member=Member.objects.get(full_legal_name="Perrin Saltmarsh")
        )
        page.set_viewport_size({"width": 1280, "height": 900})
        login_via_code(LEAD_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_guild_cubbies_rentals', args=[guild.pk])}")

        pane = page.locator("[data-cubby-rentals]")
        chip = pane.locator('[data-rental-filter="waitlist"]')
        expect(chip).to_have_text("Waitlist (2)")
        chip.click()
        expect(chip).to_have_attribute("aria-current", "true")
        table = pane.locator("[data-cubby-waitlist-table]")
        expect(table.locator("tbody tr")).to_have_count(2)
        expect(table.locator("tbody tr").first).to_contain_text("Odile Thornquist")
        expect(table.locator("tbody tr").nth(1)).to_contain_text("Perrin Saltmarsh")

        pane.locator(f'[data-waitlist-remove="{later.pk}"]').click()
        dialog = page.get_by_role("dialog").filter(has_text="Remove Perrin Saltmarsh from the Waitlist?")
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("We won't tell Perrin.")
        dialog.get_by_role("button", name="Remove").click()

        expect(page.locator("#plt-container").get_by_text("Perrin Saltmarsh is off the waitlist.")).to_be_visible()
        expect(table.locator("tbody tr")).to_have_count(1)
        expect(pane.locator('[data-rental-filter="waitlist"]')).to_have_text("Waitlist (1)")
        later.refresh_from_db()
        assert later.left_at is not None
        assert not Notification.objects.filter(user=later.member.user).exists()

    def it_fits_a_phone_for_the_member_and_for_staff(live_server, page, login_via_code):
        guild = _full_guild()
        page.set_viewport_size({"width": 390, "height": 844})
        login_via_code(MEMBER_EMAIL)
        page.goto(_tab_url(live_server, guild))

        join = page.locator("[data-cubbies-full] [data-waitlist-join]")
        expect(join).to_be_visible()
        card_box = page.locator("[data-cubbies-full]").bounding_box()
        join_box = join.bounding_box()
        assert card_box is not None and join_box is not None
        assert join_box["x"] + join_box["width"] <= card_box["x"] + card_box["width"]
        _no_sideways_scroll(page)
        join.click()
        expect(page.locator("[data-cubbies-waiting] [data-waitlist-place]")).to_have_text("number 2")
        _no_sideways_scroll(page)

        page.context.clear_cookies()
        login_via_code(LEAD_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_guild_cubbies_rentals', args=[guild.pk])}?show=waitlist")
        pane = page.locator("[data-cubby-rentals]")
        expect(pane.locator("[data-waitlist-card]")).to_have_count(2)
        expect(pane.locator("[data-waitlist-card]").first).to_be_visible()
        expect(pane.locator("[data-waitlist-card]").first).to_contain_text("Number 1")
        expect(pane.locator("[data-cubby-waitlist-table]")).to_be_hidden()
        _no_sideways_scroll(page)
