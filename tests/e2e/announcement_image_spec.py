"""End-to-end (#1064): an announcement carries one image to the email preview and the guild page.

A lead attaches an image on step 1 and sees its thumbnail, removes it and attaches it again, sees
it in the step 2 email preview, saves the draft (the thumbnail now shows the stored file), sends
the guild announcement, and finds the image on the guild page card. With ``PL_SCREENSHOT_DIR`` set,
the three guidance shots are taken to the standard frame (``tests/e2e/guidance.py``) into
``mockups/screenshots/1064/``: step 1 rings the image field, step 2 is the email preview, step 3
rings the post's image on the guild page. Uploads land in the throwaway MEDIA_ROOT and are served
by the ``serve_media`` fixture, never object storage. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import reverse
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import expect

from membership.models import AnnouncementDraft, GuildAnnouncement, Member
from tests.e2e.guidance import shoot
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MembershipPlanFactory

LEAD_EMAIL = "zine-lead@example.com"
EDITOR = '.pl-rte[data-rte-for="id_body"] .ql-editor'
TITLE = "Call for zine contributions"
SHOTS = Path(__file__).resolve().parents[2] / "mockups" / "screenshots" / "1064"


def _shoot(page, step: int, target: str | None = None) -> None:
    """One guidance shot to the standard frame, taken only when ``PL_SCREENSHOT_DIR`` is set."""
    if os.environ.get("PL_SCREENSHOT_DIR"):
        shoot(page, SHOTS / f"step-{step}.png", target=target)


def _scroll_to(locator, offset: int) -> None:
    """Scroll the page so ``locator`` sits ``offset`` pixels below the top, clear of the sticky bar."""
    locator.evaluate(f"el => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - {offset})")


def _poster() -> dict:
    """A seeded zine poster, made in memory: what a guild lead would attach."""
    width, height = 1200, 675
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):  # navy into teal, top to bottom
        mix = y / height
        draw.line([(0, y), (width, y)], fill=(int(9 + 20 * mix), int(46 + 90 * mix), int(75 + 70 * mix)))
    draw.ellipse((820, -140, 1320, 360), fill=(238, 180, 75))
    draw.ellipse((-160, 470, 260, 890), fill=(240, 138, 62))
    for i in range(6):  # stacked pages, slightly askew
        x = 760 + i * 14
        y = 360 + i * 10
        draw.rectangle((x, y, x + 260, y + 200), fill=(244, 239, 221), outline=(9, 46, 75), width=4)
    big = ImageFont.load_default(size=78)
    small = ImageFont.load_default(size=40)
    draw.text((80, 150), "Call for zine", font=big, fill=(244, 239, 221))
    draw.text((80, 245), "contributions", font=big, fill=(238, 180, 75))
    draw.text((80, 380), "One page each. Art, poems, recipes.", font=small, fill=(244, 239, 221))
    draw.text((80, 440), "Pages due Friday.", font=small, fill=(244, 239, 221))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return {"name": "zine-call.png", "mimeType": "image/png", "buffer": buffer.getvalue()}


def describe_an_announcement_with_an_image():
    def it_attaches_previews_saves_sends_and_shows_the_image(live_server, page, login_via_code, serve_media):
        MembershipPlanFactory()
        guild = GuildFactory(name="Zine Guild")
        reader_user = get_user_model().objects.create_user(username="reader@example.com", email="reader@example.com")
        reader = Member.objects.get(user=reader_user)
        reader.status = Member.Status.ACTIVE
        reader.save(update_fields=["status"])
        GuildMembershipFactory(guild=guild, member=reader)

        login_via_code(LEAD_EMAIL)
        guild.guild_lead = Member.objects.get(user__username=LEAD_EMAIL)
        guild.save(update_fields=["guild_lead"])

        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{live_server.url}{reverse('hub_compose')}?audience=guild:{guild.pk}")
        page.locator("#id_subject").fill(TITLE)
        page.locator(EDITOR).fill("Send us one page for the winter zine by Friday.")

        field = page.locator("[data-compose-image]")
        thumb = field.locator("[data-compose-image-thumb]")
        zone = field.locator("[data-compose-image-zone]")
        expect(zone).to_be_visible()
        expect(thumb).to_be_hidden()

        # Attach, remove, attach again: the thumbnail follows each choice.
        page.locator("#id_image").set_input_files(_poster())
        expect(thumb).to_be_visible()
        expect(thumb).to_have_attribute("src", re.compile(r"^data:image/png;base64,"))
        expect(zone).to_be_hidden()
        field.get_by_role("button", name="Remove Image").click()
        expect(thumb).to_be_hidden()
        expect(zone).to_be_visible()
        page.locator("#id_image").set_input_files(_poster())
        expect(thumb).to_be_visible()
        _scroll_to(page.locator('.pl-rte[data-rte-for="id_body"]'), 120)
        expect(field).to_be_in_viewport(ratio=1)
        _shoot(page, 1, target="announcements.image")

        # Step 2: the email preview shows it, from the file just chosen.
        page.get_by_role("tab", name="2. Preview & send").click()
        email_image = page.frame_locator("#compose-preview iframe").locator("[data-announcement-image]")
        expect(email_image).to_be_visible()
        expect(email_image).to_have_attribute("alt", TITLE)
        _scroll_to(page.locator(".pl-wizard-preview"), 90)
        # Inside the email, the headline to the top, so the whole image sits under it in the frame.
        email_image.evaluate(
            "img => img.ownerDocument.defaultView.scrollTo(0,"
            " img.ownerDocument.querySelector('h2').getBoundingClientRect().top - 24)"
        )
        _shoot(page, 2)

        # Save draft stores it; the field swaps back with the stored file and an empty input.
        page.get_by_role("tab", name="1. Compose").click()
        page.locator("[data-compose-save-draft]").first.click()
        expect(thumb).to_have_attribute("src", re.compile(r"/media/announcements/"))
        draft = AnnouncementDraft.objects.get(guild=guild)
        assert draft.image.name.startswith("announcements/")
        assert page.locator("#id_image").evaluate("input => input.files.length") == 0

        # Send it; the post carries the stored file to the guild page.
        page.get_by_role("tab", name="2. Preview & send").click()
        page.get_by_role("button", name="Send email", exact=True).click()
        with page.expect_navigation():
            page.get_by_role("button", name="Yes, send it").click()
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_announcements')}?tab=sent")
        post = GuildAnnouncement.objects.get(guild=guild)
        assert post.image.name == draft.image.name

        page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}")
        card = page.locator(f'[data-guild-announcement="{post.pk}"]')
        card_image = card.locator("[data-announcement-image]")
        _scroll_to(card, 110)
        expect(card_image).to_be_visible()
        expect(card_image).to_have_attribute("alt", TITLE)
        expect(card_image).to_have_attribute("data-guidance-target", "guild.announcement-image")
        page.wait_for_function(
            "img => img.complete && img.naturalWidth > 0", arg=card_image.element_handle(), timeout=5000
        )
        _shoot(page, 3, target="guild.announcement-image")
