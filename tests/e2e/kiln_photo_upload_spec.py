"""End-to-end: kiln photos shrink on the phone and upload one by one (#1246).

A ticket with 11 iPhone camera photos (about 3.8 MB each at 4032x3024, 41.9 MB together) once
posted in one request on Save, over three minutes on a 1.5 Mbps cell uplink. Now each photo is
shrunk in the browser (``static/js/kiln_photo_shrink.js``) and uploads as soon as it is picked
(``static/js/kiln_ticket_form.js``). The bytes are measured on the server (``CONTENT_LENGTH`` of
every upload request), because Chrome's network throttling does not slow a multipart upload.

The service worker is blocked because it fetches requests past ``page.route``, which fails one
upload here. Every spec makes the guild and members it uses, because an earlier live_server spec
flushes the tables. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterator

import pytest
from django.contrib.auth import get_user_model
from django.core.signals import request_started
from django.urls import reverse
from PIL import Image
from playwright.sync_api import expect

from core.models import SiteConfiguration
from kiln.models import KilnReply, KilnTicket
from tests.kiln.factories import KilnTicketFactory
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

PHOTOS = 11
# Over a 1.5 Mbps uplink 8 MB is about 43 s in all, and each photo is its own short request.
BUDGET = 8_000_000
UPLOAD_PATH = re.compile(r"^/kiln/(new|\d+)/photos/$")
# Shrinking a 12 megapixel photo and the first request to a cold live server take a few seconds.
UPLOADED = 30_000


@pytest.fixture
def browser_context_args(browser_context_args):
    """Block the service worker: it fetches requests itself, past ``page.route``."""
    return {**browser_context_args, "service_workers": "block"}


def _camera_jpeg(i: int) -> dict:
    """About 3.8 MB at 4032x3024, the size an iPhone camera pick posts."""
    noise = Image.effect_noise((4032, 3024), 10)
    ramp = Image.linear_gradient("L").resize((4032, 3024))
    buffer = io.BytesIO()
    Image.merge("RGB", (Image.blend(ramp, noise, 0.5), noise, ramp)).save(buffer, "JPEG", quality=90)
    return {"name": f"IMG_{i:04}.jpg", "mimeType": "image/jpeg", "buffer": buffer.getvalue()}


def _warm_the_canvas(page) -> None:
    """Headless Chrome on a machine with no GPU can stall a minute or more on its first canvas use
    (WSL: about 90 s, gone with ``--use-gl=disabled``). Pay that once here, outside every timed wait."""
    page.evaluate(
        "async () => { const c = document.createElement('canvas'); c.width = c.height = 8;"
        " c.getContext('2d').fillRect(0, 0, 4, 4); await new Promise(r => c.toBlob(r, 'image/jpeg')); }"
    )


def _open_the_kiln():
    MembershipPlanFactory()
    config = SiteConfiguration.load()
    config.kiln_tickets_open = True
    config.save(update_fields=["kiln_tickets_open"])
    return GuildFactory(name="Ceramics Guild", slug="ceramics-guild")


@pytest.fixture
def posted_bytes() -> Iterator[dict[str, list[int]]]:
    """The ``CONTENT_LENGTH`` of every POST the live server receives, by path."""
    seen: dict[str, list[int]] = {}

    def record(sender, environ, **kwargs) -> None:
        if environ.get("REQUEST_METHOD") == "POST":
            seen.setdefault(environ["PATH_INFO"], []).append(int(environ.get("CONTENT_LENGTH") or 0))

    request_started.connect(record, weak=False)
    yield seen
    request_started.disconnect(record)


def _upload_sizes(seen: dict[str, list[int]]) -> list[int]:
    return [size for path, sizes in seen.items() if UPLOAD_PATH.match(path) for size in sizes]


def describe_eleven_camera_photos_on_a_new_ticket():
    def it_shrinks_each_one_and_stores_all_eleven_as_they_are_picked(
        live_server, page, login_via_code, serve_media, posted_bytes
    ):
        guild = _open_the_kiln()
        login_via_code("k1246@example.com")
        member = get_user_model().objects.get(username="k1246@example.com").member
        GuildMembershipFactory(guild=guild, member=member)
        files = [_camera_jpeg(i) for i in range(PHOTOS)]
        assert sum(len(f["buffer"]) for f in files) > 40_000_000

        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{live_server.url}{reverse('kiln:new')}")
        _warm_the_canvas(page)
        page.locator(".pl-kiln-photo-pick input[type=file]").set_input_files(files)

        # Each tile says Uploading until its photo is stored, before anything is saved.
        expect(page.locator("[data-kiln-upload-state=uploading]").first).to_contain_text("Uploading")
        expect(page.get_by_role("button", name="Save as draft")).to_be_disabled()
        stored = page.locator("[data-kiln-photo][data-kiln-uploaded]")
        expect(stored).to_have_count(PHOTOS, timeout=120_000)
        expect(page.locator("[data-kiln-new-photo]")).to_have_count(0)

        ticket = KilnTicket.objects.get(maker=member)
        assert ticket.status == KilnTicket.Status.DRAFT
        assert ticket.photos.count() == PHOTOS
        sizes = _upload_sizes(posted_bytes)
        assert len(sizes) == PHOTOS
        assert sum(sizes) < BUDGET, f"{sum(sizes) / 1e6:.1f} MB posted for {PHOTOS} photos"
        assert "/kiln/new/" not in posted_bytes, "Nothing was saved by the form"

        # The first photo started the draft: the address is its edit page and Save writes to it.
        edit = reverse("kiln:edit", args=[ticket.pk])
        assert page.url.endswith(edit)
        expect(stored.first.get_by_text("Cover")).to_be_visible()
        expect(stored.nth(1).get_by_role("button", name="Make cover")).to_be_visible()
        page.get_by_role("button", name="Save as draft").click()
        page.wait_for_url("**/guilds/**")
        assert KilnTicket.objects.filter(maker=member).count() == 1
        assert KilnTicket.objects.get(pk=ticket.pk).photos.count() == PHOTOS
        assert posted_bytes[edit][0] < 100_000, "Save posts the answers, not the photos again"


def describe_a_photo_that_fails_to_upload():
    def it_shows_didnt_upload_and_retry_and_keeps_the_photos_already_stored(
        live_server, page, login_via_code, serve_media
    ):
        guild = _open_the_kiln()
        login_via_code("k1246-retry@example.com")
        member = get_user_model().objects.get(username="k1246-retry@example.com").member
        GuildMembershipFactory(guild=guild, member=member)

        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{live_server.url}{reverse('kiln:new')}")
        _warm_the_canvas(page)
        pick = page.locator(".pl-kiln-photo-pick input[type=file]")
        pick.set_input_files(_camera_jpeg(1))
        stored = page.locator("[data-kiln-photo][data-kiln-uploaded]")
        expect(stored).to_have_count(1, timeout=UPLOADED)
        ticket = KilnTicket.objects.get(maker=member)

        # The connection drops on the second photo: that tile alone fails.
        page.route(f"**{reverse('kiln:photo_add', args=[ticket.pk])}", lambda route: route.abort())
        pick.set_input_files(_camera_jpeg(2))
        failed = page.locator(".pl-kiln-photo--failed")
        expect(failed).to_have_count(1, timeout=UPLOADED)
        expect(failed).to_contain_text("Didn't upload")
        expect(stored).to_have_count(1)
        assert ticket.photos.count() == 1
        expect(page.get_by_role("button", name="Save as draft")).to_be_enabled()

        # Retry once the signal is back stores it.
        page.unroute(f"**{reverse('kiln:photo_add', args=[ticket.pk])}")
        failed.get_by_role("button", name="Retry").click()
        expect(stored).to_have_count(2, timeout=UPLOADED)
        expect(page.locator(".pl-kiln-photo--failed")).to_have_count(0)
        assert ticket.photos.count() == 2


def describe_a_crew_message_photo():
    def it_posts_the_shrunk_copy(live_server, page, login_via_code, serve_media, posted_bytes):
        guild = _open_the_kiln()
        maker = MemberFactory()
        ticket = KilnTicketFactory(maker=maker, ready=True)
        ticket.submit()
        login_via_code("k1246-crew@example.com")
        crew = get_user_model().objects.get(username="k1246-crew@example.com").member
        GuildStaffMembershipFactory(guild=guild, member=crew)

        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{live_server.url}{reverse('kiln:detail', args=[ticket.pk])}")
        _warm_the_canvas(page)
        thread = page.locator("[data-kiln-thread]")
        thread.get_by_role("textbox").fill("Is this the bowl on the top shelf?")
        photo = _camera_jpeg(3)
        pick = thread.locator("[data-kiln-photo-pick]")
        pick.locator("input[capture]").set_input_files(photo)
        expect(pick.locator("[data-kiln-photo-pick-preview] img")).to_be_visible(timeout=UPLOADED)
        assert pick.locator("input[capture]").evaluate("input => input.files[0].size") < len(photo["buffer"]) / 3

        thread.get_by_role("button", name="Send").click()
        expect(thread.locator("[data-kiln-reply-photo] img")).to_be_visible()
        assert KilnReply.objects.get(ticket=ticket).has_photo
        sent = posted_bytes[reverse("kiln:reply", args=[ticket.pk])][0]
        assert sent < 1_500_000, f"{sent / 1e6:.1f} MB posted for one {len(photo['buffer']) / 1e6:.1f} MB photo"
