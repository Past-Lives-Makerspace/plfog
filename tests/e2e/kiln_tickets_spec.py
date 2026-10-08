"""End-to-end: kiln tickets for the Ceramics Guild (#691).

Part 1: a member files a glaze ticket and a bisque copy of it. Part 2: the crew loads the kiln
and the maker and crew talk on the ticket.

In a real browser on a phone sized viewport: the sidebar's Kiln Tickets entry, the New ticket
form's branches (Alpine shows and hides them), a photo picked through the camera input
(``static/js/kiln_ticket_form.js`` previews it and enables Submit), the kind flag notice, then
My Tickets with the ticket In the queue. "Make another like this" carries every answer but
the photo and the Cone 6 confirmations into a bisque ticket. On Load the Kiln the crew filter the
tiles, cannot tick the other firing's tile, tick one glaze ticket and confirm; the maker then sees
it In the kiln and answers the crew's message. Waits are on what the page shows, never a snapshot
after it. Every spec makes the guild, list options and members it uses, because an earlier
live_server spec flushes the tables. Set ``CAPTURE_691_SCREENSHOTS=1`` to write the PR screenshots to
``mockups/screenshots/`` (or ``CAPTURE_691_DIR``). Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import io
import os
import re
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import reverse
from PIL import Image
from playwright.sync_api import expect

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from core.models import SiteConfiguration
from kiln.models import ClayOption, GlazeOption, KilnFlag, KilnTicket
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
)

EMAIL = "kiln-maker@example.com"
CREW_EMAIL = "kiln-crew@example.com"
SHOTS = Path(os.environ.get("CAPTURE_691_DIR") or Path(__file__).resolve().parents[2] / "mockups" / "screenshots")
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


def _jpeg(color: tuple[int, int, int] = (110, 45, 77)) -> dict:
    buffer = io.BytesIO()
    Image.new("RGB", (900, 1200), color).save(buffer, format="JPEG")
    return {"name": "bowls.jpg", "mimeType": "image/jpeg", "buffer": buffer.getvalue()}


def _open_the_kiln() -> None:
    config = SiteConfiguration.load()
    config.kiln_tickets_open = True
    config.save(update_fields=["kiln_tickets_open"])


def _queued_ticket(maker, firing_type: str, color: tuple[int, int, int], **answers) -> KilnTicket:
    """A ticket already in the queue with a real photo, filed by ``maker``."""
    clay, _ = ClayOption.objects.get_or_create(name="G-Mix 6", archived_at=None)
    ticket = KilnTicket.objects.create(
        maker=maker, firing_type=firing_type, clay=clay, height_in=4, width_in=6, length_in=6, **answers
    )
    photo = _jpeg(color)
    ticket.add_photo(SimpleUploadedFile("pot.jpg", photo["buffer"], content_type="image/jpeg"))
    ticket.submit()
    return ticket


def _capture(page, name: str) -> None:
    if os.environ.get("CAPTURE_691_SCREENSHOTS"):
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / name), full_page=True)


def _clear_of_the_feedback_bubble(page, button) -> None:
    """The sticky action bar's button and the fixed feedback bubble must not overlap (#695 review)."""
    page.evaluate("window.scrollTo(0, 0)")
    fab = page.locator(".hub-feedback-fab")
    expect(fab).to_be_visible()
    a, b = button.bounding_box(), fab.bounding_box()
    assert a is not None and b is not None
    apart = (
        a["x"] + a["width"] <= b["x"]
        or b["x"] + b["width"] <= a["x"]
        or a["y"] + a["height"] <= b["y"]
        or b["y"] + b["height"] <= a["y"]
    )
    assert apart, f"Submit {a} sits under the feedback bubble {b}"


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
        # An earlier live_server spec flushes the tables, seeded lists included, so make the one glaze this picks.
        GlazeOption.objects.get_or_create(name="Ritual Clear", archived_at=None)
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
        _clear_of_the_feedback_bubble(page, submit)

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


def describe_loading_the_kiln():
    def it_loads_one_ticked_glaze_ticket_and_the_maker_sees_it_in_the_kiln(
        live_server, page, login_via_code, serve_media
    ):
        MembershipPlanFactory()
        _open_the_kiln()
        guild = GuildFactory(name="Ceramics Guild", slug="ceramics-guild")
        glaze_option, _ = GlazeOption.objects.get_or_create(name="Ritual Clear", archived_at=None)

        # The maker signs in first, so their account is the one the login flow makes.
        login_via_code(EMAIL)
        maker = get_user_model().objects.get(username=EMAIL).member
        maker.preferred_name = "Maya Okafor"
        maker.save(update_fields=["preferred_name"])
        GuildMembershipFactory(guild=guild, member=maker)
        bowls = _queued_ticket(
            maker, "glaze", (110, 45, 77), glaze_studio=True, bottom_free_of_glaze=True, glaze_cone6=True
        )
        bowls.studio_glazes.add(glaze_option)
        mug = _queued_ticket(
            maker,
            "glaze",
            (163, 93, 44),
            clay_other=True,
            clay_other_name="Standard 266",
            clay_other_cone6=True,
            bottom_free_of_glaze=True,
            glaze_cone6=True,
        )
        vase = _queued_ticket(maker, "bisque", (215, 201, 178), walls_under_inch=True)
        KilnTicket.objects.filter(pk=bowls.pk).update(submitted_at=timezone.now() - timedelta(days=4))
        page.context.clear_cookies()

        login_via_code(CREW_EMAIL)
        crew = get_user_model().objects.get(username=CREW_EMAIL).member
        crew.preferred_name = "Dana Reyes"
        crew.save(update_fields=["preferred_name"])
        GuildStaffMembershipFactory(guild=guild, member=crew)

        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server.url}{reverse('kiln:mine')}")
        page.locator('[data-kiln-tab="load"]').click()
        expect(page.locator("[data-kiln-load]")).to_be_visible()
        tile = page.locator("[data-tile]")
        expect(tile).to_have_count(3)
        # Longest wait first, so the firing starts as glaze and the bisque tile is held back.
        expect(page.locator("[data-kiln-firing-name]")).to_have_text("Glaze firing 1")
        expect(tile.first).to_have_attribute("data-ticket", str(bowls.pk))
        expect(page.locator(f'[data-ticket="{mug.pk}"]')).to_contain_text("Other clay")
        expect(page.locator(f'[data-ticket="{vase.pk}"]')).to_be_hidden()

        # Filters: All shows every tile, Flagged only the flagged one, Bisque only bisque.
        page.locator("label.pl-chip", has_text="All 3").click()
        expect(tile.filter(visible=True)).to_have_count(3)
        vase_box = page.locator(f'[data-ticket="{vase.pk}"] input[name="tickets"]')
        expect(vase_box).to_be_disabled()
        page.locator("label.pl-chip", has_text="Flagged 1").click()
        expect(tile.filter(visible=True)).to_have_count(1)
        expect(page.locator(f'[data-ticket="{mug.pk}"]')).to_be_visible()
        page.locator("label.pl-chip", has_text="Flagged 1").click()
        page.locator("label.pl-chip", has_text="Bisque 1").click()
        expect(tile.filter(visible=True)).to_have_count(1)
        expect(page.locator(f'[data-ticket="{vase.pk}"]')).to_be_visible()
        page.locator("label.pl-chip", has_text="Glaze 2").click()
        expect(tile.filter(visible=True)).to_have_count(2)

        # Tick the bowls: the tile says Loaded in words, and the bar counts it.
        confirm = page.get_by_role("button", name="Confirm loaded")
        expect(confirm).to_be_disabled()
        page.locator(f'[data-ticket="{bowls.pk}"] .pl-kiln-tile__photo').click()
        expect(page.locator(f'[data-ticket="{bowls.pk}"] input[name="tickets"]')).to_be_checked()
        expect(page.locator(f'[data-ticket="{bowls.pk}"] .pl-kiln-tile__state')).to_be_visible()
        expect(page.locator("[data-kiln-ticked]")).to_have_text("1 ticket ticked")
        expect(confirm).to_be_enabled()
        assert page.evaluate(NO_SIDEWAYS_SCROLL)
        _capture(page, "kiln-tickets-691-load-desktop.png")

        # Switching to bisque unticks the glaze tile and changes the firing's name; back again.
        page.locator(".pl-kiln-seg label", has_text="Bisque").click()
        expect(page.locator("[data-kiln-firing-name]")).to_have_text("Bisque firing 1")
        expect(page.locator(f'[data-ticket="{bowls.pk}"] input[name="tickets"]')).not_to_be_checked()
        expect(confirm).to_be_disabled()
        page.locator(".pl-kiln-seg label", has_text="Glaze").click()
        expect(page.locator("[data-kiln-firing-name]")).to_have_text("Glaze firing 1")

        # A phone loads the page at its own width (the desktop sidebar would stay open on a resize).
        page.set_viewport_size({"width": 390, "height": 844})
        page.reload()
        expect(page.locator(".hub-sidebar")).not_to_be_in_viewport()
        page.locator(f'[data-ticket="{bowls.pk}"] .pl-kiln-tile__photo').click()
        expect(confirm).to_be_enabled()
        expect(page.locator(f'[data-ticket="{bowls.pk}"]')).to_contain_text("Maya Okafor")
        assert page.evaluate(NO_SIDEWAYS_SCROLL)
        _capture(page, "kiln-tickets-691-load-phone.png")
        _clear_of_the_feedback_bubble(page, confirm)

        confirm.click()
        expect(page.locator(".plt-msg", has_text="Glaze firing 1 is loaded with 1 ticket.")).to_be_visible()
        expect(page.locator("[data-tile]")).to_have_count(2)
        assert KilnTicket.objects.get(pk=bowls.pk).status == KilnTicket.Status.LOADED
        assert KilnTicket.objects.get(pk=mug.pk).status == KilnTicket.Status.SUBMITTED
        assert KilnTicket.objects.get(pk=vase.pk).status == KilnTicket.Status.SUBMITTED

        # The crew asks the maker something from the ticket.
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server.url}{reverse('kiln:detail', args=[bowls.pk])}")
        expect(page.get_by_role("heading", level=1)).to_contain_text(f"Ticket {bowls.pk}")
        page.get_by_label(f"Message {maker.display_name}").fill("They are on the middle shelf. Lovely glaze!")
        page.locator("[data-kiln-thread]").get_by_role("button", name="Send").click()
        expect(page.locator("[data-kiln-thread] .pl-kiln-msg")).to_have_count(1)
        page.context.clear_cookies()

        # The maker sees it In the kiln, reads the message and answers.
        page.set_viewport_size({"width": 390, "height": 844})
        login_via_code(EMAIL)
        page.goto(f"{live_server.url}{reverse('kiln:mine')}")
        loaded = page.locator('[data-group="loaded"]')
        expect(loaded).to_contain_text(f"Ticket {bowls.pk}")
        expect(loaded.locator(".pl-kiln-status")).to_have_text("In the kiln")
        loaded.get_by_role("link", name=re.compile(f"Ticket {bowls.pk}")).click()
        thread = page.locator("[data-kiln-thread]")
        expect(thread).to_contain_text("They are on the middle shelf.")
        expect(thread).to_contain_text("Kiln crew")
        page.get_by_label("Reply to the crew").fill("Thank you! Can't wait to see them.")
        thread.get_by_role("button", name="Send").click()
        expect(page.locator("[data-kiln-thread] .pl-kiln-msg")).to_have_count(2)
        expect(page.locator("[data-kiln-timeline]")).to_contain_text("by Dana Reyes in Glaze firing 1")
        expect(page.locator(".hub-sidebar")).to_have_class(re.compile("hub-sidebar--closed"))
        expect(page.locator(".hub-sidebar")).not_to_be_in_viewport()  # the drawer has finished sliding away
        assert page.evaluate(NO_SIDEWAYS_SCROLL)
        page.evaluate("window.scrollTo(0, 0)")
        _capture(page, "kiln-tickets-691-thread-phone.png")
