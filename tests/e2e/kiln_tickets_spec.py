"""End-to-end: a Ceramics Guild member files a glaze ticket and a bisque copy of it (#691).

In a real browser on a phone sized viewport: the sidebar's Kiln Tickets entry, the New ticket
form's branches (Alpine shows and hides them), a photo picked through the camera input
(``static/js/kiln_ticket_form.js`` previews it and enables Submit), the kind flag notice, then
My Tickets with the ticket In the queue. "Make another like this" carries every answer but
the photo and the Cone 6 confirmations into a bisque ticket. Waits are on what the page shows,
never a snapshot after it. Set ``CAPTURE_691_SCREENSHOTS=1`` to write the PR screenshots to
``mockups/screenshots/`` (or ``CAPTURE_691_DIR``). Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import reverse
from PIL import Image
from playwright.sync_api import expect

from core.models import SiteConfiguration
from kiln.models import KilnFlag, KilnTicket
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MembershipPlanFactory

EMAIL = "kiln-maker@example.com"
SHOTS = Path(os.environ.get("CAPTURE_691_DIR") or Path(__file__).resolve().parents[2] / "mockups" / "screenshots")
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


def _jpeg() -> dict:
    buffer = io.BytesIO()
    Image.new("RGB", (900, 1200), (110, 45, 77)).save(buffer, format="JPEG")
    return {"name": "bowls.jpg", "mimeType": "image/jpeg", "buffer": buffer.getvalue()}


def _capture(page, name: str) -> None:
    if os.environ.get("CAPTURE_691_SCREENSHOTS"):
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / name), full_page=True)


def _add_photo(page) -> None:
    page.locator("[data-kiln-photo-add] input[type=file]").set_input_files(_jpeg())
    expect(page.locator("[data-kiln-new-photo]")).to_have_count(1)


def describe_filing_kiln_tickets():
    def it_files_a_glaze_ticket_and_a_bisque_copy_into_the_queue(live_server, page, login_via_code, serve_media):
        MembershipPlanFactory()
        config = SiteConfiguration.load()
        config.kiln_tickets_open = True  # the launch switch; off by default until the crew screens ship
        config.save(update_fields=["kiln_tickets_open"])
        guild = GuildFactory(name="Ceramics Guild", slug="ceramics-guild")
        login_via_code(EMAIL)
        member = get_user_model().objects.get(username=EMAIL).member
        GuildMembershipFactory(guild=guild, member=member)

        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{live_server.url}{reverse('hub_community_calendar')}")
        page.get_by_role("button", name="Toggle sidebar").click()
        page.locator('.hub-sidebar a[data-nav="kiln"]').click()
        expect(page.get_by_role("heading", level=1)).to_have_text("Kiln Tickets")

        page.get_by_role("link", name="New ticket").click()
        expect(page.get_by_role("heading", level=1)).to_have_text("New Ticket")
        submit = page.get_by_role("button", name="Submit ticket")
        expect(submit).to_be_disabled()
        expect(page.locator('[data-branch="glaze"]')).to_be_hidden()

        # Glaze, with a photo, three pieces of an Other clay.
        page.locator('label.pl-radio-option:has(input[name="firing_type"][value="glaze"])').click()
        expect(page.locator('[data-branch="glaze"]')).to_be_visible()
        expect(page.locator('[data-branch="bisque"]')).to_be_hidden()
        _add_photo(page)
        expect(submit).to_be_enabled()
        page.get_by_label("Height", exact=True).fill("4")
        page.get_by_label("Width", exact=True).fill("6")
        page.get_by_label("Length", exact=True).fill("6")
        page.get_by_label("Quantity of identical pieces").fill("3")
        page.get_by_label("Type of clay").select_option("other")
        expect(page.locator('[data-follow="clay-other"]')).to_be_visible()
        page.get_by_label("Which clay?").fill("Standard 266 Dark Brown")
        page.locator("label.pl-toggle:has(#id_clay_other_cone6)").click()

        # Studio glazes, glaze near the bottom, stilts on.
        page.locator('label.pl-radio-option:has(input[name="glaze_studio"])').click()
        expect(page.locator('[data-follow="studio-glazes"]')).to_be_visible()
        page.locator("label.pl-chip", has_text="Ritual Clear").click()
        page.locator('label.pl-radio-option:has(input[name="bottom_free_of_glaze"][value="no"])').click()
        expect(page.locator('[data-follow="stilts"]')).to_be_visible()
        page.locator('label.pl-radio-option:has(input[name="stilts_added"][value="yes"])').click()
        page.locator("label.pl-toggle:has(#id_glaze_cone6)").click()
        notice = page.locator("[data-kiln-flag-notice]")
        expect(notice).to_be_visible()
        expect(notice).to_contain_text("Your clay is not on the studio list.")
        expect(notice).to_contain_text("you added stilts")
        assert page.evaluate(NO_SIDEWAYS_SCROLL)
        _capture(page, "kiln-tickets-691-new-ticket-phone.png")

        submit.click()
        expect(page).to_have_url(f"{live_server.url}{reverse('kiln:mine')}")
        glaze = KilnTicket.objects.get(maker=member)
        queued = page.locator('[data-group="submitted"]')
        expect(queued).to_contain_text(f"Ticket {glaze.pk} · Glaze · 3 pieces")
        expect(queued.locator(".pl-kiln-status")).to_have_text("In the queue")
        expect(queued).to_contain_text("The crew will take a look: other clay, glaze near the bottom")
        assert set(glaze.flags.values_list("kind", flat=True)) == {
            KilnFlag.Kind.OTHER_CLAY,
            KilnFlag.Kind.GLAZE_ON_BOTTOM,
        }

        # Make another like this: the answers come along, the photo and confirmations do not.
        queued.get_by_role("link", name="Make another like this").click()
        expect(page.locator(".pl-kiln-copied")).to_contain_text(f"Copied from ticket {glaze.pk}.")
        expect(page.get_by_label("Which clay?")).to_have_value("Standard 266 Dark Brown")
        expect(page.locator("#id_clay_other_cone6")).not_to_be_checked()
        expect(page.get_by_role("button", name="Submit ticket")).to_be_disabled()
        page.locator('label.pl-radio-option:has(input[name="firing_type"][value="bisque"])').click()
        expect(page.locator('[data-branch="bisque"]')).to_be_visible()
        _add_photo(page)
        page.locator('label.pl-radio-option:has(input[name="walls_under_inch"][value="yes"])').click()
        page.locator("label.pl-toggle:has(#id_clay_other_cone6)").click()
        page.get_by_role("button", name="Submit ticket").click()

        expect(page).to_have_url(f"{live_server.url}{reverse('kiln:mine')}")
        expect(page.locator('[data-group="submitted"] .pl-kiln-row')).to_have_count(2)
        bisque = KilnTicket.objects.exclude(pk=glaze.pk).get(maker=member)
        expect(queued).to_contain_text(f"Ticket {bisque.pk} · Bisque · 3 pieces")
        assert bisque.status == KilnTicket.Status.SUBMITTED and bisque.photos.count() == 1
        assert page.evaluate(NO_SIDEWAYS_SCROLL)
        expect(page.locator(".hub-sidebar")).to_have_class(re.compile("hub-sidebar--closed"))
        _capture(page, "kiln-tickets-691-my-tickets-phone.png")
