"""End-to-end (#729): a lead adds nine members in one click, sends, and finds them saved next time.

Drives the composer's "Add people" picker in a real browser: the search box filters the add list
client side, nine ticked members land checked in the recipients with one "Add selected" click, a
typed address comes back from the server as a checked row, and the send saves them all to the
guild's mailing list, so a new announcement opens with every one of them pre-checked. Screenshots
of the open picker in both themes go to ``PL_SCREENSHOT_DIR`` when it is set. Run with
``pytest -m e2e``.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import Member
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MembershipPlanFactory

LEAD_EMAIL = "penina-lead@example.com"
EDITOR = '.pl-rte[data-rte-for="id_body"] .ql-editor'


def _member(email: str, name: str) -> Member:
    """A member whose login account has no name of its own (the name is on the Member), as in prod."""
    user = get_user_model().objects.create_user(username=email, email=email)
    member = Member.objects.get(user=user)
    member.full_legal_name = name
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["full_legal_name", "status"])
    return member


def _screenshot(page, theme: str) -> None:
    """The open picker in ``theme``, saved only when ``PL_SCREENSHOT_DIR`` names a folder."""
    folder = os.environ.get("PL_SCREENSHOT_DIR")
    if not folder:
        return
    page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
    # Tall enough that the whole picker fits without scrolling under the sticky top bar.
    page.set_viewport_size({"width": 1100, "height": 2400})
    page.locator("#compose-recipients").screenshot(path=str(Path(folder) / f"729-picker-{theme}.png"))


def describe_adding_many_people_to_a_guild_announcement():
    def it_adds_nine_in_one_click_and_lists_them_next_time(live_server, page, login_via_code):
        MembershipPlanFactory()
        guild = GuildFactory(name="Writers Guild")
        roster = _member("rory@example.com", "Rory Roster")
        GuildMembershipFactory(guild=guild, member=roster)
        nine = [_member(f"writer{i}@example.com", f"Writer {i}") for i in range(1, 10)]
        left_out = _member("zed@example.com", "Zed Elsewhere")

        login_via_code(LEAD_EMAIL)
        guild.guild_lead = Member.objects.get(user__username=LEAD_EMAIL)
        guild.save(update_fields=["guild_lead"])

        page.goto(f"{live_server.url}{reverse('hub_compose')}?audience=guild:{guild.pk}")
        page.locator(EDITOR).fill("Workshop night is Thursday.")
        page.locator("[data-compose-add-toggle]").click()

        # The search box narrows the add list to matching rows, and clearing it brings them back.
        search = page.locator("[data-compose-add-filter]")
        search.fill("writer 3")
        expect(page.locator("[data-compose-add-row]:visible")).to_have_count(1)
        search.fill("nobody at all")
        expect(page.locator("[data-compose-add-none]")).to_be_visible()
        search.fill("")
        expect(page.locator(f'[data-compose-add-pick][value="user:{left_out.user_id}"]')).to_be_visible()

        for member in nine:
            page.locator(f'[data-compose-add-pick][value="user:{member.user_id}"]').check()
        expect(page.locator("[data-compose-add-selected]")).to_contain_text("(9)")
        _screenshot(page, "dark")
        _screenshot(page, "light")
        page.evaluate("() => document.documentElement.removeAttribute('data-theme')")

        # One click adds all nine, checked, and takes them off the add list.
        page.locator("[data-compose-add-selected]").click()
        added = page.locator("#compose-added-recipients input[name=recipients]:checked")
        expect(added).to_have_count(9)
        expect(page.locator(f'[data-compose-add-pick][value="user:{nine[0].user_id}"]')).to_be_hidden()
        expect(page.locator("[data-compose-add-selected]")).to_be_disabled()

        # A typed address comes back as a checked email only row.
        page.locator("#compose-add-addresses").fill("friend@example.com")
        page.locator("[data-compose-add-addresses]").click()
        expect(page.locator('#compose-added-recipients input[value="custom:friend@example.com"]')).to_be_checked()
        expect(page.locator("#compose-add-addresses")).to_have_value("")

        # Send it.
        page.get_by_role("button", name="Preview & send →").first.click()
        page.get_by_role("button", name="Send announcement").click()
        with page.expect_navigation():
            page.get_by_role("button", name="Yes, send it").click()
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_announcements')}?tab=sent")

        assert set(guild.mailing_list_emails.filter(user__isnull=False).values_list("user_id", flat=True)) == {
            member.user_id for member in nine
        }
        assert guild.mailing_list_emails.filter(email="friend@example.com", user__isnull=True).exists()
        assert not guild.mailing_list_emails.filter(user=roster.user).exists()

        # A new announcement for the guild lists all nine and the address, already checked.
        page.goto(f"{live_server.url}{reverse('hub_compose')}?audience=guild:{guild.pk}")
        for member in nine:
            expect(
                page.locator(f'#compose-recipients input[name=recipients][value="user:{member.user_id}"]')
            ).to_be_checked()
        expect(page.locator('#compose-recipients input[value="custom:friend@example.com"]')).to_be_checked()
        expect(page.locator("#compose-recipients")).to_contain_text("Writer 1 · writer1@example.com")
