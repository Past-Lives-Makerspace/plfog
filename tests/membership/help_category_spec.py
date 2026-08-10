"""BDD specs for the HelpCategory model — slug auto-fill, audience, sort_order, and counts."""

from __future__ import annotations

import pytest

from membership.models import HelpCategory
from tests.membership.factories import HelpCategoryFactory, WikiArticleFactory

pytestmark = pytest.mark.django_db


def describe_HelpCategory():
    def it_uses_the_name_and_audience_as_its_string(db):
        category = HelpCategoryFactory(name="Guilds", audience=HelpCategory.Audience.GUILD_LEAD)
        assert str(category) == "Guilds (Guild leads & staff)"

    def it_defaults_the_audience_to_member(db):
        assert HelpCategoryFactory().audience == HelpCategory.Audience.MEMBER

    def it_offers_the_four_audience_choices(db):
        assert [choice for choice, _ in HelpCategory.Audience.choices] == [
            "member",
            "guild_lead",
            "instructor",
            "admin",
        ]

    def describe_slug_auto_fill():
        def it_fills_the_slug_from_the_name_when_blank(db):
            assert HelpCategoryFactory(name="Running a guild").slug == "running-a-guild"

        def it_falls_back_to_category_when_the_name_has_no_slug_characters(db):
            assert HelpCategoryFactory(name="!!!").slug == "category"

        def it_keeps_an_explicit_slug_as_given(db):
            assert HelpCategoryFactory(name="Guilds", slug="the-guilds").slug == "the-guilds"

        def it_keeps_an_existing_slug_when_the_name_changes(db):
            category = HelpCategoryFactory(name="Original name")
            original_slug = category.slug
            category.name = "A completely different name"
            category.save()
            assert category.slug == original_slug

        def it_appends_a_suffix_to_a_second_matching_auto_slug(db):
            first = HelpCategoryFactory(name="Teaching")
            second = HelpCategoryFactory(name="Teaching")
            assert first.slug == "teaching"
            assert second.slug == "teaching-2"

        def it_keeps_incrementing_the_suffix_past_the_second_duplicate(db):
            HelpCategoryFactory(name="Same")
            HelpCategoryFactory(name="Same")
            third = HelpCategoryFactory(name="Same")
            assert third.slug == "same-3"

    def describe_sort_order_auto_assign():
        def it_places_the_first_default_row_at_ten(db):
            assert HelpCategoryFactory().sort_order == 10

        def it_places_a_new_default_row_after_the_current_max(db):
            HelpCategoryFactory(sort_order=70)
            assert HelpCategoryFactory().sort_order == 80

        def it_keeps_an_explicit_sort_order_as_given(db):
            assert HelpCategoryFactory(sort_order=5).sort_order == 5

        def it_does_not_reassign_on_update(db):
            category = HelpCategoryFactory(sort_order=20)
            category.sort_order = 0
            category.save()
            category.refresh_from_db()
            assert category.sort_order == 0

    def it_orders_by_sort_order_then_pk(db):
        third = HelpCategoryFactory(sort_order=50)
        first = HelpCategoryFactory(sort_order=1)
        second = HelpCategoryFactory(sort_order=1)
        assert list(HelpCategory.objects.all()) == [first, second, third]

    def describe_with_published_counts():
        def it_counts_only_published_articles(db):
            category = HelpCategoryFactory()
            WikiArticleFactory(category=category, is_published=True)
            WikiArticleFactory(category=category, is_published=True)
            WikiArticleFactory(category=category, is_published=False)
            annotated = HelpCategory.objects.with_published_counts().get(pk=category.pk)
            assert annotated.published_count == 2

        def it_counts_zero_for_an_empty_category(db):
            category = HelpCategoryFactory()
            annotated = HelpCategory.objects.with_published_counts().get(pk=category.pk)
            assert annotated.published_count == 0

    def describe_nonempty():
        def it_keeps_categories_with_published_articles_and_drops_the_rest(db):
            full = HelpCategoryFactory()
            WikiArticleFactory(category=full, is_published=True)
            drafts_only = HelpCategoryFactory()
            WikiArticleFactory(category=drafts_only, is_published=False)
            HelpCategoryFactory()  # empty
            assert list(HelpCategory.objects.with_published_counts().nonempty()) == [full]
