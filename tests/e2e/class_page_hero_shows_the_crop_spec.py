"""End-to-end: the class page shows exactly what is inside the composer's crop box.

The composer crops a 16:9 box and the server cuts that rectangle into a copy the page
shows (#547). The banner then cover fitted the copy into a frame of another shape, so a
laptop lost the top and bottom of the box and a phone lost its sides. Now the frame takes
the copy's shape and the copy is contain fitted, so the box is the truth. Only a real
browser runs the cropper, the upload and the page layout, so this drives the whole path:
upload a photo on the Photos step, drag the box, save, open the class page at a desktop and
a phone width, and measure what is painted against the copy the save cut. Reuses the
cropper spec's helpers. Needs the network for the Cropper.js CDN. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from django.urls import reverse
from PIL import Image
from playwright.sync_api import Page, expect

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering
from tests.e2e.class_composer_cropper_spec import (
    CROP_INPUT,
    EMAIL,
    FRAME,
    _drag_frame_down,
    _open_photos_step,
    _seed_instructor,
    _wait_ready,
)

CAP = 700
FIELD = (40, 90, 160)
SUBJECT = (235, 140, 30)


def _subject_photo(path: Path) -> Path:
    """A 1200 by 900 photo with its subject off centre: an orange block low and to the right on a blue field.

    Off centre on purpose, so a crop box dragged down takes a different slice of the photo
    than the automatic one, and the match between the box and the page is visible in a
    screenshot. The field is uniform so a pixel read off the page can be checked.
    """
    img = Image.new("RGB", (1200, 900), FIELD)
    img.paste(Image.new("RGB", (320, 240), SUBJECT), (760, 560))
    img.save(path, "PNG")
    return path


def _painted(page: Page) -> dict[str, Any]:
    """The hero frame, the img's natural size and fit, and the rect a contain fit paints the photo into."""
    return page.evaluate(
        """() => {
          const hero = document.querySelector('.cp-detail__hero').getBoundingClientRect();
          const img = document.querySelector('.cp-detail__hero-img');
          const box = img.getBoundingClientRect();
          const scale = Math.min(box.width / img.naturalWidth, box.height / img.naturalHeight);
          const w = img.naturalWidth * scale, h = img.naturalHeight * scale;
          return {
            hero: {width: hero.width, height: hero.height},
            img: {width: box.width, height: box.height, top: box.top, left: box.left},
            natural: {width: img.naturalWidth, height: img.naturalHeight},
            fit: getComputedStyle(img).objectFit,
            painted: {width: w, height: h, left: box.left + (box.width - w) / 2, top: box.top + (box.height - h) / 2},
            backdrop: getComputedStyle(document.querySelector('.cp-detail__hero-backdrop')).backgroundImage,
          };
        }"""
    )


def _pixel(page: Page, x: float, y: float) -> tuple[int, int, int]:
    """The colour painted at viewport point (x, y), read off a screenshot."""
    shot = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    return shot.getpixel((round(x), round(y)))  # type: ignore[return-value]


def describe_the_class_page_banner():
    def it_shows_the_whole_copy_the_crop_box_cut(
        live_server, page: Page, login_via_code, serve_media, settings, tmp_path
    ):
        offering = ClassOfferingFactory(
            instructor=_seed_instructor(), status=ClassOffering.Status.DRAFT, ready=True, image=""
        )
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)
        page.locator("#hero-file-input").set_input_files(str(_subject_photo(tmp_path / "subject.png")))
        expect(page.locator(FRAME)).to_be_visible()
        _wait_ready(page)

        # Cropper draws a full width 16:9 box in the middle; a drag down takes the lower slice.
        _drag_frame_down(page, 60)
        crop = json.loads(page.locator(CROP_INPUT).input_value())
        assert crop["w"] == 1200 and crop["h"] == pytest.approx(675, abs=1) and crop["y"] > 112
        page.locator('#composer-form button[type="submit"]').click()
        page.wait_for_url(re.compile(r"step=2"))

        offering.refresh_from_db()
        assert (offering.hero_crop_x, offering.hero_crop_y, offering.hero_crop_w, offering.hero_crop_h) == (
            crop["x"],
            crop["y"],
            crop["w"],
            crop["h"],
        )
        with offering.hero_cropped.open("rb") as handle:
            copy = Image.open(handle).convert("RGB")
            copy.load()
        assert copy.size == (crop["w"], crop["h"])
        assert offering.hero_aspect_ratio == f"{crop['w']} / {crop['h']}"

        # The public page at a desktop width and a phone width: a 16:9 copy fills the hero
        # column under the cap at both, so the frame has the copy's shape.
        ClassOffering.objects.filter(pk=offering.pk).update(status=ClassOffering.Status.PUBLISHED)
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
        url = f"{live_server.url}{reverse('classes:public_class_detail', kwargs={'slug': offering.slug})}"
        for width in (1366, 390):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(url)
            page.wait_for_load_state("networkidle")
            page.wait_for_function("() => document.querySelector('.cp-detail__hero-img').naturalWidth > 0")
            seen = _painted(page)
            assert seen["natural"] == {"width": crop["w"], "height": crop["h"]}, width
            assert seen["fit"] == "contain", width
            assert offering.hero_cropped.url in seen["backdrop"], width
            expected_height = min(seen["hero"]["width"] * crop["h"] / crop["w"], CAP)
            assert seen["hero"]["height"] == pytest.approx(expected_height, abs=1), width
            # The painted rect has the copy's shape and fits inside the frame: nothing is cut off.
            painted = seen["painted"]
            assert painted["width"] / painted["height"] == pytest.approx(crop["w"] / crop["h"], abs=0.01), width
            assert painted["width"] <= seen["hero"]["width"] + 0.5 and painted["height"] <= seen["hero"]["height"] + 0.5
            if seen["hero"]["height"] < CAP:
                assert seen["hero"]["width"] / seen["hero"]["height"] == pytest.approx(crop["w"] / crop["h"], abs=0.01)
            else:
                assert painted["height"] == pytest.approx(CAP, abs=1), width
            # The copy's top left corner is painted at the painted rect's top left: the field,
            # not the subject and not the blurred backdrop, with the overlay clear at the top.
            field = _pixel(page, painted["left"] + 4, painted["top"] + 4)
            assert field == pytest.approx(copy.getpixel((4, 4)), abs=24), (width, field)
            assert field == pytest.approx(FIELD, abs=24), (width, field)
