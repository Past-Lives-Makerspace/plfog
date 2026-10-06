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

import io
import json
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse, urlsplit

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
SHAPE_PICKER = "[data-hero-crop-shape]"
# The class page caps the photo's height (static/css/cms-public.css, the hero's ::before sizer).
HERO_CAP = 700
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


def _seed_draft_with_square_photo(instructor: Member, **fields) -> ClassOffering:
    """A ready draft with a square hero photo.

    Square on purpose: a 16:9 frame on a square photo has room to move, so a drag
    changes the crop. On a 16:9 photo the frame fills the image and cannot move.
    ``fields`` can carry the four hero_crop_* columns to seed a saved box, which the save
    cuts into a cropped copy (issue #547).
    """
    return cast(
        ClassOffering,
        ClassOfferingFactory(
            instructor=instructor,
            status=ClassOffering.Status.DRAFT,
            ready=True,
            image__width=1200,
            image__height=1200,
            **fields,
        ),
    )


# The box Cropper draws by itself on the square photo (full width, 16:9, centred), so a drag
# from it moves the same distance as on a photo with no box.
CENTRED_BOX = {"hero_crop_x": 0, "hero_crop_y": 262, "hero_crop_w": 1200, "hero_crop_h": 675}


def _png(path: Path, width: int, height: int) -> Path:
    Image.new("RGB", (width, height), (40, 90, 160)).save(path, "PNG")
    return path


def _png_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (160, 90, 40)).save(buf, "PNG")
    return buf.getvalue()


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


def _boxed_geometry(photo) -> tuple[float, float, float, float, float]:
    """A boxed frame photo: its frame's width and height, the photo's laid out width, and its left and top offsets in the frame."""
    return tuple(
        photo.evaluate(
            "el => { const frame = el.closest('.cls-media').getBoundingClientRect();"
            " const img = el.getBoundingClientRect();"
            " return [frame.width, frame.height, img.width, img.left - frame.left, img.top - frame.top]; }"
        )
    )


def _expected_offset(box_edge: float, box_size: float, frame_size: float, scale: float, focus: float = 50) -> float:
    """Where card_focus.js puts a boxed photo's edge on one axis: the box's edge meets the frame's,
    less the share of the box's overflow the card focus (50% while the sliders are untouched) picks.
    Zero when the scaled box is exactly the frame's size on that axis, so a sign check is not enough."""
    return -(box_edge * scale) - (box_size * scale - frame_size) * focus / 100


def _boxed_offsets(photo) -> tuple[float, float]:
    """A boxed frame photo's left and top offsets inside its frame."""
    return tuple(
        photo.evaluate(
            "el => { const frame = el.closest('.cls-media').getBoundingClientRect();"
            " const img = el.getBoundingClientRect();"
            " return [img.left - frame.left, img.top - frame.top]; }"
        )
    )


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
        # The saved box is cut into a copy (#547), and every card shows that copy centred.
        assert offering.hero_cropped
        position = offering.hero_object_position
        assert position == "50% 50%"

        # Back on the Photos step: the frame restores the saved crop, and every card frame
        # (two on this step, the phone one on the Review step) shows the cropped copy, centred,
        # exactly what the catalog will render. The cropper announces nothing on ready, so
        # the box's centre on the original never pulls the frames off it.
        expect(page.locator(FRAME)).to_be_visible()
        assert json.loads(page.locator(CROP_INPUT).input_value()) == crop
        restored = page.evaluate(f"document.querySelector('{PREVIEW}').cropper.getData(true)")
        assert restored["y"] == pytest.approx(crop["y"], abs=2)
        assert restored["height"] == pytest.approx(crop["h"], abs=2)
        # The Review step (6) binds the server's string; step 2 binds through card_focus.js,
        # which reads it back to whole percentages. Compare the computed position.
        centre = _percentages(position)
        for step, frames in ((2, 2), (6, 1)):
            card_photos = page.locator(f'[data-composer-step="{step}"] {CARD_PHOTOS}')
            expect(card_photos).to_have_count(frames)
            for photo in card_photos.all():
                shown = _percentages(photo.evaluate("el => getComputedStyle(el).objectPosition"))
                assert shown == pytest.approx(centre, abs=0.5), (step, shown, centre)

    def it_shows_the_saved_box_as_the_hero_on_the_page_preview(live_server, page, login_via_code, serve_media):
        # Issue #547: saving a box renders a copy cut to it (ClassOffering.hero_cropped), and
        # the page preview's banner shows that copy. The composer keeps its cropper on the
        # original, so the box can be moved again, while its card frames show the copy.
        offering = _seed_draft_with_square_photo(_seed_instructor())
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)
        expect(page.locator(FRAME)).to_be_visible()
        _wait_ready(page)
        _drag_frame_down(page, 60)
        crop = json.loads(page.locator(CROP_INPUT).input_value())

        page.locator('#composer-form button[type="submit"]').click()
        page.wait_for_url(re.compile(r"step=2"))

        offering.refresh_from_db()
        assert "hero-crops/" in offering.hero_cropped.name
        with offering.hero_cropped.open("rb") as handle:
            assert Image.open(handle).size == (crop["w"], crop["h"])
        expect(page.locator(PREVIEW)).to_have_attribute("src", offering.image.url)
        expect(page.locator(CARD_PHOTOS).first).to_have_attribute("src", offering.hero_cropped.url)

        page.goto(f"{live_server.url}{reverse('classes:class_preview', kwargs={'pk': offering.pk})}")
        hero = page.locator(".cp-detail__hero-img")
        expect(hero).to_have_attribute("src", re.compile(r"hero-crops/"))
        expect(hero).to_have_attribute("src", offering.hero_cropped.url)

    def it_moves_the_card_frames_with_the_crop_before_any_save(live_server, page, login_via_code, serve_media):
        # Issue #536, item 4: the cropper announces the crop's centre (hero-crop on window) after
        # every drag and the card focus component follows it, so the laptop and phone frames on
        # this step and the phone frame on Review show the crop the host is dragging, not the
        # saved one. Before, they held the saved value until Save Draft. On a class that already
        # has a cropped copy (#547) the frames show that copy, and the new box is measured on the
        # original, so the first drag swaps every frame to the original before the centre lands.
        offering = _seed_draft_with_square_photo(_seed_instructor(), **CENTRED_BOX)
        assert offering.hero_cropped
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)
        expect(page.locator(FRAME)).to_be_visible()
        _wait_ready(page)
        card_photos = page.locator(CARD_PHOTOS)
        expect(card_photos).to_have_count(3)
        for photo in card_photos.all():
            expect(photo).to_have_attribute("src", offering.hero_cropped.url)

        _drag_frame_down(page, 60)
        crop = json.loads(page.locator(CROP_INPUT).input_value())
        # The photo is 1200x1200: the crop's centre as a percentage of the source image.
        centre = ((crop["x"] + crop["w"] / 2) / 1200 * 100, (crop["y"] + crop["h"] / 2) / 1200 * 100)
        assert centre[1] > 55, centre  # the drag moved the box well below the middle
        natural_w = page.locator(PREVIEW).evaluate("el => el.naturalWidth")
        assert natural_w == 1200
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")

        # Every frame shows the box region, cover fitted, exactly what the saved copy will show:
        # the original, scaled so the box covers the frame and shifted so the box's top left
        # meets the frame's. The Review step's frame has no size until that step is on screen.
        expect(page.locator("[data-card-focus-input]")).to_have_value("")  # the sliders were never touched
        for step, frames in ((2, 2), (6, 1)):
            if step == 6:
                page.locator('[data-step-tab="6"]').click()
            card_photos = page.locator(f'[data-composer-step="{step}"] {CARD_PHOTOS}')
            expect(card_photos).to_have_count(frames)
            for photo in card_photos.all():
                expect(photo).to_be_visible()
                expect(photo).to_have_attribute("src", offering.image.url)
                expect(photo).not_to_have_attribute("data-hero-source", re.compile(r".*"))
                expect(photo).to_have_class(re.compile(r"pl-card-focus__img--boxed"))
                frame_w, frame_h, shown_w, left, top = _boxed_geometry(photo)
                scale = max(frame_w / crop["w"], frame_h / crop["h"])
                assert shown_w == pytest.approx(natural_w * scale, abs=1), (step, shown_w, natural_w * scale)
                assert left == pytest.approx(_expected_offset(crop["x"], crop["w"], frame_w, scale), abs=1), (
                    step,
                    left,
                )
                assert top == pytest.approx(_expected_offset(crop["y"], crop["h"], frame_h, scale), abs=1), (step, top)

        # The card focus sliders choose which part of the box shows: moving Up and down shifts
        # the photo inside the frame. The phone frame is the wide one, so there the box is
        # fitted to the frame's width and overflows it vertically; in the laptop frame this
        # box fits the height and the slider has nothing to shift.
        page.locator('[data-step-tab="2"]').click()
        photo = page.locator(f'[data-composer-step="2"] {CARD_PHOTOS}').last
        expect(photo).to_be_visible()
        top_before = _boxed_offsets(photo)[1]
        page.locator('[data-composer-step="2"] .pl-card-focus__range').first.evaluate(
            "el => { el.value = 90; el.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        page.wait_for_function(
            f"before => {{ const imgs = document.querySelectorAll('[data-composer-step=\"2\"] {CARD_PHOTOS}');"
            " const img = imgs[imgs.length - 1];"
            " return img.getBoundingClientRect().top - img.closest('.cls-media').getBoundingClientRect().top !== before; }",
            arg=top_before,
        )
        frame_w, frame_h, _shown_w, _left, top = _boxed_geometry(photo)
        scale = max(frame_w / crop["w"], frame_h / crop["h"])
        assert top < top_before
        assert top == pytest.approx(_expected_offset(crop["y"], crop["h"], frame_h, scale, focus=90), abs=1), top
        # Nothing was saved: the row keeps its box and its copy.
        copy = offering.hero_cropped.name
        offering.refresh_from_db()
        assert offering.hero_crop_box == tuple(CENTRED_BOX.values())
        assert offering.hero_cropped.name == copy

    def it_keeps_the_frames_on_a_saved_focal_point_until_the_host_drags(live_server, page, login_via_code, serve_media):
        # A hero placed with the Adjust tool is a focal point (hero_crop_w 0, x and y as
        # percentages) and the composer seeds hero_crop empty for it, so Cropper mounts its
        # automatic box. That box is nobody's choice: announcing its centre on ready pulled
        # the frames off the saved focal point while the real card stayed on it (PR #539
        # review). Ready announces nothing (since #547 a restored box's copy is centred by
        # the server too); a real drag still moves the frames.
        offering = cast(
            ClassOffering,
            ClassOfferingFactory(
                instructor=_seed_instructor(),
                status=ClassOffering.Status.DRAFT,
                ready=True,
                image__width=1200,
                image__height=1200,
                hero_crop_x=30,
                hero_crop_y=80,
                hero_crop_w=0,
                hero_crop_h=0,
            ),
        )
        assert offering.hero_object_position == "30% 80%"
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)
        expect(page.locator(FRAME)).to_be_visible()
        _wait_ready(page)
        page.wait_for_function("() => document.querySelector('.cropper-crop-box').offsetWidth > 0")
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        # No box was seeded and none was announced: the frames hold the saved focal point.
        expect(page.locator(CROP_INPUT)).to_have_value("")
        for step, frames in ((2, 2), (6, 1)):
            card_photos = page.locator(f'[data-composer-step="{step}"] {CARD_PHOTOS}')
            expect(card_photos).to_have_count(frames)
            for photo in card_photos.all():
                shown = _percentages(photo.evaluate("el => getComputedStyle(el).objectPosition"))
                assert shown == (30.0, 80.0), (step, shown)

        # A drag is a real crop: now the frames on this step show the box region, cover fitted
        # (the Review step's frame is laid out when that step shows).
        _drag_frame_down(page, 60)
        crop = json.loads(page.locator(CROP_INPUT).input_value())
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        for photo in page.locator(f'[data-composer-step="2"] {CARD_PHOTOS}').all():
            expect(photo).to_have_class(re.compile(r"pl-card-focus__img--boxed"))
            frame_w, frame_h, shown_w, left, top = _boxed_geometry(photo)
            scale = max(frame_w / crop["w"], frame_h / crop["h"])
            assert shown_w == pytest.approx(1200 * scale, abs=1)
            assert left == pytest.approx(_expected_offset(crop["x"], crop["w"], frame_w, scale), abs=1), left
            assert top == pytest.approx(_expected_offset(crop["y"], crop["h"], frame_h, scale), abs=1), top

    def it_frames_a_freshly_uploaded_photo_on_a_saved_class(live_server, page, login_via_code, serve_media, tmp_path):
        # Seeded with a box, so the class has a cropped copy and its card frames show it (#547).
        offering = _seed_draft_with_square_photo(_seed_instructor(), **CENTRED_BOX)
        old_copy = offering.hero_cropped.url
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)
        expect(page.locator(FRAME)).to_be_visible()
        before = page.locator(PREVIEW).get_attribute("src")
        assert before
        expect(page.locator(CARD_PHOTOS).first).to_have_attribute("src", old_copy)

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
        # The upload replaced the original and its copy on the server; the frames show the new
        # photo and no longer carry the deleted original for a drag to swap in.
        assert not offering.hero_cropped
        card_photos = page.locator(CARD_PHOTOS)
        expect(card_photos).to_have_count(3)
        for photo in card_photos.all():
            expect(photo).to_have_attribute("src", offering.image.url)
            expect(photo).not_to_have_attribute("data-hero-source", re.compile(r".*"))

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
        # The server hid the picker with no photo; the live cropper reveals it, on Wide.
        expect(page.locator(SHAPE_PICKER)).to_be_visible()
        expect(_shape_radio(page, "wide")).to_be_checked()
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

    def it_withholds_the_frame_on_an_imported_photo_until_a_new_photo_is_uploaded(
        live_server, page, login_via_code, serve_media, tmp_path
    ):
        # A saved box cannot position an imported photo (issue #378), so the composer
        # offers no frame on one and points at Adjust instead. The proxy is same origin,
        # so a routed reply stands in for the old class site and the photo really loads.
        # Routed on the context, not the page: the hub's service worker fetches images
        # itself, and a page route never sees a request the worker makes.
        offering = cast(
            ClassOffering,
            ClassOfferingFactory(
                instructor=_seed_instructor(),
                status=ClassOffering.Status.DRAFT,
                ready=True,
                image="",
                legacy_image_url="https://classes.pastlives.space/sites/default/files/glen.jpg",
            ),
        )
        imported = _png_bytes(1200, 1200)
        page.context.route(
            re.compile(r"/_legacy-image/\?"), lambda route: route.fulfill(content_type="image/png", body=imported)
        )
        page.add_init_script(
            "window.addEventListener('composer-step-shown', e => {"
            " window.__stepsShown = (window.__stepsShown || []).concat(e.detail.step); });"
        )
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)

        # Wait for everything a frame needs: the photo's pixels, the Photos step's
        # reveal, and Cropper.js itself. Where the frame is offered, the mount sets
        # img.cropper synchronously at that point, so its absence now is a real absence.
        page.wait_for_function(
            """() => {
                const img = document.querySelector('#hero-preview img');
                return !!(img && img.complete && img.naturalWidth > 0
                    && (window.__stepsShown || []).includes(2) && window.Cropper);
            }"""
        )
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        mounted = page.evaluate(
            """() => ({
                cropper: !!document.querySelector('#hero-preview img').cropper,
                frames: document.querySelectorAll('.cropper-container').length,
            })"""
        )
        assert mounted == {"cropper": False, "frames": 0}
        expect(page.locator("#hero-legacy-note")).to_be_visible()
        expect(page.locator("#hero-crop-hint")).to_be_hidden()
        # No box, so no shape to choose either.
        expect(page.locator(SHAPE_PICKER)).to_be_hidden()

        page.locator("#hero-file-input").set_input_files(str(_png(tmp_path / "replacement.png", 900, 600)))

        expect(page.locator("#hero-legacy-note")).to_have_count(0)
        expect(page.locator("#hero-crop-hint")).to_be_visible()
        frame = page.locator(f"#hero-preview {FRAME}")
        expect(frame).to_have_count(1)
        expect(frame).to_be_visible()
        # The cropper's ready reveals the picker for the new photo, on Wide.
        expect(page.locator(SHAPE_PICKER)).to_be_visible()
        expect(_shape_radio(page, "wide")).to_be_checked()
        offering.refresh_from_db()
        assert "replacement" in offering.image.name


def _shape_radio(page, shape: str):
    return page.locator(f'input[name="hero_crop_shape"][value="{shape}"]')


def _box_data(page) -> dict[str, float]:
    """Cropper's own box in source pixels, rounded, straight from the instance."""
    return cast(dict[str, float], page.evaluate(f"document.querySelector('{PREVIEW}').cropper.getData(true)"))


def _hero_crops(page) -> int:
    """How many hero-crop announcements the window has seen (counted by the init script below)."""
    return cast(int, page.evaluate("window.__heroCrops"))


def _count_hero_crops(page) -> None:
    page.add_init_script(
        "window.__heroCrops = 0; window.addEventListener('hero-crop', () => { window.__heroCrops += 1; });"
    )


def _drag_corner_in(page, dx: int, dy: int) -> None:
    """Drag the box's bottom right handle up and left by (dx, dy) viewport pixels, the way a host reshapes a free box."""
    handle = page.locator(".cropper-point.point-se")
    expect(handle).to_be_visible()
    handle.evaluate("el => el.scrollIntoView({ block: 'center', behavior: 'instant' })")
    box = handle.bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - dx, y - dy, steps=8)
    page.mouse.up()


def _hero_on_page(page) -> dict[str, Any]:
    """The class page hero: its frame, the ratio it was handed, the img's fit and the rect a contain fit paints."""
    return cast(
        dict[str, Any],
        page.evaluate(
            """() => {
              const hero = document.querySelector('.cp-detail__hero');
              const img = document.querySelector('.cp-detail__hero-img');
              const frame = hero.getBoundingClientRect();
              const box = img.getBoundingClientRect();
              const scale = Math.min(box.width / img.naturalWidth, box.height / img.naturalHeight);
              return {
                width: frame.width,
                height: frame.height,
                ratio: getComputedStyle(hero).getPropertyValue('--cp-hero-ratio').replace(/\\s+/g, ' ').trim(),
                fit: getComputedStyle(img).objectFit,
                natural: {width: img.naturalWidth, height: img.naturalHeight},
                painted: {width: img.naturalWidth * scale, height: img.naturalHeight * scale},
              };
            }"""
        ),
    )


def _seed_draft_with_landscape_photo(instructor: Member) -> ClassOffering:
    """A ready draft with a 1200 by 900 photo: wider than square, so Square has room to slide and Free opens on 4:3."""
    return cast(
        ClassOffering,
        ClassOfferingFactory(
            instructor=instructor,
            status=ClassOffering.Status.DRAFT,
            ready=True,
            image__width=1200,
            image__height=900,
        ),
    )


def describe_crop_shape_picker():
    """The Photos step's Wide, Square and Free radios (the 2026-10-02 instructor round, PR 7).

    The radios are named hero_crop_shape and no form reads them: the shape lives only in
    the box the cropper writes to hero_crop, so the row, the copy and the class page follow
    it through the paths PR 6 built. Only a browser runs Cropper's re-fit, so this drives
    it end to end.
    """

    def it_fits_a_square_box_that_the_row_the_copy_and_the_class_page_all_take(
        live_server, page, login_via_code, serve_media, settings
    ):
        offering = _seed_draft_with_landscape_photo(_seed_instructor())
        _count_hero_crops(page)
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)
        expect(page.locator(FRAME)).to_be_visible()
        _wait_ready(page)

        # No saved box: the picker is up, on Wide, and nothing has been written or announced.
        expect(page.locator(SHAPE_PICKER)).to_be_visible()
        expect(_shape_radio(page, "wide")).to_be_checked()
        expect(page.locator(CROP_INPUT)).to_have_value("")
        assert _hero_crops(page) == 0

        _shape_radio(page, "square").check()

        # Cropper re-fit the box square; the input and the card previews heard about it once.
        box = _box_data(page)
        assert box["width"] == pytest.approx(box["height"], abs=1), box
        assert box["height"] == pytest.approx(900, abs=1), box  # as tall as the photo allows
        crop = json.loads(page.locator(CROP_INPUT).input_value())
        assert crop == {"x": round(box["x"]), "y": round(box["y"]), "w": round(box["width"]), "h": round(box["height"])}
        assert _hero_crops(page) == 1

        page.locator('#composer-form button[type="submit"]').click()
        page.wait_for_url(re.compile(r"step=2"))

        offering.refresh_from_db()
        assert offering.hero_crop_w == offering.hero_crop_h == crop["w"]
        assert (offering.hero_crop_x, offering.hero_crop_y) == (crop["x"], crop["y"])
        with offering.hero_cropped.open("rb") as handle:
            assert Image.open(handle).size == (crop["w"], crop["w"])
        assert offering.hero_aspect_ratio == f"{crop['w']} / {crop['w']}"
        # Back on the Photos step the rebuilt cropper read the square box and checked Square.
        expect(page.locator(FRAME)).to_be_visible()
        _wait_ready(page)
        expect(_shape_radio(page, "square")).to_be_checked()
        restored = _box_data(page)
        assert restored["width"] == pytest.approx(restored["height"], abs=1), restored

        # The class page: the hero takes the box's own numbers as its ratio and paints the
        # whole square copy. At 1366 the column is wider than it is tall, so the 700px cap
        # holds and the square sits pillarboxed in it; at 390 the frame is the column's
        # width and square.
        ClassOffering.objects.filter(pk=offering.pk).update(status=ClassOffering.Status.PUBLISHED)
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
        url = f"{live_server.url}{reverse('classes:public_class_detail', kwargs={'slug': offering.slug})}"
        for width in (1366, 390):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(url)
            page.wait_for_load_state("networkidle")
            page.wait_for_function("() => document.querySelector('.cp-detail__hero-img').naturalWidth > 0")
            hero = _hero_on_page(page)
            assert hero["ratio"] == f"{crop['w']} / {crop['w']}", (width, hero)
            assert hero["fit"] == "contain", width
            assert hero["natural"] == {"width": crop["w"], "height": crop["w"]}, width
            painted = hero["painted"]
            assert painted["width"] == pytest.approx(painted["height"], abs=1), (width, painted)
            assert painted["width"] <= hero["width"] + 0.5 and painted["height"] <= hero["height"] + 0.5, (width, hero)
            expected_height = min(hero["width"], HERO_CAP)
            assert hero["height"] == pytest.approx(expected_height, abs=1), (width, hero)
            assert painted["height"] == pytest.approx(expected_height, abs=1), (width, hero)
            if width == 1366:
                assert painted["width"] < hero["width"] - 100, (width, hero)  # the soft bars at the sides

    def it_saves_the_odd_rectangle_a_host_drags_in_free_and_restores_the_shape(
        live_server, page, login_via_code, serve_media
    ):
        offering = _seed_draft_with_landscape_photo(_seed_instructor())
        _count_hero_crops(page)
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)
        expect(page.locator(FRAME)).to_be_visible()
        _wait_ready(page)

        _shape_radio(page, "free").check()

        # Free opens on the whole photo (4:3, neither wide nor square), written and announced once.
        box = _box_data(page)
        assert (box["width"], box["height"]) == pytest.approx((1200, 900), abs=1), box
        assert json.loads(page.locator(CROP_INPUT).input_value())["h"] == pytest.approx(900, abs=1)
        assert _hero_crops(page) == 1

        # Pull the bottom right corner in: a free box takes any rectangle, and the drag writes
        # it like any other drag (one more announcement).
        _drag_corner_in(page, 40, 150)
        crop = json.loads(page.locator(CROP_INPUT).input_value())
        assert crop["w"] < 1200 and crop["h"] < 900, crop
        ratio = crop["w"] / crop["h"]
        assert abs(ratio / (16 / 9) - 1) > 0.02 and abs(ratio - 1) > 0.02, crop  # an odd rectangle
        assert _hero_crops(page) == 2

        page.locator('#composer-form button[type="submit"]').click()
        page.wait_for_url(re.compile(r"step=2"))

        offering.refresh_from_db()
        assert offering.hero_crop_box == (crop["x"], crop["y"], crop["w"], crop["h"])
        with offering.hero_cropped.open("rb") as handle:
            assert Image.open(handle).size == (crop["w"], crop["h"])
        # Reloaded: the picker shows Free for the odd box, and the box is the saved one.
        expect(page.locator(FRAME)).to_be_visible()
        _wait_ready(page)
        expect(_shape_radio(page, "free")).to_be_checked()
        restored = _box_data(page)
        assert restored["width"] == pytest.approx(crop["w"], abs=2)
        assert restored["height"] == pytest.approx(crop["h"], abs=2)

    def it_shows_wide_for_a_saved_wide_box_and_keeps_an_untouched_box_unwritten(
        live_server, page, login_via_code, serve_media
    ):
        # A box saved before the picker existed is 16:9; it reads as Wide, and until a shape
        # is clicked or the box dragged the input holds exactly what the server rendered.
        offering = _seed_draft_with_square_photo(_seed_instructor(), **CENTRED_BOX)
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)
        expect(page.locator(FRAME)).to_be_visible()
        _wait_ready(page)
        expect(_shape_radio(page, "wide")).to_be_checked()
        rendered = page.locator(CROP_INPUT).input_value()
        assert json.loads(rendered) == {"x": 0, "y": 262, "w": 1200, "h": 675}

        page.locator('[data-step-tab="3"]').click()
        page.locator('[data-step-tab="2"]').click()
        expect(page.locator(FRAME)).to_be_visible()
        _wait_ready(page)
        expect(_shape_radio(page, "wide")).to_be_checked()
        expect(page.locator(CROP_INPUT)).to_have_value(rendered)


def describe_hero_on_a_live_class():
    # The live class edit page (class_form_published.html) renders the same field outside any
    # composer step. Quinlan, 2026-10-05: before, it drew only the gallery, so replacing the
    # banner of a live class took a Request a change to an admin.
    def it_replaces_and_recrops_the_banner_without_a_request(live_server, page, login_via_code, serve_media, tmp_path):
        offering = _seed_draft_with_square_photo(_seed_instructor())
        offering.status = ClassOffering.Status.PUBLISHED
        offering.save(update_fields=["status"])
        login_via_code(EMAIL)
        page.goto(f"{live_server.url}{reverse('classes:teach_class_edit', kwargs={'pk': offering.pk})}")
        expect(page.get_by_text("This class is live.")).to_be_visible()
        expect(page.locator(FRAME)).to_be_visible()
        before = page.locator(PREVIEW).get_attribute("src")

        page.locator("#hero-file-input").set_input_files(str(_png(tmp_path / "live-hero.png", 1200, 1200)))

        expect(page.locator(PREVIEW)).not_to_have_attribute("src", before)
        expect(page.locator(FRAME)).to_have_count(1)
        offering.refresh_from_db()
        assert "live-hero" in offering.image.name
        _wait_ready(page)
        _drag_frame_down(page, 60)
        crop = json.loads(page.locator(CROP_INPUT).input_value())
        assert crop["y"] > 0

        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_text("Class updated.")).to_be_visible()
        offering.refresh_from_db()
        assert (offering.hero_crop_x, offering.hero_crop_y, offering.hero_crop_w, offering.hero_crop_h) == (
            crop["x"],
            crop["y"],
            crop["w"],
            crop["h"],
        )
        assert offering.hero_cropped
        assert offering.status == ClassOffering.Status.PUBLISHED
