"""BDD specs for the WikiArticle model — slug auto-fill, de-dup, ordering, and published()."""

from __future__ import annotations

import pytest
from django.db.utils import IntegrityError

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
