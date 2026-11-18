"""End-to-end: the Share button on a live class's Share & Print card (#1254).

The button opens a two-item menu, Story and Post. ``static/js/share_image.js``
fetches the chosen PNG and hands it to ``navigator.share`` as a file where ``navigator.canShare``
takes files, downloads it where it does not (Chromium on Linux has neither, like most desktop
browsers), and inside the Android app, which has no ``navigator.share``, shares the class link
through the Capacitor plugin and shows a note. The sheet and the bridge are stubbed through
``add_init_script``, the way the real bridge injects into the remote page; the stubbed sheet
decodes the file it is given, so the size asserted is the picture's own. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import io
from datetime import timedelta
from typing import cast

import pytest
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

from classes.factories import ClassOfferingFactory, ClassSessionFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

TEACHER = "instagram-share@example.com"
TITLE = "Forge a Leaf Dish"
SHARE = "button[data-share-image-open]"
MENU = "[data-share-image-menu] [role=menu]"
ITEMS = "[data-share-image-menu] [role=menuitem]"
NOTE = "[data-share-image-note]"
CHOICES = [
    pytest.param("Story", "story", (1080, 1920), id="story"),
    pytest.param("Post", "post", (1080, 1350), id="post"),
]
FILE_SHEET = """
navigator.canShare = function (data) { return Boolean(data && data.files && data.files.length); };
navigator.share = function (data) {
  var file = data.files[0];
  return createImageBitmap(file).then(function (bitmap) {
    window.__plSharedPicture = { name: file.name, type: file.type, width: bitmap.width,
                                 height: bitmap.height, title: data.title, count: data.files.length };
  });
};
"""
CLOSED_SHEET = """
navigator.canShare = function () { return true; };
navigator.share = function () {
  window.__plShareCalls = (window.__plShareCalls || 0) + 1;
  return Promise.reject(new DOMException('closed', 'AbortError'));
};
"""
ANDROID_APP = (
    "window.Capacitor = { isNativePlatform: function () { return true; },"
    " Plugins: { Share: { share: function (data) { window.__plCapShared = data; return Promise.resolve({}); } } } };"
)


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before teardown truncates (see class_composer_draft_spec)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _open_live_class(page: Page, live_server, login_via_code) -> ClassOffering:
    """Sign the instructor in and open their live class's edit page, where the card sits."""
    MembershipPlanFactory()  # so the user signal provisions the member the instructor factory updates
    user = UserFactory(username=TEACHER, email=TEACHER)
    instructor = cast(Member, InstructorFactory(user=user, full_legal_name="Nia Share", instructor_slug="nia-share"))
    offering = cast(
        ClassOffering,
        ClassOfferingFactory(
            instructor=instructor, status=ClassOffering.Status.PUBLISHED, title=TITLE, slug="forge-a-leaf-dish"
        ),
    )
    start = timezone.now() + timedelta(days=3)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    login_via_code(TEACHER)
    page.goto(f"{live_server.url}{reverse('classes:teach_class_edit', kwargs={'pk': offering.pk})}")
    expect(page.locator(SHARE)).to_be_visible()
    return offering


def describe_the_instagram_share_button():
    def it_opens_a_two_item_menu_that_escape_and_a_click_outside_close(live_server, page: Page, login_via_code):
        _open_live_class(page, live_server, login_via_code)
        expect(page.locator(MENU)).to_be_hidden()
        expect(page.locator(SHARE)).to_have_attribute("aria-expanded", "false")

        page.locator(SHARE).click()

        expect(page.locator(MENU)).to_be_visible()
        expect(page.locator(ITEMS)).to_have_text(["Story", "Post"])
        expect(page.locator(SHARE)).to_have_attribute("aria-expanded", "true")
        page.keyboard.press("Escape")
        expect(page.locator(MENU)).to_be_hidden()

        page.locator(SHARE).click()
        expect(page.locator(MENU)).to_be_visible()
        page.locator(".pl-qr-share__title").click()
        expect(page.locator(MENU)).to_be_hidden()

    @pytest.mark.parametrize(("label", "fmt", "size"), CHOICES)
    def it_hands_the_picture_to_the_share_sheet(live_server, page: Page, login_via_code, label, fmt, size):
        page.add_init_script(FILE_SHEET)
        _open_live_class(page, live_server, login_via_code)

        page.locator(SHARE).click()
        page.get_by_role("menuitem", name=label, exact=True).click()

        page.wait_for_function("window.__plSharedPicture")
        assert page.evaluate("window.__plSharedPicture") == {
            "name": f"forge-a-leaf-dish-instagram-{fmt}.png",
            "type": "image/png",
            "width": size[0],
            "height": size[1],
            "title": TITLE,
            "count": 1,
        }
        expect(page.locator(MENU)).to_be_hidden()
        expect(page.locator(NOTE)).to_be_hidden()

    @pytest.mark.parametrize(("label", "fmt", "size"), CHOICES)
    def it_downloads_the_picture_where_the_browser_cannot_share_files(
        live_server, page: Page, login_via_code, label, fmt, size
    ):
        _open_live_class(page, live_server, login_via_code)
        assert page.evaluate("typeof navigator.canShare") == "undefined"

        page.locator(SHARE).click()
        with page.expect_download() as info:
            page.get_by_role("menuitem", name=label, exact=True).click()

        download = info.value
        assert download.suggested_filename == f"forge-a-leaf-dish-instagram-{fmt}.png"
        image = Image.open(io.BytesIO(open(download.path(), "rb").read()))
        assert (image.format, image.size) == ("PNG", size)

    def it_stays_quiet_when_the_person_closes_the_sheet(live_server, page: Page, login_via_code):
        page.add_init_script(CLOSED_SHEET)
        _open_live_class(page, live_server, login_via_code)
        downloads: list[object] = []
        page.on("download", lambda download: downloads.append(download))

        page.locator(SHARE).click()
        page.get_by_role("menuitem", name="Post", exact=True).click()

        page.wait_for_function("window.__plShareCalls === 1")
        page.wait_for_timeout(500)  # a fallback download would have started by now
        assert downloads == []
        expect(page.locator(MENU)).to_be_hidden()

    def it_shares_the_class_link_inside_the_android_app_and_says_why(live_server, page: Page, login_via_code):
        page.add_init_script(ANDROID_APP)
        offering = _open_live_class(page, live_server, login_via_code)
        expect(page.locator(NOTE)).to_be_hidden()

        page.locator(SHARE).click()
        page.get_by_role("menuitem", name="Story", exact=True).click()

        page.wait_for_function("window.__plCapShared")
        assert page.evaluate("window.__plCapShared") == {
            "title": TITLE,
            "url": offering.qr_url,
            "dialogTitle": "Share this class",
        }
        expect(page.locator(NOTE)).to_be_visible()
