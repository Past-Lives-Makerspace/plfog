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
    def it_covers_every_kind_plus_the_safety_starter():
        # Six content starters keyed by their own kind, plus spec D's Safety & Rules card,
        # whose segment is deliberately not a kind (the brief locks the six).
        assert set(STARTERS) == {kind.value for kind in WikiPage.Kind} | {"safety"}

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
        assert set(starter) == {"label", "description", "icon", "fact_prompts", "body", "page_kind", "status"}
        assert starter["label"]
        assert starter["description"]
        assert starter["icon"]

    @pytest.mark.parametrize("kind", sorted(STARTERS))
    def it_prompts_for_at_least_two_facts(kind):
        # The Quick Answers block is the most useful thing on the page, so the starter
        # has to ask for it before it asks for prose.
        assert len(STARTERS[kind]["fact_prompts"]) >= 2

    @pytest.mark.parametrize("kind", sorted(STARTERS))
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

    def it_names_the_machine_facts_a_member_actually_wants():
        assert "Blade or bit" in STARTERS["machine"]["fact_prompts"]

    def it_keeps_the_project_starter_free_of_a_review_prompt():
        # A project write-up never goes stale, so nothing in its starter should imply
        # someone has to come back and re-confirm it.
        assert REVIEW_INTERVALS["project"] is None
