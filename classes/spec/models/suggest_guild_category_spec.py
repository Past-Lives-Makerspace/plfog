"""BDD specs for ClassOffering.suggest_guild_category — the guild-tagging suggester."""

from __future__ import annotations

from classes.factories import CategoryFactory, ClassOfferingFactory
from tests.membership.factories import GuildFactory


def _guild_category(name: str):
    """A guild-linked Category named for one of the keyword rules."""
    return CategoryFactory(name=name, guild=GuildFactory(name=name))


def describe_suggest_guild_category():
    def it_matches_a_keyword_in_the_title(db):
        metal = _guild_category("Metalworking")
        offering = ClassOfferingFactory(title="Blacksmithing 101", slug="bs-101")
        assert offering.suggest_guild_category() == metal

    def it_matches_a_keyword_in_the_description_only(db):
        metal = _guild_category("Metalworking")
        offering = ClassOfferingFactory(title="Intro Session", slug="intro-weld", description="Learn to weld steel.")
        assert offering.suggest_guild_category() == metal

    def it_returns_the_earlier_more_specific_match(db):
        glass = _guild_category("Glass")
        _guild_category("Metalworking")
        offering = ClassOfferingFactory(
            title="Intro to Stained Glass",
            slug="stained-glass",
            description="Bring your own metal tools.",
        )
        # Both Glass and Metalworking match, but Glass is ordered first.
        assert offering.suggest_guild_category() == glass

    def it_returns_none_when_nothing_matches(db):
        _guild_category("Metalworking")
        offering = ClassOfferingFactory(title="Open Social Hour", slug="social", description="Come relax and hang out.")
        assert offering.suggest_guild_category() is None

    def it_falls_through_when_the_matched_category_is_absent(db):
        # Text matches Glass (earlier) and Metalworking, but only Metalworking exists,
        # so the Glass match is skipped and the scan continues to Metalworking.
        metal = _guild_category("Metalworking")
        offering = ClassOfferingFactory(
            title="Stained Glass Suncatchers with Metal Foil",
            slug="stained-metal",
        )
        assert offering.suggest_guild_category() == metal

    def it_uses_a_prebuilt_dict_without_requerying(db):
        # No guild-linked category exists, so the default DB path would return None.
        # Passing a prebuilt dict makes the match succeed — proving the dict is used.
        cat = CategoryFactory(name="Metalworking")
        offering = ClassOfferingFactory(title="Welding Basics", slug="weld-basics")
        assert offering.suggest_guild_category({"Metalworking": cat}) == cat
