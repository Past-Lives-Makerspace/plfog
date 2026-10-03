"""End-to-end: the card focus sliders say which axis can move (issue #427).

The two card frames on the composer's Photos step are cover fitted and 150px tall, so a
photo overflows each frame on one axis only and the other slider writes a value that
frame cannot show: QA saw the CSS applied and nothing moving. ``card_focus.js``
``measure()`` now reads each frame's slack, and the field disables a slider with nothing
to move in either frame and says, under each slider, what it moves. Only a browser lays
the frames out and loads the photo, so this drives the real component through both paths
a photo reaches the frames by: the saved photo, the instant upload on a saved class, and
the local preview a class picks before its first save. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import pytest
from django.urls import reverse
from PIL import Image
from playwright.sync_api import expect

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

EMAIL = "slack-teacher@example.com"
STEP = '[data-composer-step="2"]'
CARD_PHOTOS = f"{STEP} .pl-card-focus__frame .cls-img"
SLIDERS = f"{STEP} .pl-card-focus__sliders"
ROW = f"{STEP} .pl-card-focus__slider-row"
OFF = re.compile(r"pl-card-focus__slider-row--off")
# The frames' widths come from the live catalog (hub.css, above .pl-card-focus__frame--laptop):
# 263px for a laptop and 342px for a phone. The card's 1px border sits inside, so the 150px
# tall media strip each measures is 2px narrower: shapes of 1.74 and 2.27.
LAPTOP = f"{STEP} .pl-card-focus__frame--laptop"
PHONE = f"{STEP} .pl-card-focus__frame--phone"

X_NONE = "This photo already fits side to side, so only up and down moves it."
Y_NONE = "This photo already fits top to bottom, so only left and right moves it."
X_LAPTOP = "Moves the laptop card. The phone card already fits side to side."
Y_PHONE = "Moves the phone card. The laptop card already fits top to bottom."


def _seed_instructor() -> Member:
    MembershipPlanFactory()  # so the user signal can provision the member the instructor factory then updates
    user = UserFactory(username=EMAIL)
    return cast(Member, InstructorFactory(user=user, full_legal_name="Slack Teacher", instructor_slug="slack-teacher"))


def _seed_draft(instructor: Member, width: int, height: int) -> ClassOffering:
    """A ready draft whose hero is a plain photo of the given size: no crop box, no copy."""
    return cast(
        ClassOffering,
        ClassOfferingFactory(
            instructor=instructor,
            status=ClassOffering.Status.DRAFT,
            ready=True,
            image__width=width,
            image__height=height,
        ),
    )


def _png(path: Path, width: int, height: int) -> Path:
    Image.new("RGB", (width, height), (40, 90, 160)).save(path, "PNG")
    return path


def _open_photos_step(page, live_server, url_name: str, **kwargs) -> None:
    page.goto(f"{live_server.url}{reverse(url_name, kwargs=kwargs)}")
    page.locator('[data-step-tab="2"]').click()


def _row(page, label: str):
    # By the label span's exact text: a why line can name the other slider ("only up and
    # down moves it"), so a substring match would catch both rows.
    return page.locator(ROW).filter(has=page.get_by_text(label, exact=True))


def _range(row):
    return row.locator("input.pl-card-focus__range")


def _why(row):
    return row.locator(".pl-card-focus__slider-why")


def _slide(range_input, value: int) -> None:
    """Move a slider the way the other composer specs do: set it and fire input."""
    range_input.evaluate(
        "(el, value) => { el.value = value; el.dispatchEvent(new Event('input', { bubbles: true })); }", value
    )


def _expect_frames_at(page, position: str) -> None:
    """Both Photos step frames compute the object-position the sliders chose."""
    photos = page.locator(CARD_PHOTOS)
    expect(photos).to_have_count(2)
    for photo in photos.all():
        expect(photo).to_have_css("object-position", position)


def _frame_width(page, selector: str) -> float:
    box = page.locator(selector).bounding_box()
    assert box is not None, f"{selector} has no box"
    return box["width"]


def describe_card_focus_slack():
    def it_disables_left_and_right_on_a_portrait_and_up_and_down_moves_both_frames(
        live_server, page, login_via_code, serve_media
    ):
        # 600 by 900: narrower than either frame, so both fit it to their width and all the
        # overflow is vertical. Left and right has nothing to move anywhere.
        offering = _seed_draft(_seed_instructor(), 600, 900)
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)

        across, down = _row(page, "Left and right"), _row(page, "Up and down")
        expect(_range(across)).to_be_disabled()
        expect(across).to_have_class(OFF)
        expect(_why(across)).to_have_text(X_NONE)
        expect(_range(down)).to_be_enabled()
        expect(down).not_to_have_class(OFF)
        expect(_why(down)).to_be_hidden()

        _expect_frames_at(page, "50% 50%")
        _slide(_range(down), 90)
        _expect_frames_at(page, "50% 90%")
        assert json.loads(page.locator("[data-card-focus-input]").input_value()) == {"x": 50, "y": 90}

    def it_disables_up_and_down_on_a_wide_landscape_and_left_and_right_moves_both_frames(
        live_server, page, login_via_code, serve_media
    ):
        # 3000 by 600: wider than either frame, so both fit it to their height and all the
        # overflow is sideways. Up and down is the dead one here.
        offering = _seed_draft(_seed_instructor(), 3000, 600)
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)

        across, down = _row(page, "Left and right"), _row(page, "Up and down")
        expect(_range(down)).to_be_disabled()
        expect(down).to_have_class(OFF)
        expect(_why(down)).to_have_text(Y_NONE)
        expect(_range(across)).to_be_enabled()
        expect(_why(across)).to_be_hidden()

        _slide(_range(across), 10)
        _expect_frames_at(page, "10% 50%")
        assert json.loads(page.locator("[data-card-focus-input]").input_value()) == {"x": 10, "y": 50}

    def it_names_the_frame_each_slider_moves_when_the_photo_sits_between_the_two_shapes(
        live_server, page, login_via_code, serve_media
    ):
        # 2000 by 1000 (2.0) is wider than the laptop frame (1.75) and narrower than the phone
        # frame (2.28): sideways slack on the laptop, vertical slack on the phone. Both sliders
        # live, and each line names the one card it moves. The only shape where neither is off.
        offering = _seed_draft(_seed_instructor(), 2000, 1000)
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)
        expect(page.locator(CARD_PHOTOS)).to_have_count(2)
        assert _frame_width(page, LAPTOP) == pytest.approx(263, abs=1)
        assert _frame_width(page, PHONE) == pytest.approx(342, abs=1)

        across, down = _row(page, "Left and right"), _row(page, "Up and down")
        expect(_why(across)).to_have_text(X_LAPTOP)
        expect(_why(down)).to_have_text(Y_PHONE)
        expect(_range(across)).to_be_enabled()
        expect(_range(down)).to_be_enabled()
        expect(across).not_to_have_class(OFF)
        expect(down).not_to_have_class(OFF)

    def it_flips_left_and_right_on_when_the_instant_upload_swaps_a_portrait_for_a_wide_one(
        live_server, page, login_via_code, serve_media, tmp_path
    ):
        # A saved class uploads instantly and the frames follow the new photo with no reload,
        # so the sliders are measured again on the photo the class now has.
        offering = _seed_draft(_seed_instructor(), 600, 900)
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_edit", pk=offering.pk)
        across, down = _row(page, "Left and right"), _row(page, "Up and down")
        expect(_range(across)).to_be_disabled()
        expect(_range(down)).to_be_enabled()

        page.locator("#hero-file-input").set_input_files(str(_png(tmp_path / "wide-hero.png", 3000, 600)))

        expect(_range(across)).to_be_enabled()
        expect(_why(across)).to_be_hidden()
        expect(_range(down)).to_be_disabled()
        expect(_why(down)).to_have_text(Y_NONE)
        offering.refresh_from_db()
        assert "wide-hero" in offering.image.name
        photos = page.locator(CARD_PHOTOS)
        expect(photos).to_have_count(2)
        for photo in photos.all():
            expect(photo).to_have_attribute("src", offering.image.url)

    def it_disables_left_and_right_on_a_portrait_picked_before_the_first_save(
        live_server, page, login_via_code, tmp_path
    ):
        # Create mode: the photo exists only as the hero field's local data URL, mirrored into
        # the skeleton card (cardFocus.localSrc). The sliders appear with it, already measured.
        _seed_instructor()
        login_via_code(EMAIL)
        _open_photos_step(page, live_server, "classes:teach_class_create")
        expect(page.locator(SLIDERS)).to_be_hidden()

        page.locator('#hero-upload-zone input[type="file"]').set_input_files(
            str(_png(tmp_path / "first-hero.png", 600, 900))
        )

        expect(page.locator(SLIDERS)).to_be_visible()
        expect(page.locator(CARD_PHOTOS).first).to_have_attribute("src", re.compile(r"^data:image/png"))
        across, down = _row(page, "Left and right"), _row(page, "Up and down")
        expect(_range(across)).to_be_disabled()
        expect(_why(across)).to_have_text(X_NONE)
        expect(_range(down)).to_be_enabled()
        expect(_why(down)).to_be_hidden()
