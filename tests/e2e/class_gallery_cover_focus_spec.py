"""End-to-end: the composer gallery's Make cover button and Set focus panel, through to the class page.

Make cover moves a card to the front of the grid and posts the existing reorder route; the
Cover badge is CSS ``:first-child``, so only a browser can say it moved. Set focus is a tap on
the photo, so only a browser can say the tap became a stored point, and that the point reached
the editor thumbnail and the class page frame and thumbnail as ``object-position``. Run with
``pytest -m e2e`` on Postgres: these specs write from the browser.
"""

from __future__ import annotations

from typing import cast
from urllib.parse import urlparse

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

from classes.factories import ClassImageFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassImage, ClassOffering
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

EMAIL = "cover-teacher@example.com"
CARDS = "#gallery-grid .cls-image-cell"
PANEL = "#gallery-focus"


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before the teardown truncates (see class_composer_steps_spec.py)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _seed_instructor() -> Member:
    MembershipPlanFactory()  # so the user signal can provision the member the instructor factory then updates
    user = UserFactory(username=EMAIL)
    return cast(Member, InstructorFactory(user=user, full_legal_name="Cover Teacher", instructor_slug="cover-teacher"))


def _seed_draft(count: int) -> tuple[ClassOffering, list[ClassImage]]:
    offering = cast(
        ClassOffering,
        ClassOfferingFactory(instructor=_seed_instructor(), status=ClassOffering.Status.DRAFT, ready=True, gallery=0),
    )
    names = ["One", "Two", "Three"][:count]
    images = [
        cast(
            ClassImage,
            ClassImageFactory(
                class_offering=offering,
                sort_order=i,
                alt_text=name,
                image__width=800,
                image__height=600,
                image__color=["#b5651d", "#3d8bd4", "#2f8f4e"][i],
            ),
        )
        for i, name in enumerate(names)
    ]
    return offering, images


def _open_gallery(page: Page, live_server, offering: ClassOffering) -> None:
    page.goto(f"{live_server.url}{reverse('classes:teach_class_edit', kwargs={'pk': offering.pk})}")
    page.locator('[data-step-tab="2"]').click()
    expect(page.locator('[data-composer-step="2"]')).to_be_visible()
    expect(page.locator("#gallery-cover-hint")).to_be_visible()


def _publish(offering: ClassOffering) -> None:
    offering.status = ClassOffering.Status.PUBLISHED
    offering.published_at = timezone.now()
    offering.save(update_fields=["status", "published_at"])


def _open_class_page(page: Page, live_server, offering: ClassOffering, settings) -> None:
    settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
    page.goto(f"{live_server.url}{reverse('classes:public_class_detail', kwargs={'slug': offering.slug})}")
    expect(page.locator(".cls-gallery")).to_be_visible()


def _object_position(locator) -> str:
    return cast(str, locator.evaluate("el => getComputedStyle(el).objectPosition"))


def _expect_cover(page: Page, image: ClassImage, total: int) -> None:
    cards = page.locator(CARDS)
    expect(cards).to_have_count(total)
    expect(cards.nth(0)).to_have_attribute("data-id", str(image.pk))
    expect(cards.nth(0).locator(".cls-image-cover-badge")).to_be_visible()
    expect(cards.nth(0).locator(".cls-image-make-cover")).to_be_hidden()
    for i in range(1, total):
        expect(cards.nth(i).locator(".cls-image-cover-badge")).to_be_hidden()
        expect(cards.nth(i).locator(".cls-image-make-cover")).to_be_visible()


def describe_make_cover():
    def it_moves_the_third_photo_to_the_front_of_the_grid_the_gallery_and_the_class_page(
        live_server, page: Page, login_via_code, serve_media, settings
    ):
        offering, (one, two, three) = _seed_draft(3)
        login_via_code(EMAIL)
        _open_gallery(page, live_server, offering)
        _expect_cover(page, one, 3)

        page.locator(CARDS).nth(2).locator(".cls-image-make-cover").click()

        expect(page.get_by_text("Cover photo updated.")).to_be_visible()
        _expect_cover(page, three, 3)
        # The keyboard is not stranded on the button that just hid.
        expect(page.locator(CARDS).nth(0).locator(".cls-image-set-focus")).to_be_focused()
        assert [img.pk for img in offering.gallery_images.all()] == [three.pk, one.pk, two.pk]

        # The badge follows a reload too: the server renders the saved order.
        page.reload()
        page.locator('[data-step-tab="2"]').click()
        _expect_cover(page, three, 3)

        _publish(offering)
        _open_class_page(page, live_server, offering, settings)
        expect(page.locator('.cls-gallery__slide[data-slide-index="0"]')).to_have_attribute("data-slide-alt", "Three")

    def it_puts_the_grid_back_when_the_save_fails(live_server, page: Page, login_via_code, serve_media):
        offering, (one, two, three) = _seed_draft(3)
        login_via_code(EMAIL)
        _open_gallery(page, live_server, offering)
        reorder = reverse("classes:teach_class_image_reorder", kwargs={"pk": offering.pk})
        page.route(f"{live_server.url}{reorder}", lambda route, request: route.fulfill(status=500, body=""))

        page.locator(CARDS).nth(1).locator(".cls-image-make-cover").click()

        expect(page.get_by_text("Could not update the cover. Try again.")).to_be_visible()
        _expect_cover(page, one, 3)
        expect(page.locator(CARDS).nth(1)).to_have_attribute("data-id", str(two.pk))
        expect(page.locator(CARDS).nth(2)).to_have_attribute("data-id", str(three.pk))
        assert [img.pk for img in offering.gallery_images.all()] == [one.pk, two.pk, three.pk]

    def it_keeps_the_badge_on_the_first_card_after_a_delete(live_server, page: Page, login_via_code, serve_media):
        offering, (one, two) = _seed_draft(2)
        login_via_code(EMAIL)
        _open_gallery(page, live_server, offering)

        page.locator(CARDS).nth(0).locator(".cls-image-remove").click()

        _expect_cover(page, two, 1)


def describe_set_focus():
    def it_stores_a_tap_near_the_top_left_and_the_editor_and_class_page_crop_around_it(
        live_server, page: Page, login_via_code, serve_media, settings
    ):
        offering, (one, _two) = _seed_draft(2)
        login_via_code(EMAIL)
        _open_gallery(page, live_server, offering)

        page.locator(CARDS).nth(0).locator(".cls-image-set-focus").click()
        expect(page.locator(PANEL)).to_be_visible()
        expect(page.locator("#gallery-focus-picker")).to_be_focused()
        photo = page.locator("#gallery-focus-photo")
        expect(photo).to_have_js_property("complete", True)
        box = photo.bounding_box()
        assert box is not None and box["width"] > 100
        photo.click(position={"x": box["width"] * 0.1, "y": box["height"] * 0.15})
        expect(page.locator("#gallery-focus-readout")).to_contain_text("% across")

        page.locator(f"{PANEL} [data-focus-save]").click()

        expect(page.get_by_text("Focus saved.")).to_be_visible()
        expect(page.locator(PANEL)).to_be_hidden()
        one.refresh_from_db()
        assert one.focus_x is not None and abs(one.focus_x - 10) <= 2
        assert one.focus_y is not None and abs(one.focus_y - 15) <= 2
        expected = f"{one.focus_x}% {one.focus_y}%"
        assert _object_position(page.locator(CARDS).nth(0).locator(".cls-image-thumb img")) == expected

        _publish(offering)
        _open_class_page(page, live_server, offering, settings)
        assert _object_position(page.locator('.cls-gallery__slide[data-slide-index="0"] img')) == expected
        assert _object_position(page.locator(".cls-gallery__thumb img").nth(0)) == expected
        assert _object_position(page.locator(".cls-gallery__thumb img").nth(1)) == "50% 50%"

    def it_resets_to_the_centre(live_server, page: Page, login_via_code, serve_media, settings):
        offering, (one, _two) = _seed_draft(2)
        ClassImage.objects.filter(pk=one.pk).update(focus_x=5, focus_y=95)
        login_via_code(EMAIL)
        _open_gallery(page, live_server, offering)
        thumb = page.locator(CARDS).nth(0).locator(".cls-image-thumb img")
        assert _object_position(thumb) == "5% 95%"

        page.locator(CARDS).nth(0).locator(".cls-image-set-focus").click()
        expect(page.locator("#gallery-focus-readout")).to_have_text("Focus: 5% across, 95% down")
        page.locator(f"{PANEL} [data-focus-reset]").click()

        expect(page.get_by_text("Focus reset to the centre.")).to_be_visible()
        expect(page.locator(PANEL)).to_be_hidden()
        one.refresh_from_db()
        assert (one.focus_x, one.focus_y) == (None, None)
        assert _object_position(thumb) == "50% 50%"

        _publish(offering)
        _open_class_page(page, live_server, offering, settings)
        assert _object_position(page.locator('.cls-gallery__slide[data-slide-index="0"] img')) == "50% 50%"

    def it_works_from_the_keyboard_and_cancel_posts_nothing(live_server, page: Page, login_via_code, serve_media):
        offering, (one, _two) = _seed_draft(2)
        login_via_code(EMAIL)
        _open_gallery(page, live_server, offering)
        opener = page.locator(CARDS).nth(0).locator(".cls-image-set-focus")
        opener.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#gallery-focus-picker")).to_be_focused()

        page.keyboard.press("ArrowLeft")
        page.keyboard.press("ArrowUp")
        expect(page.locator("#gallery-focus-readout")).to_have_text("Focus: 45% across, 45% down")
        assert _object_position(page.locator("#gallery-focus-preview")) == "45% 45%"
        page.keyboard.press("Escape")

        expect(page.locator(PANEL)).to_be_hidden()
        expect(opener).to_be_focused()
        one.refresh_from_db()
        assert (one.focus_x, one.focus_y) == (None, None)

        # Cancel closes the same way, and the keyboard can still reach Save.
        page.keyboard.press("Enter")
        page.keyboard.press("Shift+ArrowRight")
        page.locator(f"{PANEL} [data-focus-cancel]:has-text('Cancel')").click()
        expect(page.locator(PANEL)).to_be_hidden()
        one.refresh_from_db()
        assert (one.focus_x, one.focus_y) == (None, None)

        page.keyboard.press("Enter")
        page.keyboard.press("Shift+ArrowRight")
        page.locator(f"{PANEL} [data-focus-save]").focus()
        page.keyboard.press("Enter")
        expect(page.get_by_text("Focus saved.")).to_be_visible()
        one.refresh_from_db()
        assert (one.focus_x, one.focus_y) == (51, 50)

    def it_fits_a_phone_screen(live_server, page: Page, login_via_code, serve_media):
        offering, _images = _seed_draft(2)
        page.set_viewport_size({"width": 390, "height": 844})
        login_via_code(EMAIL)
        _open_gallery(page, live_server, offering)

        page.locator(CARDS).nth(1).locator(".cls-image-set-focus").click()

        dialog = page.locator(f"{PANEL} .pl-modal")
        expect(dialog).to_be_visible()
        box = dialog.bounding_box()
        assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 390
        assert page.evaluate("document.documentElement.scrollWidth") <= 390
        save = page.locator(f"{PANEL} [data-focus-save]")
        save.scroll_into_view_if_needed()
        expect(save).to_be_in_viewport()
