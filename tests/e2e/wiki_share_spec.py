"""End-to-end: Share on a wiki page (#1223).

A member with no edit rights reads an Official page. With ``navigator.share`` removed (the
desktop browsers and the Android app today), Share in the desktop row opens the Copy / Text /
Email menu, every item carrying the page's short ``/m/<code>/`` link, and Copy link answers
"Copied!". On a phone the header's copy is hidden and the action bar's Share opens the same menu
upward, inside the screen, above the bar. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from urllib.parse import unquote

from django.contrib.auth.models import User
from playwright.sync_api import Page, expect

from membership.models import Member, WikiPage
from tests.features import turn_on
from tests.membership.factories import MembershipPlanFactory, WikiPageFactory

READER_EMAIL = "wiki-share-reader@example.com"
NO_WEB_SHARE = "delete Navigator.prototype.share; delete navigator.share;"
TITLE = "Laser Cutter Rules"


def _reader() -> None:
    MembershipPlanFactory()
    member = User.objects.create_user(username=READER_EMAIL, email=READER_EMAIL).member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Juniper Ashdown"
    member.save(update_fields=["status", "full_legal_name"])


def _open(page: Page, live_server, login_via_code, *, width: int, height: int) -> WikiPage:
    turn_on("wiki")
    _reader()
    wiki_page = WikiPageFactory(title=TITLE, official=True)
    page.add_init_script(NO_WEB_SHARE)
    page.set_viewport_size({"width": width, "height": height})
    login_via_code(READER_EMAIL)
    page.goto(f"{live_server.url}{wiki_page.get_absolute_url()}")
    return wiki_page


def describe_sharing_a_wiki_page():
    def it_opens_copy_text_and_email_from_the_desktop_row(live_server, page: Page, login_via_code):
        wiki_page = _open(page, live_server, login_via_code, width=1280, height=900)
        page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_server.url)
        assert page.evaluate("typeof navigator.share") == "undefined"
        expect(page.locator(".pl-wp-actionbar")).to_be_hidden()

        share = page.locator(".pl-wp-actions [data-wiki-share]")
        expect(share.locator("[role=menu]")).to_be_hidden()
        share.get_by_role("button", name="Share").click()

        menu = share.locator("[role=menu]")
        expect(menu).to_be_visible()
        expect(share.locator("[role=menuitem]")).to_have_count(3)
        url = wiki_page.sticker_url
        text_href = unquote(share.locator("a[href^='sms:']").get_attribute("href") or "")
        email_href = unquote(share.locator("a[href^='mailto:']").get_attribute("href") or "")
        assert f"{TITLE} on the Past Lives wiki {url}" in text_href
        assert email_href.startswith(f"mailto:?subject={TITLE}&body=")
        assert f"{TITLE} on the Past Lives wiki {url}" in email_href

        share.locator("[data-share-copy]").click()
        expect(share.locator("[data-share-copy]")).to_have_text("Copied!")
        assert page.evaluate("navigator.clipboard.readText()") == url

    def it_opens_the_menu_upward_from_the_phone_bar(live_server, page: Page, login_via_code):
        wiki_page = _open(page, live_server, login_via_code, width=390, height=844)
        expect(page.locator(".pl-wp-actions [data-wiki-share]")).to_be_hidden()

        bar = page.locator(".pl-wp-actionbar")
        share = bar.locator("[data-wiki-share]")
        share.get_by_role("button", name="Share").click()

        menu = share.locator("[role=menu]")
        expect(menu).to_be_visible()
        expect(share.locator("[role=menuitem]")).to_have_count(3)
        assert share.locator("[data-share-copy]").get_attribute("data-share-copy") == wiki_page.sticker_url
        panel = share.locator(".pl-share__panel").bounding_box()
        bar_box = bar.bounding_box()
        assert panel is not None and bar_box is not None
        assert panel["y"] >= 0
        assert panel["y"] + panel["height"] <= bar_box["y"]
        assert panel["x"] >= 0 and panel["x"] + panel["width"] <= 390
