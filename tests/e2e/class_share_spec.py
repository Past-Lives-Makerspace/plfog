"""End-to-end: the Share button on the public class page.

Chromium on Linux has no ``navigator.share``, so a click opens the Copy / Text / Email menu
and Copy link writes the class's public url to the clipboard. The device sheet is proved by
stubbing ``navigator.share`` and the Capacitor Share plugin through ``add_init_script``, the
way the real bridge injects into the remote page: each is called with the facts and the menu
stays shut, a closed sheet (AbortError) is silent, and any other failure falls through to the
menu. The last scenario reaches the page through a boosted click from the catalog, so the
listener ``static/js/class_share.js`` bound on the first document is what serves the arrival.
Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import cast
from urllib.parse import unquote, urlparse

from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Page, expect

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering

SHARE_BUTTON = "button[data-share-url]"
MENU = ".cp-share [role=menu]"
ITEMS = ".cp-share [role=menuitem]"
COPY = "button[data-share-copy]"
MANUAL = ".cp-share__manual"
NO_CLIPBOARD = "Object.defineProperty(navigator, 'clipboard', { value: undefined });"
TEXT_LINK = ".cp-share a[href^='sms:']"
EMAIL_LINK = ".cp-share a[href^='mailto:']"
TITLE = "Forge a Leaf Dish"
WEB_SHARE_STUB = "navigator.share = function (data) { window.__plShared = data; return Promise.resolve(); };"
WEB_SHARE_CLOSED = (
    "navigator.share = function () {"
    " window.__plShareCalls = (window.__plShareCalls || 0) + 1;"
    " return Promise.reject(new DOMException('closed', 'AbortError')); };"
)
WEB_SHARE_BROKEN = "navigator.share = function () { return Promise.reject(new Error('no sheet')); };"
CAPACITOR_STUB = (
    "window.Capacitor = { isNativePlatform: function () { return true; },"
    " Plugins: { Share: { share: function (data) { window.__plCapShared = data; return Promise.resolve({}); } } } };"
)


def _published() -> ClassOffering:
    offering = cast(
        "ClassOffering",
        ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, title=TITLE, slug="forge-a-leaf-dish"),
    )
    start = timezone.now() + timedelta(days=3)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _open(page: Page, live_server, settings) -> ClassOffering:
    offering = _published()
    settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
    page.goto(f"{live_server.url}{reverse('classes:public_class_detail', kwargs={'slug': offering.slug})}")
    return offering


def describe_the_share_button():
    def it_opens_a_menu_of_copy_text_and_email_when_there_is_no_share_sheet(live_server, page: Page, settings):
        offering = _open(page, live_server, settings)
        assert page.evaluate("typeof navigator.share") == "undefined"
        expect(page.locator(MENU)).to_be_hidden()

        page.locator(SHARE_BUTTON).click()

        expect(page.locator(MENU)).to_be_visible()
        expect(page.locator(ITEMS)).to_have_count(3)
        text_href = page.locator(TEXT_LINK).get_attribute("href") or ""
        email_href = page.locator(EMAIL_LINK).get_attribute("href") or ""
        assert text_href.startswith("sms:") and offering.public_url in unquote(text_href)
        assert email_href.startswith("mailto:") and offering.public_url in unquote(email_href)
        assert f"{TITLE} at Past Lives Makerspace" in unquote(email_href)

        page.keyboard.press("Escape")
        expect(page.locator(MENU)).to_be_hidden()

    def it_closes_the_menu_on_a_click_outside(live_server, page: Page, settings):
        _open(page, live_server, settings)
        page.locator(SHARE_BUTTON).click()
        expect(page.locator(MENU)).to_be_visible()
        page.locator("h1.cp-detail__title").click()
        expect(page.locator(MENU)).to_be_hidden()

    def it_copies_the_link_to_the_clipboard(live_server, page: Page, settings):
        page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_server.url)
        offering = _open(page, live_server, settings)
        page.locator(SHARE_BUTTON).click()

        page.locator(COPY).click()

        expect(page.locator(COPY)).to_have_text("Copied!")
        assert page.evaluate("navigator.clipboard.readText()") == offering.public_url

    def it_offers_the_link_to_copy_by_hand_when_the_clipboard_is_missing(live_server, page: Page, settings):
        page.add_init_script(NO_CLIPBOARD)
        offering = _open(page, live_server, settings)
        page.locator(SHARE_BUTTON).click()
        expect(page.locator(MANUAL)).to_be_hidden()

        page.locator(COPY).click()

        expect(page.locator(MANUAL)).to_be_visible()
        expect(page.locator(MANUAL)).to_have_value(offering.public_url)
        expect(page.locator(COPY)).to_have_text("Copy link")
        state = page.evaluate(
            """() => {
              const el = document.querySelector('.cp-share__manual');
              return {focused: document.activeElement === el,
                      start: el.selectionStart, end: el.selectionEnd, length: el.value.length};
            }"""
        )
        assert state["focused"] is True
        assert (state["start"], state["end"]) == (0, state["length"])

    def it_prefers_the_web_share_sheet_and_opens_no_menu(live_server, page: Page, settings):
        page.add_init_script(WEB_SHARE_STUB)
        offering = _open(page, live_server, settings)

        page.locator(SHARE_BUTTON).click()

        page.wait_for_function("window.__plShared")
        assert page.evaluate("window.__plShared") == {
            "title": TITLE,
            "text": f"{TITLE} at Past Lives Makerspace",
            "url": offering.public_url,
        }
        expect(page.locator(MENU)).to_be_hidden()

    def it_stays_quiet_when_the_person_closes_the_sheet(live_server, page: Page, settings):
        page.add_init_script(WEB_SHARE_CLOSED)
        _open(page, live_server, settings)
        page.locator(SHARE_BUTTON).click()
        page.wait_for_function("window.__plShareCalls === 1")
        expect(page.locator(MENU)).to_be_hidden()

    def it_falls_back_to_the_menu_when_the_sheet_fails(live_server, page: Page, settings):
        page.add_init_script(WEB_SHARE_BROKEN)
        _open(page, live_server, settings)
        page.locator(SHARE_BUTTON).click()
        expect(page.locator(MENU)).to_be_visible()

    def it_prefers_the_capacitor_plugin_inside_the_app(live_server, page: Page, settings):
        page.add_init_script(CAPACITOR_STUB + WEB_SHARE_STUB)
        offering = _open(page, live_server, settings)

        page.locator(SHARE_BUTTON).click()

        page.wait_for_function("window.__plCapShared")
        assert page.evaluate("window.__plCapShared") == {
            "title": TITLE,
            "text": f"{TITLE} at Past Lives Makerspace",
            "url": offering.public_url,
            "dialogTitle": "Share this class",
        }
        assert page.evaluate("window.__plShared") is None
        expect(page.locator(MENU)).to_be_hidden()

    def it_still_works_after_a_boosted_arrival_from_the_catalog(live_server, page: Page, settings):
        offering = _published()
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
        page.goto(f"{live_server.url}{reverse('classes:public_list')}")
        detail_path = reverse("classes:public_class_detail", kwargs={"slug": offering.slug})

        page.locator(f"a.cls-title[href='{detail_path}']").click()
        page.wait_for_url(f"**{detail_path}")
        page.locator(SHARE_BUTTON).click()

        expect(page.locator(MENU)).to_be_visible()
        expect(page.locator(ITEMS)).to_have_count(3)
