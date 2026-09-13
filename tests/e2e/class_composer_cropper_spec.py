"""End-to-end: the hero cropper on the composer's Photos step (issue #368, item 4).

Cropper.js measures its mount when it initialises, and the Photos pane is hidden until
the host opens it, so the Python suite cannot see what the browser does: whether the
crop frame appears at all, at what size, whether it survives a trip to another step,
whether an untouched frame keeps ``hero_crop`` empty, whether the crop a host drags is
the crop the saved class and its card frames carry, and whether a fresh photo gets a
frame in both composer modes. This drives the real ``static/js/hero_cropper.js``,
Cropper.js from its CDN, and both inline upload scripts in
``templates/classes/_components/hero_image_field.html``. Needs the network for the CDN.
Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlsplit

import pytest
from django.conf import settings
from django.urls import reverse
from PIL import Image
from playwright.sync_api import expect

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

EMAIL = "cropper-teacher@example.com"
FRAME = ".cropper-container"
CROP_INPUT = "#id_hero_crop"
PREVIEW = "[data-hero-cropper-preview]"
CARD_PHOTOS = ".pl-card-focus__frame .cls-img"
# Cropper.js sizes a mount it cannot measure (a display:none pane) at 200x100 and never
# grows it; a frame that mounted on screen is a good deal wider than that.
COLLAPSED_WIDTH = 200


@pytest.fixture
def cross_origin_media():
    """A second origin serving ``MEDIA_ROOT`` with no CORS header, the way the R2 bucket does.

    A real socket on 127.0.0.1 (the live server is on localhost, a different origin to
    the browser), not a Playwright route: a routed reply is accepted cross origin, so the
    block production hits only reproduces against a real response with no
    ``Access-Control-Allow-Origin``. Yields the origin; point ``MEDIA_URL`` at it.
    """
    root = Path(settings.MEDIA_ROOT)

    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, path: str) -> str:
            return str(root / unquote(urlsplit(path).path.removeprefix("/media/")))

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _seed_instructor() -> Member:
    MembershipPlanFactory()  # so the user signal can provision the member the instructor factory then updates
    user = UserFactory(username=EMAIL)
    return cast(
        Member, InstructorFactory(user=user, full_legal_name="Cropper Teacher", instructor_slug="cropper-teacher")
    )


def _seed_draft_with_square_photo(instructor: Member) -> ClassOffering:
    """A ready draft with a square hero photo.

    Square on purpose: a 16:9 frame on a square photo has room to move, so a drag
    changes the crop. On a 16:9 photo the frame fills the image and cannot move.
    """
    return cast(
        ClassOffering,
        ClassOfferingFactory(
            instructor=instructor,
            status=ClassOffering.Status.DRAFT,
            ready=True,
            image__width=1200,
            image__height=1200,
        ),
    )


def _png(path: Path, width: int, height: int) -> Path:
    Image.new("RGB", (width, height), (40, 90, 160)).save(path, "PNG")
    return path


def _open_photos_step(page, live_server, url_name: str, **kwargs) -> None:
    page.goto(f"{live_server.url}{reverse(url_name, kwargs=kwargs)}")
    page.locator('[data-step-tab="2"]').click()


def _percentages(object_position: str) -> tuple[float, float]:
    """``"50.0% 68.8%"`` -> ``(50.0, 68.8)``."""
    x, y = (float(part.rstrip("%")) for part in object_position.split())
    return x, y


def _width(page, selector: str) -> float:
    box = page.locator(selector).bounding_box()
    assert box is not None, f"{selector} has no box"
    return box["width"]


def _wait_ready(page) -> None:
    """Block until the cropper on the current preview img has built its frame."""
    page.wait_for_function(
        f"() => {{ const img = document.querySelector('{PREVIEW}'); return !!(img && img.cropper && img.cropper.ready); }}"
    )


def _drag_frame_down(page, pixels: int) -> None:
    """Drag the crop frame by its face, the way a host does."""
    face = page.locator(".cropper-face")
    expect(face).to_be_visible()
    # Centre it in the viewport first, instantly: bounding_box() does not scroll, and the
    # hub's `html { scroll-behavior: smooth }` would still be animating when the box is
    # read, so the press would land on the page under it instead of the frame.
    face.evaluate("el => el.scrollIntoView({ block: 'center', behavior: 'instant' })")
    box = face.bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + pixels, steps=6)
    page.mouse.up()


def describe_hero_cropper():
    def it_mounts_a_full_width_frame_on_first_reveal_and_keeps_it_on_every_revisit(
        live_server, page, login_via_code, serve_media
    ):
        offering = _seed_draft_with_square_photo(_seed_instructor())
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)

        frame = page.locator(FRAME)
        expect(frame).to_be_visible()
        first_width = _width(page, FRAME)
        assert first_width > COLLAPSED_WIDTH
        # The frame fills the banner pane it was mounted in.
        assert first_width == pytest.approx(_width(page, "#hero-preview"), abs=1)
        # Nobody dragged anything, so nothing was written.
        expect(page.locator(CROP_INPUT)).to_have_value("")

        page.locator('[data-step-tab="3"]').click()
        expect(frame).to_be_hidden()
        page.locator('[data-step-tab="2"]').click()
        expect(frame).to_be_visible()
        assert _width(page, FRAME) == pytest.approx(first_width, abs=1)
        expect(page.locator(CROP_INPUT)).to_have_value("")

    def it_saves_the_crop_a_host_drags_and_the_card_frames_follow_it(live_server, page, login_via_code, serve_media):
        offering = _seed_draft_with_square_photo(_seed_instructor())
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)
        expect(page.locator(FRAME)).to_be_visible()
        expect(page.locator(CARD_PHOTOS).first).to_have_attribute("style", re.compile(r"object-position: 50% 50%"))

        _drag_frame_down(page, 60)
        crop = json.loads(page.locator(CROP_INPUT).input_value())
        assert crop["w"] > 0 and crop["h"] > 0 and crop["y"] > 0

        page.locator('#composer-form button[type="submit"]').click()
        page.wait_for_url(re.compile(r"step=2"))

        offering.refresh_from_db()
        saved = (offering.hero_crop_x, offering.hero_crop_y, offering.hero_crop_w, offering.hero_crop_h)
        assert saved == (crop["x"], crop["y"], crop["w"], crop["h"])
        position = offering.hero_object_position
        assert position != "50% 50%"

        # Back on the Photos step: the frame restores the saved crop, and every card frame
        # (two on this step, the phone one on step 5) shows the photo at the crop's centre,
        # exactly what the catalog will render.
        expect(page.locator(FRAME)).to_be_visible()
        assert json.loads(page.locator(CROP_INPUT).input_value()) == crop
        restored = page.evaluate(f"document.querySelector('{PREVIEW}').cropper.getData(true)")
        assert restored["y"] == pytest.approx(crop["y"], abs=2)
        assert restored["height"] == pytest.approx(crop["h"], abs=2)
        # Step 5 binds the server's string ("50.0% 68.8%"); step 2 binds through
        # card_focus.js, which reads it back to whole percentages ("50% 69%"). Same focal
        # point within a third of a pixel at card size, so compare the computed position.
        centre = _percentages(position)
        for step, frames in ((2, 2), (5, 1)):
            card_photos = page.locator(f'[data-composer-step="{step}"] {CARD_PHOTOS}')
            expect(card_photos).to_have_count(frames)
            for photo in card_photos.all():
                shown = _percentages(photo.evaluate("el => getComputedStyle(el).objectPosition"))
                assert shown == pytest.approx(centre, abs=0.5), (step, shown, centre)

    def it_frames_a_freshly_uploaded_photo_on_a_saved_class(live_server, page, login_via_code, serve_media, tmp_path):
        offering = _seed_draft_with_square_photo(_seed_instructor())
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)
        expect(page.locator(FRAME)).to_be_visible()
        before = page.locator(PREVIEW).get_attribute("src")
        assert before

        page.locator("#hero-file-input").set_input_files(str(_png(tmp_path / "new-hero.png", 900, 600)))

        # The instant upload swaps the photo in, and the frame is rebuilt on the new one.
        expect(page.locator(PREVIEW)).not_to_have_attribute("src", before)
        frame = page.locator(FRAME)
        expect(frame).to_have_count(1)
        expect(frame).to_be_visible()
        assert _width(page, FRAME) > COLLAPSED_WIDTH
        expect(page.locator(CROP_INPUT)).to_have_value("")
        offering.refresh_from_db()
        assert "new-hero" in offering.image.name

    def it_frames_a_photo_picked_before_the_first_save(live_server, page, login_via_code, serve_media, tmp_path):
        _seed_instructor()
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_create")
        expect(page.locator(FRAME)).to_have_count(0)

        page.locator('#hero-upload-zone input[type="file"]').set_input_files(
            str(_png(tmp_path / "first-hero.png", 900, 600))
        )

        frame = page.locator(FRAME)
        expect(frame).to_be_visible()
        assert _width(page, FRAME) > COLLAPSED_WIDTH
        expect(page.locator(CROP_INPUT)).to_have_value("")
        # The card skeleton mirrors the same picked photo, so the two panes agree.
        expect(page.locator(CARD_PHOTOS).first).to_have_attribute("src", re.compile(r"^data:image/png"))

        # A drag writes the box in source pixels; nothing else does.
        _drag_frame_down(page, 30)
        crop = json.loads(page.locator(CROP_INPUT).input_value())
        assert crop["w"] == 900 and crop["h"] == pytest.approx(506, abs=1)

    def it_rebuilds_the_frame_after_a_window_resize_while_the_step_was_hidden(
        live_server, page, login_via_code, serve_media
    ):
        # Cropper's own window resize handler keeps running while the pane is display:none
        # and scales its geometry to nothing (a phone keyboard, a rotation, a zoom on
        # another step all do this), so the reveal has to rebuild from the saved crop.
        offering = _seed_draft_with_square_photo(_seed_instructor())
        login_via_code(EMAIL)
        page.set_viewport_size({"width": 1280, "height": 900})
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)
        expect(page.locator(FRAME)).to_be_visible()
        _drag_frame_down(page, 60)
        crop = json.loads(page.locator(CROP_INPUT).input_value())

        page.locator('[data-step-tab="3"]').click()
        expect(page.locator(FRAME)).to_be_hidden()
        page.set_viewport_size({"width": 1000, "height": 900})
        page.locator('[data-step-tab="2"]').click()

        expect(page.locator(FRAME)).to_be_visible()
        _wait_ready(page)
        assert _width(page, ".cropper-crop-box") > 0
        assert _width(page, FRAME) == pytest.approx(_width(page, "#hero-preview"), abs=1)
        # The drag survived the rebuild, on screen and in the field the form posts.
        assert json.loads(page.locator(CROP_INPUT).input_value()) == crop
        rebuilt = page.evaluate(f"document.querySelector('{PREVIEW}').cropper.getData(true)")
        assert rebuilt["y"] == pytest.approx(crop["y"], abs=2)
        assert rebuilt["height"] == pytest.approx(crop["h"], abs=2)

    def it_mounts_one_frame_after_a_boosted_leave_and_back(live_server, page, login_via_code, serve_media):
        # htmx snapshots the page before a boosted navigation and restores that DOM on Back;
        # a snapshot taken with the frame still mounted came back as a second, stacked frame.
        offering = _seed_draft_with_square_photo(_seed_instructor())
        login_via_code(EMAIL)
        edit_path = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        # Arrive the way a host does, through the boosted link on Manage My Classes.
        page.goto(f"{live_server.url}{reverse('classes:teach_dashboard')}")
        page.locator(f'a[href="{edit_path}"]').first.click()
        page.wait_for_url(re.compile(re.escape(edit_path)))
        page.locator('[data-step-tab="2"]').click()
        expect(page.locator(FRAME)).to_be_visible()
        _wait_ready(page)

        # Leave through the boosted Cancel link, let the new page finish loading, then come
        # back. The wait matters: hub/base.html loads htmx inside <body>, so a boosted swap
        # re-executes it as an async script, and until that copy boots the previous page's
        # copy still owns window.onpopstate with a stale current path; a Back inside that
        # window snapshots the wrong page under the composer's history key. Nobody presses
        # Back before the page has even settled, so the scenario does not either.
        page.locator("#composer-form").get_by_role("link", name="Cancel").click()
        page.wait_for_url(lambda url: edit_path not in url)
        page.wait_for_load_state("networkidle")
        page.go_back()
        page.wait_for_url(re.compile(re.escape(edit_path)))
        page.locator('[data-step-tab="2"]').click()

        expect(page.locator(FRAME)).to_be_visible()
        _wait_ready(page)
        assert page.locator(FRAME).count() == 1
        assert page.locator(PREVIEW).count() == 1
        assert _width(page, FRAME) > COLLAPSED_WIDTH

    def it_frames_a_photo_served_from_another_origin_with_no_cors_header(
        live_server, page, login_via_code, cross_origin_media, settings
    ):
        # Production serves uploads from R2: another origin, no CORS headers. Cropper.js's
        # default checkCrossOrigin fetched its working copy of such a photo with
        # crossorigin="anonymous" and a cache-busting ?timestamp=, the browser refused it,
        # and the frame never appeared (issue #379). The cropper only ever reads the crop
        # box, never pixels, so a plain img load is all it needs.
        offering = _seed_draft_with_square_photo(_seed_instructor())
        settings.MEDIA_URL = f"{cross_origin_media}/media/"
        assert offering.hero_image_url.startswith(cross_origin_media), offering.hero_image_url
        requests: list[str] = []
        page.on("request", lambda request: requests.append(request.url))
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)

        frame = page.locator(FRAME)
        expect(frame).to_be_visible()
        _wait_ready(page)
        assert page.locator(FRAME).count() == 1
        assert _width(page, FRAME) > COLLAPSED_WIDTH
        busted = [url for url in requests if "timestamp=" in url]
        assert not busted, busted
