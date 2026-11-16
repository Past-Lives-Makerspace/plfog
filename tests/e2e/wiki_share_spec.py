"""End-to-end: Share on a wiki page (#1223).

A member with no edit rights reads an Official page. With ``navigator.share`` removed (the
desktop browsers and the Android app today), Share in the desktop row opens the Copy / Text /
Email menu, every item carrying the page's short ``/m/<code>/`` link, and Copy link answers
"Copied!". On a phone the header's copy is hidden and the action bar's Share opens the same menu
upward, inside the screen, above the bar. A phone reader with no bar (an admin previewing as Guest,
switched through the endpoint the View As picker posts to) keeps the header Share, whose menu opens
from the row's left edge and stays inside the screen. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from urllib.parse import unquote

from django.contrib.auth.models import User
from django.urls import reverse
from playwright.sync_api import Page, expect

from membership.models import Member, WikiPage
from tests.features import turn_on
from tests.membership.factories import MembershipPlanFactory, WikiPageFactory

READER_EMAIL = "wiki-share-reader@example.com"
NO_WEB_SHARE = "delete Navigator.prototype.share; delete navigator.share;"
TITLE = "Laser Cutter Rules"


def _reader(fog_role: str) -> None:
    MembershipPlanFactory()
    member = User.objects.create_user(username=READER_EMAIL, email=READER_EMAIL).member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Juniper Ashdown"
    member.fog_role = fog_role
    member.save(update_fields=["status", "full_legal_name", "fog_role"])
    member.sync_user_permissions()


def _preview_as_guest(page: Page, live_server) -> None:
    """Switch the session to the Guest preview the way the View As picker's pick() posts it."""
    status = page.evaluate(
        """async (endpoint) => {
            const token = document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.split('=')[1];
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token || '' },
                body: JSON.stringify({ role: 'guest' }),
            });
            return response.status;
        }""",
        f"{live_server.url}{reverse('hub_view_as_set')}",
    )
    assert status == 200


def _inside(box, width: int, height: int) -> None:
    assert box is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert box["y"] >= 0 and box["y"] + box["height"] <= height


def _open(
    page: Page, live_server, login_via_code, *, width: int, height: int, fog_role: str = Member.FogRole.MEMBER
) -> WikiPage:
    turn_on("wiki")
    _reader(fog_role)
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

    def it_keeps_the_header_menu_on_screen_for_a_phone_reader_with_no_bar(live_server, page: Page, login_via_code):
        wiki_page = _open(page, live_server, login_via_code, width=390, height=844, fog_role=Member.FogRole.ADMIN)
        _preview_as_guest(page, live_server)
        page.reload()
        expect(page.locator(".pl-wp-actionbar")).to_have_count(0)

        share = page.locator(".pl-wp-actions [data-wiki-share]")
        expect(share).to_be_visible()
        share.get_by_role("button", name="Share").click()

        expect(share.locator("[role=menu]")).to_be_visible()
        assert share.locator("[data-share-copy]").get_attribute("data-share-copy") == wiki_page.sticker_url
        _inside(share.locator(".pl-share__panel").bounding_box(), 390, 844)

        # The by-hand copy field is the widest thing the panel holds; it must fit too.
        page.evaluate("Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })")
        share.locator("[data-share-copy]").click()
        manual = share.locator(".pl-share__manual")
        expect(manual).to_be_visible()
        _inside(share.locator(".pl-share__panel").bounding_box(), 390, 844)
        _inside(manual.bounding_box(), 390, 844)
