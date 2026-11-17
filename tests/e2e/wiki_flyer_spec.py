"""End-to-end: a wiki page's printable flyer (#1224).

An editor opens the Flyer from the Share This Page card; it opens in its own tab and the sheet
shows the title, the Quick Answers and the QR. Then the longest page the editor allows (a 200
character title, a 100 character guild name, the longest machine name, twelve Quick Answers at
their longest) must show each answer whole or not at all, count the ones left off, hide its
toolbar in print, and print as one Letter page. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import re

from django.contrib.auth.models import User
from django.urls import reverse
from playwright.sync_api import Page, expect

from membership.models import Member, WikiPage
from tests.features import turn_on
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    MembershipPlanFactory,
    WikiPageFactFactory,
    WikiPageFactory,
)

EDITOR_EMAIL = "wiki-flyer-editor@example.com"
PDF_PAGE = re.compile(rb"/Type\s*/Page(?!s)")

#: Whether the sheet's body holds every line, and where each Quick Answer row landed: whole inside the list
#: ("in"), wrapped whole into the clipped column right of it ("out"), or cut ("cut", never allowed).
MEASURE = """() => {
    const body = document.querySelector('.pl-wkfl__body');
    const grid = document.querySelector('.pl-wkfl__grid').getBoundingClientRect();
    const rows = [...document.querySelectorAll('[data-wkfl-fact]')].map((row) => {
        const box = row.getBoundingClientRect();
        if (box.left >= grid.right - 1) return 'out';
        return box.right <= grid.right + 1 && box.bottom <= grid.bottom + 1 ? 'in' : 'cut';
    });
    return {fits: body.scrollHeight <= body.clientHeight + 1, rows};
}"""


def _editor() -> None:
    MembershipPlanFactory()
    member = User.objects.create_user(username=EDITOR_EMAIL, email=EDITOR_EMAIL).member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Juniper Ashdown"
    member.save(update_fields=["status", "full_legal_name"])
    member.sync_user_permissions()


def _machine_page(*, title: str, guild_name: str, machine: str, facts: list[tuple[str, str]]) -> WikiPage:
    tool = EquipmentFactory(name=machine, guild=GuildFactory(name=guild_name))
    page = WikiPageFactory(title=title, kind=WikiPage.Kind.MACHINE, equipment=tool, guild=tool.guild)
    for index, (label, value) in enumerate(facts):
        WikiPageFactFactory(page=page, label=label, value=value, sort_order=index)
    return page


def describe_the_wiki_flyer():
    def it_opens_from_the_share_card_in_its_own_tab(live_server, page: Page, login_via_code):
        turn_on("wiki")
        _editor()
        wiki_page = _machine_page(
            title="Table Saw",
            guild_name="Woodworking",
            machine="SawStop PCS",
            facts=[("Blade", "10 inch, 40 tooth"), ("Max rip", "30 inches"), ("Dust port", "")],
        )
        page.set_viewport_size({"width": 1280, "height": 900})
        login_via_code(EDITOR_EMAIL)
        page.goto(f"{live_server.url}{wiki_page.get_absolute_url()}")

        flyer = page.locator(".pl-wp-qrshare [data-qr-share-flyer]")
        expect(flyer).to_be_visible()
        expect(flyer).to_have_attribute("target", "_blank")
        with page.context.expect_page() as opened:
            flyer.click()
        sheet = opened.value
        sheet.wait_for_load_state()

        assert sheet.url == f"{live_server.url}{reverse('hub_wiki_flyer', args=[wiki_page.slug])}"
        expect(sheet.locator("[data-wkfl-title]")).to_have_text("Table Saw")
        expect(sheet.locator("[data-wkfl-scope]")).to_have_text("Woodworking")
        expect(sheet.locator("[data-wkfl-fact]")).to_have_count(2)
        expect(sheet.locator(".pl-wkfl__qr svg")).to_be_visible()
        expect(sheet.locator("[data-wkfl-url]")).to_have_text(wiki_page.sticker_url_typed)

    def it_fits_the_longest_page_on_one_letter_sheet(live_server, page: Page, login_via_code):
        turn_on("wiki")
        _editor()
        long_value = ("Use the push stick for anything under six inches and keep the guard down " * 3)[:200]
        wiki_page = _machine_page(
            title=("SawStop Professional Cabinet Saw with the Outfeed Table and the Dado Insert " * 3)[:200],
            guild_name=("Woodworking and Furniture Making Guild of Past Lives " * 2)[:100],
            machine=("SawStop PCS 3 HP Professional Cabinet Saw " * 3)[:120],
            facts=[((f"Question {index} about the saw " * 3)[:60], long_value) for index in range(12)],
        )
        page.set_viewport_size({"width": 1280, "height": 1400})
        login_via_code(EDITOR_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_wiki_flyer', args=[wiki_page.slug])}")
        expect(page.locator(".pl-wkfl__qr svg")).to_be_visible()
        expect(page.locator("[data-wkfl-fact]")).to_have_count(8)
        page.evaluate("document.fonts.ready")

        for media in ("screen", "print"):
            page.emulate_media(media=media)
            measured = page.evaluate(MEASURE)
            assert measured["fits"], media
            assert "cut" not in measured["rows"], (media, measured["rows"])
            shown = measured["rows"].count("in")
            assert shown >= 1, media
            # The four the view left off plus every row the sheet had no room for.
            expect(page.locator("[data-wkfl-more]")).to_have_text(f"{12 - shown} more Quick Answers on the full page")

        expect(page.locator(".pl-evfl-page__toolbar")).to_be_hidden()
        pdf = page.pdf(format="Letter", print_background=True, prefer_css_page_size=True)
        assert len(PDF_PAGE.findall(pdf)) == 1, f"the flyer printed {len(PDF_PAGE.findall(pdf))} pages"
