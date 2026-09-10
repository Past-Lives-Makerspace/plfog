"""BDD specs for the wiki starter templates.

These exist as module data rather than template markup so the starter chooser renders
from one source and spec D can add its Safety card without editing spec A's template.
These specs pin the contract that makes that possible: one entry per kind, every key
present, and bodies that survive the wiki sanitizer they will be saved through.
"""

from __future__ import annotations

import pytest

from membership.markdown import render_wiki_content
from membership.models import REVIEW_INTERVALS, WikiPage
from membership.wiki_starters import STARTERS


def describe_STARTERS():
    def it_covers_every_kind_plus_the_safety_and_blank_starters():
        # Six content starters keyed by their own kind, plus spec D's Safety & Rules card
        # and the Blank page one, whose segments are deliberately not kinds (the brief
        # locks the six).
        assert set(STARTERS) == {kind.value for kind in WikiPage.Kind} | {"safety", "blank"}

    @pytest.mark.parametrize("kind", sorted(STARTERS))
    def it_files_every_starter_under_a_real_kind(kind):
        assert STARTERS[kind]["page_kind"] in WikiPage.Kind.values

    def it_covers_the_same_kinds_as_the_review_intervals():
        assert {starter["page_kind"] for starter in STARTERS.values()} <= set(REVIEW_INTERVALS)
        assert set(REVIEW_INTERVALS) == {kind.value for kind in WikiPage.Kind}

    def it_forces_official_on_the_safety_starter_and_nothing_else():
        # This IS the safety gate's determination rule: safety content is Official
        # content, so there is no separate field and no box for a member to untick.
        assert STARTERS["safety"]["status"] == WikiPage.Status.OFFICIAL
        assert [key for key, value in STARTERS.items() if value["status"]] == ["safety"]

    def it_puts_the_safety_starter_on_a_twelve_month_review_clock():
        assert REVIEW_INTERVALS[STARTERS["safety"]["page_kind"]] == 12

    @pytest.mark.parametrize("kind", sorted(STARTERS))
    def it_supplies_every_key(kind):
        starter = STARTERS[kind]
        assert set(starter) == {"label", "description", "icon", "body", "page_kind", "status"}
        assert starter["label"]
        assert starter["description"]
        assert starter["icon"]

    @pytest.mark.parametrize("kind", sorted(STARTERS))
    def it_pre_seeds_no_quick_answers_rows(kind):
        # A starter carries no fact prompts at all any more. Create mode used to render
        # one list-editor card per prompt between "The Basics" and the editor, labelled
        # "Question: Tools needed" — a label that is not a question, on rows the member
        # never asked for. The one place prompts still make sense is a stub the SEEDER
        # made, where they are the only content, and that list lives in that command.
        assert "fact_prompts" not in STARTERS[kind]

    @pytest.mark.parametrize("kind", sorted(set(STARTERS) - {"blank"}))
    def it_offers_a_body_of_headings_that_survives_the_sanitizer(kind):
        body = STARTERS[kind]["body"]
        assert "<h2>" in body
        rendered = render_wiki_content(body)
        assert "<h2" in rendered

    @pytest.mark.parametrize("kind", sorted(STARTERS))
    def it_gives_every_starter_heading_an_anchor(kind):
        # The TOC chip row reads these ids, so a starter with an id-less heading would
        # ship a page whose own chips scroll nowhere.
        rendered = render_wiki_content(STARTERS[kind]["body"])
        assert rendered.count("<h2") == rendered.count('id="')

    @pytest.mark.parametrize("kind", sorted(STARTERS))
    def it_uses_title_case_headings(kind):
        # FRONTEND.md rule 22. The starter is what most pages will keep forever.
        import re

        for heading in re.findall(r"<h2>(.*?)</h2>", STARTERS[kind]["body"]):
            words = [w for w in heading.split() if w not in {"To", "It", "Is", "With", "From", "You", "I", "I'd"}]
            assert all(word[0].isupper() or not word[0].isalpha() for word in words), heading

    def describe_the_blank_starter():
        def it_opens_an_empty_editor():
            # The whole point of the card: somebody who knows what they are writing should
            # not have to delete four headings before they can start.
            assert STARTERS["blank"]["body"] == ""

        def it_still_files_the_page_under_a_real_kind():
            # The Kind select is on the form and the member can change it. What must not
            # happen is a page filed under a segment that is not a kind at all — WikiPage
            # has no "blank", and kind_label would KeyError on the way back out.
            assert STARTERS["blank"]["page_kind"] == WikiPage.Kind.HOWTO
            assert STARTERS["blank"]["status"] == ""

        def it_comes_last_on_the_chooser():
            # It is the escape hatch, not the recommendation: the guided cards go first.
            assert list(STARTERS)[-1] == "blank"

    def it_keeps_the_project_starter_free_of_a_review_prompt():
        # A project write-up never goes stale, so nothing in its starter should imply
        # someone has to come back and re-confirm it.
        assert REVIEW_INTERVALS["project"] is None
