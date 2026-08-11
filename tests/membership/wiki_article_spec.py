"""BDD specs for the WikiArticle model — slugs, search, snippets, related fill, prev/next, URLs, TOC."""

from __future__ import annotations

import pytest
from django.db.utils import IntegrityError
from django.utils.html import escape

from membership.models import HelpCategory, WikiArticle
from tests.membership.factories import HelpCategoryFactory, WikiArticleFactory

pytestmark = pytest.mark.django_db


def describe_WikiArticle():
    def it_uses_the_title_as_its_string(db):
        assert str(WikiArticleFactory(title="Orientations")) == "Orientations"

    def describe_slug_auto_fill():
        def it_fills_the_slug_from_the_title_when_blank(db):
            assert WikiArticleFactory(title="Taking a class").slug == "taking-a-class"

        def it_falls_back_to_article_when_the_title_has_no_slug_characters(db):
            assert WikiArticleFactory(title="!!!").slug == "article"

        def it_keeps_an_explicit_slug_as_given(db):
            assert WikiArticleFactory(title="Guild voting", slug="voting").slug == "voting"

        def it_keeps_an_existing_slug_when_the_title_changes(db):
            article = WikiArticleFactory(title="Original title")
            original_slug = article.slug
            article.title = "A completely different title"
            article.save()
            assert article.slug == original_slug

    def describe_de_dup_within_a_page():
        def it_appends_a_suffix_to_a_second_matching_auto_slug(db):
            first = WikiArticleFactory(title="Guild voting")
            second = WikiArticleFactory(title="Guild voting")
            assert first.slug == "guild-voting"
            assert second.slug == "guild-voting-2"

        def it_keeps_incrementing_the_suffix_past_the_second_duplicate(db):
            WikiArticleFactory(title="Same")
            WikiArticleFactory(title="Same")
            third = WikiArticleFactory(title="Same")
            assert third.slug == "same-3"

    def it_forbids_two_articles_that_share_an_explicit_slug_on_a_page(db):
        page = WikiArticleFactory(title="First", slug="dup").page
        with pytest.raises(IntegrityError):
            WikiArticleFactory(page=page, title="Second", slug="dup")

    def it_orders_by_sort_order_then_pk(db):
        third = WikiArticleFactory(title="C guide", sort_order=5)
        first = WikiArticleFactory(title="A guide", sort_order=1)
        second = WikiArticleFactory(title="B guide", sort_order=1)
        assert list(WikiArticle.objects.all()) == [first, second, third]

    def describe_published():
        def it_returns_only_published_articles(db):
            live = WikiArticleFactory(title="Live", is_published=True)
            WikiArticleFactory(title="Draft", is_published=False)
            assert list(WikiArticle.objects.published()) == [live]

    def describe_audience():
        def it_delegates_to_the_category(db):
            category = HelpCategoryFactory(audience=HelpCategory.Audience.INSTRUCTOR)
            assert WikiArticleFactory(category=category).audience == HelpCategory.Audience.INSTRUCTOR

        def it_falls_back_to_member_when_uncategorized(db):
            assert WikiArticleFactory(category=None).audience == HelpCategory.Audience.MEMBER

    def describe_search():
        def it_ands_terms_across_title_and_body(db):
            match = WikiArticleFactory(title="Book a slot", body="How an orientation works.")
            WikiArticleFactory(title="Book a slot", body="Nothing else here.")
            assert list(WikiArticle.objects.search("book orientation")) == [match]

        def it_matches_a_whole_string_in_a_single_field(db):
            match = WikiArticleFactory(title="Guild voting", body="Rank three guilds.")
            assert list(WikiArticle.objects.search("voting")) == [match]

        def it_matches_a_term_in_the_category_name(db):
            category = HelpCategoryFactory(name="Teaching")
            match = WikiArticleFactory(title="Run your class", body="Roster and waitlist.", category=category)
            assert list(WikiArticle.objects.search("teaching roster")) == [match]

        def it_excludes_an_article_missing_any_term(db):
            WikiArticleFactory(title="Guild voting", body="Rank three guilds.")
            assert list(WikiArticle.objects.search("voting kiln")) == []

        def it_excludes_drafts(db):
            WikiArticleFactory(title="Kiln safety", is_published=False)
            assert list(WikiArticle.objects.search("kiln")) == []

        def it_excludes_unlisted_slugs(db):
            WikiArticleFactory(title="Instructor orientation", slug="instructor-orientation", is_published=True)
            assert list(WikiArticle.objects.search("instructor orientation")) == []

        def it_returns_nothing_for_an_empty_query(db):
            WikiArticleFactory(title="Guild voting")
            assert list(WikiArticle.objects.search("")) == []

        def it_returns_nothing_for_a_whitespace_query(db):
            WikiArticleFactory(title="Guild voting")
            assert list(WikiArticle.objects.search("   ")) == []

    def describe_search_snippet():
        def it_wraps_the_first_hit_in_mark(db):
            article = WikiArticleFactory(body="The kiln room stays locked overnight.")
            assert "<mark>kiln</mark>" in article.search_snippet("kiln")

        def it_escapes_html_before_the_mark_insertion(db):
            article = WikiArticleFactory(body="Watch out <script>alert('x')</script> near the kiln.")
            snippet = article.search_snippet("kiln")
            assert "<script>" not in snippet
            assert "&lt;script&gt;" in snippet
            assert "<mark>kiln</mark>" in snippet

        def it_clips_a_long_body_to_the_radius_with_ellipses(db):
            article = WikiArticleFactory(body=("start " * 40) + "kiln " + ("end " * 40))
            snippet = article.search_snippet("kiln")
            assert snippet.startswith("…")
            assert snippet.endswith("…")
            assert len(snippet) < len(article.body)

        def it_marks_the_earliest_hit_across_terms(db):
            article = WikiArticleFactory(body="alpha then bravo later.")
            assert article.search_snippet("bravo alpha missing").startswith("<mark>alpha</mark>")

        def it_falls_back_to_the_lead_text_for_title_only_hits(db):
            article = WikiArticleFactory(title="Kiln safety", body="Nothing about that word here.")
            assert article.search_snippet("kiln") == escape(article.lead_text())

        def it_strips_tags_from_a_rich_editor_html_body_before_windowing(db):
            article = WikiArticleFactory(body="<p>The <strong>kiln</strong> room stays locked.</p>")
            snippet = article.search_snippet("kiln")
            assert "<mark>kiln</mark>" in snippet
            assert "<strong>" not in snippet

    def describe_lead_text():
        def it_strips_markdown_from_the_first_paragraph(db):
            article = WikiArticleFactory(body="## Heading {#h}\n\nRead the **full** [guide](/help/) first.")
            assert article.lead_text() == "Read the full guide first."

        def it_skips_blocks_that_strip_to_nothing(db):
            article = WikiArticleFactory(body="![](/static/help/x/01-a.png)\n\nReal lead copy.")
            assert article.lead_text() == "Real lead copy."

        def it_truncates_on_a_word_boundary(db):
            article = WikiArticleFactory(body="alpha bravo charlie delta")
            assert article.lead_text(limit=14) == "alpha bravo…"

        def it_returns_a_short_paragraph_unchanged(db):
            article = WikiArticleFactory(body="Short and sweet.")
            assert article.lead_text() == "Short and sweet."

        def it_returns_empty_for_a_heading_only_body(db):
            article = WikiArticleFactory(body="## Only a heading")
            assert article.lead_text() == ""

        def describe_with_a_rich_editor_html_body():
            def it_strips_tags_from_the_first_paragraph(db):
                article = WikiArticleFactory(body="<p>Read the <strong>full</strong> <a href='/help/'>guide</a>.</p>")
                assert article.lead_text() == "Read the full guide."

            def it_skips_headings_and_empty_paragraphs(db):
                article = WikiArticleFactory(body="<h2>Heading</h2><p><br></p><p>Real lead copy.</p>")
                assert article.lead_text() == "Real lead copy."

            def it_truncates_on_a_word_boundary(db):
                article = WikiArticleFactory(body="<p>alpha bravo charlie delta</p>")
                assert article.lead_text(limit=14) == "alpha bravo…"

            def it_returns_empty_for_a_heading_only_body(db):
                article = WikiArticleFactory(body="<h2>Only a heading</h2>")
                assert article.lead_text() == ""

    def describe_related_for_display():
        def it_returns_explicit_published_picks_first_in_pick_order(db):
            article = WikiArticleFactory(title="Main guide")
            second = WikiArticleFactory(title="Second pick")
            first = WikiArticleFactory(title="First pick")
            article.related_articles.add(first)
            article.related_articles.add(second)
            assert article.related_for_display() == [first, second]

        def it_ignores_unpublished_explicit_picks(db):
            article = WikiArticleFactory(title="Main guide")
            draft = WikiArticleFactory(title="Draft pick", is_published=False)
            live = WikiArticleFactory(title="Live pick")
            article.related_articles.add(draft)
            article.related_articles.add(live)
            assert article.related_for_display() == [live]

        def it_fills_from_the_category_by_sort_order_excluding_self_and_picked(db):
            category = HelpCategoryFactory()
            article = WikiArticleFactory(title="Main", category=category, sort_order=10)
            pick = WikiArticleFactory(title="Pick", category=category, sort_order=40)
            sibling_b = WikiArticleFactory(title="Sib B", category=category, sort_order=30)
            sibling_a = WikiArticleFactory(title="Sib A", category=category, sort_order=20)
            article.related_articles.add(pick)
            assert article.related_for_display() == [pick, sibling_a, sibling_b]

        def it_caps_the_fill_at_the_limit(db):
            category = HelpCategoryFactory()
            article = WikiArticleFactory(title="Main", category=category, sort_order=10)
            siblings = [WikiArticleFactory(category=category, sort_order=20 + n) for n in range(4)]
            assert article.related_for_display() == siblings[:3]

        def it_caps_explicit_picks_at_the_limit(db):
            article = WikiArticleFactory(title="Main guide")
            picks = [WikiArticleFactory(title=f"Pick {n}") for n in range(4)]
            for pick in picks:
                article.related_articles.add(pick)
            assert article.related_for_display() == picks[:3]

        def it_returns_fewer_when_fewer_exist(db):
            category = HelpCategoryFactory()
            article = WikiArticleFactory(title="Main", category=category)
            sibling = WikiArticleFactory(title="Only sibling", category=category)
            assert article.related_for_display() == [sibling]

        def it_uses_explicit_picks_only_when_uncategorized(db):
            article = WikiArticleFactory(title="Main", category=None)
            pick = WikiArticleFactory(title="Pick")
            article.related_articles.add(pick)
            assert article.related_for_display() == [pick]

    def describe_next_and_previous_in_category():
        def it_walks_published_neighbors_by_sort_order(db):
            category = HelpCategoryFactory()
            first = WikiArticleFactory(category=category, sort_order=10)
            second = WikiArticleFactory(category=category, sort_order=20)
            third = WikiArticleFactory(category=category, sort_order=30)
            assert second.next_in_category() == third
            assert second.previous_in_category() == first

        def it_breaks_sort_order_ties_by_pk(db):
            category = HelpCategoryFactory()
            first = WikiArticleFactory(category=category, sort_order=10)
            second = WikiArticleFactory(category=category, sort_order=10)
            assert first.next_in_category() == second
            assert second.previous_in_category() == first

        def it_returns_none_at_the_ends(db):
            category = HelpCategoryFactory()
            first = WikiArticleFactory(category=category, sort_order=10)
            last = WikiArticleFactory(category=category, sort_order=20)
            assert first.previous_in_category() is None
            assert last.next_in_category() is None

        def it_skips_drafts(db):
            category = HelpCategoryFactory()
            first = WikiArticleFactory(category=category, sort_order=10)
            WikiArticleFactory(category=category, sort_order=20, is_published=False)
            third = WikiArticleFactory(category=category, sort_order=30)
            assert first.next_in_category() == third
            assert third.previous_in_category() == first

        def it_returns_none_when_uncategorized(db):
            article = WikiArticleFactory(category=None)
            assert article.next_in_category() is None
            assert article.previous_in_category() is None

    def describe_url_category_segment():
        def it_uses_the_category_slug(db):
            category = HelpCategoryFactory(name="Guilds")
            assert WikiArticleFactory(category=category).url_category_segment == "guilds"

        def it_falls_back_to_more_when_uncategorized(db):
            assert WikiArticleFactory(category=None).url_category_segment == "more"

    def describe_get_absolute_url():
        def it_builds_the_canonical_help_url(db):
            category = HelpCategoryFactory(name="Guilds")
            article = WikiArticleFactory(title="Guild voting", category=category)
            assert article.get_absolute_url() == "/help/guilds/guild-voting/"

        def it_uses_the_more_segment_when_uncategorized(db):
            article = WikiArticleFactory(title="Guild voting", category=None)
            assert article.get_absolute_url() == "/help/more/guild-voting/"

    def describe_toc():
        def it_extracts_h2_and_h3_headings_with_ids(db):
            article = WikiArticleFactory(body="## Alpha {#alpha}\n\ntext\n\n### Beta {#beta}\n\nmore")
            assert article.toc() == [(2, "alpha", "Alpha"), (3, "beta", "Beta")]

        def it_skips_headings_without_ids(db):
            article = WikiArticleFactory(body="## No anchor here\n\n## Anchored {#anchored}")
            assert article.toc() == [(2, "anchored", "Anchored")]

        def it_ignores_h4_headings(db):
            article = WikiArticleFactory(body="#### Deep dive {#deep-dive}")
            assert article.toc() == []

        def it_unescapes_entities_in_the_heading_text(db):
            article = WikiArticleFactory(body="## Kilns & you {#kilns}")
            assert article.toc() == [(2, "kilns", "Kilns & you")]

        def it_returns_empty_when_there_are_no_headings(db):
            article = WikiArticleFactory(body="Just a paragraph.")
            assert article.toc() == []

        def it_returns_empty_for_a_rich_editor_html_body_whose_headings_carry_no_ids(db):
            article = WikiArticleFactory(body="<h2>Alpha</h2><p>text</p><h3>Beta</h3>")
            assert article.toc() == []
