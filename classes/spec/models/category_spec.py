"""BDD specs for Category."""

from __future__ import annotations

import pytest

from classes.factories import CategoryFactory
from classes.models import Category


def describe_Category():
    def it_stringifies_as_name(db):
        category = CategoryFactory(name="Woodworking")
        assert str(category) == "Woodworking"

    def it_orders_by_sort_then_name(db):
        CategoryFactory(name="Beta", sort_order=1)
        CategoryFactory(name="Alpha", sort_order=1)
        CategoryFactory(name="Zero", sort_order=0)
        names = list(Category.objects.values_list("name", flat=True))
        assert names == ["Zero", "Alpha", "Beta"]

    def it_enforces_name_uniqueness(db):
        CategoryFactory(name="Pottery")
        with pytest.raises(Exception):
            CategoryFactory(name="Pottery")

    def it_is_labeled_guild_type_for_the_admin(db):
        # User-facing relabel: the model reads "Guild Type"/"Guild Types" in the Django admin.
        assert Category._meta.verbose_name == "Guild Type"
        assert Category._meta.verbose_name_plural == "Guild Types"

    def describe_logo_prefix():
        def it_resolves_from_the_category_name(db):
            category = CategoryFactory(name="Woodworking")
            assert category.logo_prefix == "woodworking"

        def it_prefers_the_linked_guild_name_over_its_own(db):
            from tests.membership.factories import GuildFactory

            guild = GuildFactory(name="Ceramics")
            category = CategoryFactory(name="Pottery & Clay", guild=guild)
            assert category.logo_prefix == "ceramics"

        def it_falls_back_to_its_own_name_when_guild_has_no_logo(db):
            from tests.membership.factories import GuildFactory

            guild = GuildFactory(name="Some Unmapped Guild")
            category = CategoryFactory(name="Glass", guild=guild)
            assert category.logo_prefix == "glass"

        def it_returns_none_when_nothing_matches(db):
            category = CategoryFactory(name="Creative Business")
            assert category.logo_prefix is None
