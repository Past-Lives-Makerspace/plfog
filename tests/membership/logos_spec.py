"""BDD specs for the guild/category logo-prefix helper."""

from __future__ import annotations

from membership.logos import logo_prefix_for


def describe_logo_prefix_for():
    def it_matches_a_guild_name_substring():
        assert logo_prefix_for("Woodworking") == "woodworking"

    def it_is_case_insensitive():
        assert logo_prefix_for("CERAMICS") == "ceramics"

    def it_maps_jewelry_category_to_jewelers_logo():
        assert logo_prefix_for("Jewelry") == "jewelers"

    def it_maps_writing_category_to_writers_logo():
        assert logo_prefix_for("Writing") == "writers"

    def it_returns_none_when_unmatched():
        assert logo_prefix_for("Quantum Computing") is None

    def it_returns_none_when_empty():
        assert logo_prefix_for(None) is None
        assert logo_prefix_for("") is None

    def it_returns_none_for_unknown_names():
        assert logo_prefix_for("Creative Business") is None

    def it_returns_none_for_blank():
        assert logo_prefix_for("") is None
        assert logo_prefix_for(None) is None
