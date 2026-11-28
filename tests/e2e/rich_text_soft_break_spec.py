"""End-to-end (#1348, feedback request #41): Shift+Enter starts a new line inside the same paragraph.

Quill has no soft break of its own, so ``static/js/rich-editor-init.js`` adds one: the key binding,
the ``<br>`` it inserts, and the clipboard rule that reads a ``<br>`` beside text back as a soft
break when a draft reopens or text is pasted. All of it lives in the browser, which no Django spec
can see. Screenshots of the composer and its Discord Preview go to ``PL_SCREENSHOT_DIR`` when it is
set. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from classes.factories import InstructorFactory, UserFactory
from core.models import TourState
from membership.models import Member
from tests.features import turn_on
from tests.membership.factories import GuildFactory, MemberFactory, MembershipPlanFactory

LEAD_EMAIL = "soft-break-lead@example.com"
EDITOR = '.pl-rte[data-rte-for="id_body"] .ql-editor'
COMPOSER_EDITOR = '.pl-rte-wrap:has(.pl-rte[data-rte-for="id_body"])'
SOFT = '<br class="pl-softbreak">'
TWO_LINES_THEN_A_PARAGRAPH = f"<p>Past Lives Makerspace{SOFT}2808 SE 9th Ave</p><p>See you there.</p>"


def _open_composer(page, live_server, login_via_code) -> None:
    """A guild lead on the announcement composer for their guild."""
    MembershipPlanFactory()
    guild = GuildFactory(name="Writers Guild")
    login_via_code(LEAD_EMAIL)
    guild.guild_lead = Member.objects.get(user__username=LEAD_EMAIL)
    guild.save(update_fields=["guild_lead"])
    page.goto(f"{live_server.url}{reverse('hub_compose')}?audience=guild:{guild.pk}")


def _type_two_lines_then_a_paragraph(page, editor: str = EDITOR) -> None:
    page.locator(editor).click()
    page.keyboard.type("Past Lives Makerspace")
    page.keyboard.press("Shift+Enter")
    page.keyboard.type("2808 SE 9th Ave")
    page.keyboard.press("Enter")
    page.keyboard.type("See you there.")


def _screenshot(page, name: str, selector: str, frame: str = "desktop") -> None:
    """``selector`` in both themes, saved only when ``PL_SCREENSHOT_DIR`` names a folder."""
    folder = os.environ.get("PL_SCREENSHOT_DIR")
    if not folder:
        return
    for theme in ("dark", "light"):
        page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
        page.locator(selector).screenshot(path=str(Path(folder) / f"1348-{name}-{frame}-{theme}.png"))


def _gap_above(page, selector: str) -> float:
    """The computed top margin of the element ``selector`` names, in pixels."""
    return float(page.eval_on_selector(selector, "(el) => parseFloat(getComputedStyle(el).marginTop)"))


def describe_the_announcement_composer():
    def it_keeps_a_shift_enter_line_inside_the_paragraph_and_enter_starts_a_new_one(page, live_server, login_via_code):
        _open_composer(page, live_server, login_via_code)
        _type_two_lines_then_a_paragraph(page)

        expect(page.locator("#id_body")).to_have_value(TWO_LINES_THEN_A_PARAGRAPH)
        expect(page.locator(f"{EDITOR} > p")).to_have_count(2)
        _screenshot(page, "composer", COMPOSER_EDITOR)

        page.get_by_role("button", name="Preview & send").click()
        description = page.locator("[data-discord-preview-description]")
        expect(description).to_have_text("Past Lives Makerspace\n2808 SE 9th Ave\n\nSee you there.")
        _screenshot(page, "discord-preview", "#compose-discord-preview")

    def it_shows_the_new_empty_line_at_once_when_shift_enter_ends_a_line(page, live_server, login_via_code):
        _open_composer(page, live_server, login_via_code)
        page.locator(EDITOR).click()
        page.keyboard.type("Past Lives Makerspace")
        one_line = page.locator(f"{EDITOR} > p").bounding_box()["height"]
        page.keyboard.press("Shift+Enter")

        # Two breaks in the editor: the second holds the empty line open, so the editor grows a line.
        expect(page.locator(f"{EDITOR} > p")).to_have_count(1)
        expect(page.locator(f"{EDITOR} > p > br.pl-softbreak")).to_have_count(2)
        assert page.locator(f"{EDITOR} > p").bounding_box()["height"] > one_line * 1.5
        # The helper break is never saved.
        expect(page.locator("#id_body")).to_have_value(f"<p>Past Lives Makerspace{SOFT}</p>")

        page.keyboard.type("2808 SE 9th Ave")
        expect(page.locator("#id_body")).to_have_value(f"<p>Past Lives Makerspace{SOFT}2808 SE 9th Ave</p>")

    def it_reopens_a_saved_draft_with_the_soft_break_still_soft(page, live_server, login_via_code):
        _open_composer(page, live_server, login_via_code)
        _type_two_lines_then_a_paragraph(page)
        with page.expect_response(lambda r: r.url.endswith(reverse("hub_compose_save_draft")) and r.status == 200):
            page.locator("[data-compose-save-draft]:visible").click()
        expect(page.locator("#compose-draft-pk")).not_to_have_value("")
        pk = int(page.locator("#compose-draft-pk").input_value())

        page.goto(f"{live_server.url}{reverse('hub_compose_resume', args=[pk])}")
        expect(page.locator(f"{EDITOR} > p")).to_have_count(2)
        expect(page.locator(f"{EDITOR} > p").first).to_have_text("Past Lives Makerspace2808 SE 9th Ave")
        expect(page.locator("#id_body")).to_have_value(TWO_LINES_THEN_A_PARAGRAPH)

    def it_keeps_a_pasted_line_break_as_a_soft_break(page, live_server, login_via_code):
        _open_composer(page, live_server, login_via_code)
        page.locator(EDITOR).click()
        page.evaluate(
            """(html) => {
                const data = new DataTransfer();
                data.setData('text/html', html);
                data.setData('text/plain', 'pasted');
                const editor = document.querySelector('.pl-rte[data-rte-for="id_body"] .ql-editor');
                editor.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
            }""",
            "<p>Past Lives Makerspace<br>2808 SE 9th Ave</p><p>See you there.</p>",
        )
        expect(page.locator("#id_body")).to_have_value(TWO_LINES_THEN_A_PARAGRAPH)

    def it_fills_the_push_line_with_a_space_where_the_break_was(page, live_server, login_via_code):
        _open_composer(page, live_server, login_via_code)
        _type_two_lines_then_a_paragraph(page)
        expect(page.locator("#id_push_message")).to_have_value("Past Lives Makerspace 2808 SE 9th Ave See you there.")


def describe_paragraph_gaps_while_typing():
    def it_shows_a_gap_before_a_new_paragraph_and_none_inside_one(page, live_server, login_via_code):
        _open_composer(page, live_server, login_via_code)
        _type_two_lines_then_a_paragraph(page)

        assert _gap_above(page, f"{EDITOR} > p:nth-child(1)") == 0
        assert _gap_above(page, f"{EDITOR} > p:nth-child(2)") >= 10
        # The soft break draws its second line one line height under the first, with no gap.
        lines = page.eval_on_selector(
            f"{EDITOR} > p:nth-child(1)",
            """(p) => {
                const range = document.createRange();
                range.selectNodeContents(p);
                return [...range.getClientRects()].map((r) => r.top);
            }""",
        )
        line_height = float(page.eval_on_selector(f"{EDITOR} > p", "(p) => parseFloat(getComputedStyle(p).lineHeight)"))
        tops = sorted(set(round(top) for top in lines))
        assert len(tops) == 2
        assert tops[1] - tops[0] <= line_height + 1

    def it_shows_the_same_on_a_phone(page, live_server, login_via_code):
        page.set_viewport_size({"width": 390, "height": 844})
        _open_composer(page, live_server, login_via_code)
        _type_two_lines_then_a_paragraph(page)
        assert _gap_above(page, f"{EDITOR} > p:nth-child(2)") >= 10
        _screenshot(page, "composer", COMPOSER_EDITOR, frame="phone")

    def it_keeps_a_run_of_wiki_panel_lines_one_box(page, live_server, login_via_code):
        _open_composer(page, live_server, login_via_code)
        page.evaluate(
            """() => {
                const wrap = document.createElement('div');
                wrap.className = 'pl-rte-wrap';
                wrap.innerHTML = '<div class="ql-editor" id="panel-probe">'
                    + '<p class="pl-panel-info">One</p><p class="pl-panel-info">Two</p></div>';
                document.body.appendChild(wrap);
            }"""
        )
        assert _gap_above(page, "#panel-probe > p:nth-child(2)") == 0


def describe_the_other_editors():
    def it_works_in_a_class_description(page, live_server, login_via_code):
        MembershipPlanFactory()
        email = "soft-break-teacher@example.com"
        user = UserFactory(username=email)
        InstructorFactory(
            user=user,
            full_legal_name="Soft Break Teacher",
            instructor_slug="soft-break-teacher",
            instructor_oriented_at=timezone.now(),
        )
        turn_on("teach")
        login_via_code(email)
        page.goto(f"{live_server.url}{reverse('classes:teach_class_create')}")
        _type_two_lines_then_a_paragraph(page, '.pl-rte[data-rte-for="id_description"] .ql-editor')
        expect(page.locator("#id_description")).to_have_value(TWO_LINES_THEN_A_PARAGRAPH)

    def it_works_in_the_short_home_announcement_editor(page, live_server, login_via_code):
        MembershipPlanFactory()
        email = "soft-break-admin@example.com"
        login_via_code(email)
        user = get_user_model().objects.get(username=email)
        member = cast(Member, Member.objects.filter(user=user).first() or MemberFactory(user=user))
        member.fog_role = Member.FogRole.ADMIN
        member.welcome_dismissed_at = timezone.now()
        member.save(update_fields=["fog_role", "welcome_dismissed_at"])
        member.sync_user_permissions()
        TourState.objects.mark_completed(user, "member-welcome")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.goto(f"{live_server.url}{reverse('hub_admin_spotlight')}")
        page.locator("[data-announcement-add]").click()
        page.wait_for_url("**/manage/spotlight/announcements/new/")
        page.locator(".pl-rte .ql-editor").click()
        page.keyboard.type("Open late")
        page.keyboard.press("Shift+Enter")
        page.keyboard.type("Friday only")
        mount = page.locator(".pl-rte")
        textarea_id = mount.get_attribute("data-rte-for")
        expect(page.locator(f"#{textarea_id}")).to_have_value(f"<p>Open late{SOFT}Friday only</p>")
