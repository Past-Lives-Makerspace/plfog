"""End-to-end: the wiki editor's Confluence-style blocks survive a save and a reopen.

A panel, a checklist, a settings block and a divider each cross three hands: Quill writes
them, ``sanitize_wiki_html`` stores them, and Quill reads them back when the editor
reopens from the server-rendered seed. Only a browser proves the last step, because the
unit specs never run Quill. Run on PostgreSQL, the way CI does::

    DATABASE_URL="postgres://plfog:plfog@localhost:5433/plfog" \
        pytest tests/e2e/wiki_blocks_spec.py -m e2e --no-cov -o addopts="" -q
"""

from __future__ import annotations

from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import WikiPage
from membership.wiki_starters import STARTERS
from tests.features import turn_on
from tests.membership.factories import MembershipPlanFactory

MEMBER_EMAIL = "wiki-blocks@example.com"


def _authored_page(body: str) -> WikiPage:
    return WikiPage.objects.create(title="Laser Settings", kind="reference", body=body, body_edited_at=timezone.now())


def _pick_panel(page, value: str) -> None:
    page.locator(".ql-toolbar .ql-panel .ql-picker-label").click()
    page.locator(f'.ql-toolbar .ql-panel .ql-picker-item[data-value="{value}"]').click()


def describe_wiki_editor_blocks():
    def it_keeps_every_block_through_a_save_and_a_reopen(live_server, page, login_via_code):
        MembershipPlanFactory()
        turn_on("wiki")
        wiki_page = _authored_page("<p>Start here.</p>")
        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_wiki_edit', args=[wiki_page.slug])}")

        editor = page.locator(".ql-editor")
        editor.click()
        page.keyboard.press("Control+End")
        page.keyboard.press("Enter")
        _pick_panel(page, "warning")
        page.keyboard.type("Wear goggles at the laser.")
        page.keyboard.press("Enter")
        # Enter carries the panel onto the new line, so the next block starts by clearing it.
        page.locator(".ql-toolbar .ql-panel .ql-picker-label").click()
        page.locator(".ql-toolbar .ql-panel .ql-picker-item:not([data-value])").click()
        page.locator('.ql-toolbar button.ql-list[value="check"]').click()
        page.keyboard.type("Turn on the extractor")
        page.keyboard.press("Enter")
        page.keyboard.press("Enter")
        page.locator(".ql-toolbar button.ql-code-block").click()
        page.keyboard.type("Power 80%")
        # Toggle it off on the next line, or the button takes the typed line back out of it.
        page.keyboard.press("Enter")
        page.locator(".ql-toolbar button.ql-code-block").click()
        page.locator(".ql-toolbar button.ql-divider").click()
        page.keyboard.type("After the line.")

        page.get_by_role("button", name="Save", exact=True).click()
        expect(page).to_have_url(f"{live_server.url}{wiki_page.get_absolute_url()}")

        body = page.locator(".pl-wp-body")
        expect(body.locator("p.pl-panel-warning")).to_have_text("Wear goggles at the laser.")
        expect(body.locator('ul.pl-checklist > li[data-list="unchecked"]')).to_have_text("Turn on the extractor")
        expect(body.locator("pre")).to_have_text("Power 80%")
        expect(body.locator("hr")).to_have_count(1)

        # Reopen: Quill must rebuild each block from the stored HTML, not flatten it.
        page.goto(f"{live_server.url}{reverse('hub_wiki_edit', args=[wiki_page.slug])}")
        expect(editor.locator("p.pl-panel-warning")).to_have_text("Wear goggles at the laser.")
        expect(editor.locator('li[data-list="unchecked"]')).to_have_text("Turn on the extractor")
        expect(editor.locator(".ql-code-block")).to_have_text("Power 80%")
        expect(editor.locator("hr")).to_have_count(1)

    def it_shows_a_starters_prompts_in_the_editor_and_drops_them_from_the_page(live_server, page, login_via_code):
        MembershipPlanFactory()
        turn_on("wiki")
        wiki_page = _authored_page(STARTERS["machine"]["body"].replace("<p></p>", "<p>Cuts wood.</p>", 1))
        login_via_code(MEMBER_EMAIL)

        page.goto(f"{live_server.url}{wiki_page.get_absolute_url()}")
        expect(page.locator(".pl-wp-body")).to_contain_text("Cuts wood.")
        expect(page.locator(".pl-wp-body .pl-panel-danger")).to_have_count(0)
        expect(page.locator(".pl-wp-body .pl-checklist")).to_have_count(0)

        page.goto(f"{live_server.url}{reverse('hub_wiki_edit', args=[wiki_page.slug])}")
        danger = page.locator(".ql-editor p.pl-panel-danger")
        expect(danger).to_have_count(1)
        hint = danger.evaluate("el => getComputedStyle(el, '::before').content")
        assert "What can hurt you" in hint, hint
