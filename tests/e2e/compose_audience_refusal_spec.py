"""End-to-end (#415): a lead who loses one guild while the composer is open keeps the announcement.

The Django test client is not htmx, so a unit spec asserting the 403 passes whether or not the
browser does the right thing. Here a real browser holds the composer open while the lead role is
taken away for one of their two guilds: Save draft shows the audience reason as a toast, and Send (a plain form POST, never a
GET to the send URL, so never a 405) lands back in the composer with the reason on the audience
field, the audience blank (never the refused guild's roster), and the typed subject and message
still there. A screenshot of the refused Send goes to
``PL_SCREENSHOT_DIR`` when it is set. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.urls import reverse
from playwright.sync_api import expect

from membership.models import AnnouncementDraft, GuildAnnouncement, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

LEAD_EMAIL = "kiln-lead@example.com"
EDITOR = '.pl-rte[data-rte-for="id_body"] .ql-editor'
MESSAGE = "Glaze night moves to Thursday zxqv."
REASON = "Your account cannot email members of Kiln Guild."


def describe_a_send_refused_at_the_audience_gate():
    def it_keeps_the_announcement_and_says_why(live_server, page, login_via_code):
        MembershipPlanFactory()
        guild = GuildFactory(name="Kiln Guild")
        other = GuildFactory(name="Print Guild")
        login_via_code(LEAD_EMAIL)
        lead = Member.objects.get(user__username=LEAD_EMAIL)
        for led in (guild, other):
            led.guild_lead = lead
            led.save(update_fields=["guild_lead"])

        page.goto(f"{live_server.url}{reverse('hub_compose')}?audience=guild:{guild.pk}")
        page.get_by_label("Subject").fill("Glaze night zxqv")
        page.locator(EDITOR).fill(MESSAGE)

        # The lead role goes away while the composer sits open.
        guild.guild_lead = None
        guild.save(update_fields=["guild_lead"])

        # Save draft is htmx: still a 403, now with the reason as a toast.
        page.get_by_role("button", name="Save draft").first.click()
        expect(page.locator(".plt-toast", has_text=REASON)).to_be_visible()

        page.get_by_role("tab", name="2. Preview & send").click()
        page.get_by_role("button", name="Send email", exact=True).click()
        with page.expect_navigation():
            page.get_by_role("button", name="Yes, send it").click()

        expect(page).to_have_url(f"{live_server.url}{reverse('hub_compose_send')}")
        expect(page.get_by_text("Method Not Allowed")).to_have_count(0)
        expect(page.locator(".plt-toast", has_text=f"{REASON} Nothing was sent")).to_be_visible()
        page.get_by_role("tab", name="1. Compose").click()
        expect(page.get_by_text(REASON, exact=True)).to_be_visible()
        expect(page.get_by_label("Who is this for?")).to_have_value("")
        expect(page.get_by_label("Subject")).to_have_value("Glaze night zxqv")
        expect(page.locator(EDITOR)).to_have_text(MESSAGE)
        folder = os.environ.get("PL_SCREENSHOT_DIR")
        if folder:
            page.screenshot(path=str(Path(folder) / "415-refused-send.png"), full_page=False)

        assert not AnnouncementDraft.objects.exists()
        assert not GuildAnnouncement.objects.exists()


def describe_a_send_from_a_lead_who_lost_their_only_guild():
    def it_shows_their_words_to_copy_and_nothing_else(live_server, page, login_via_code):
        MembershipPlanFactory()
        guild = GuildFactory(name="Kiln Guild")
        login_via_code(LEAD_EMAIL)
        guild.guild_lead = Member.objects.get(user__username=LEAD_EMAIL)
        guild.save(update_fields=["guild_lead"])

        page.goto(f"{live_server.url}{reverse('hub_compose')}?audience=guild:{guild.pk}")
        page.get_by_label("Subject").fill("Glaze night zxqv")
        page.locator(EDITOR).fill(MESSAGE)

        # Their only guild goes away, so they can no longer compose at all.
        guild.guild_lead = None
        guild.save(update_fields=["guild_lead"])

        page.get_by_role("button", name="Save draft").first.click()
        expect(page.locator(".plt-toast", has_text="Your account cannot use Email Members.")).to_be_visible()

        page.get_by_role("tab", name="2. Preview & send").click()
        page.get_by_role("button", name="Send email", exact=True).click()
        with page.expect_navigation():
            page.get_by_role("button", name="Yes, send it").click()

        expect(page).to_have_url(f"{live_server.url}{reverse('hub_compose_send')}")
        expect(page.get_by_text("Method Not Allowed")).to_have_count(0)
        expect(page.locator("[data-compose-refused-reason]")).to_contain_text("Your account cannot use Email Members.")
        expect(page.get_by_label("Subject")).to_have_value("Glaze night zxqv")
        expect(page.get_by_label("Message")).to_have_value(MESSAGE)
        expect(page.locator("[data-compose-refused] form")).to_have_count(0)
        folder = os.environ.get("PL_SCREENSHOT_DIR")
        if folder:
            page.screenshot(path=str(Path(folder) / "415-refused-page.png"), full_page=False)
        assert not AnnouncementDraft.objects.exists()
