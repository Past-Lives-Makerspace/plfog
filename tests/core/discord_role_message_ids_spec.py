"""Specs for the reaction-role message id list on Site Settings (more than one picker message)."""

from __future__ import annotations

import pytest

from core.models import SiteConfiguration, split_discord_ids

pytestmark = pytest.mark.django_db


def describe_split_discord_ids():
    def it_splits_on_commas_spaces_and_new_lines():
        assert split_discord_ids("111, 222 333\n444,555\t666") == ["111", "222", "333", "444", "555", "666"]

    def it_drops_blanks_from_doubled_and_trailing_separators():
        assert split_discord_ids(" ,111,, 222 , ") == ["111", "222"]

    def it_returns_nothing_for_blank_text():
        assert split_discord_ids("") == []
        assert split_discord_ids("   ") == []


def describe_discord_role_message_ids():
    def _config(main: str, extra: str) -> SiteConfiguration:
        config = SiteConfiguration.load()
        config.discord_role_message_id = main
        config.discord_role_message_extra_ids = extra
        return config

    def it_is_the_main_message_alone_when_the_extra_ids_are_blank():
        assert _config("1301", "").discord_role_message_ids == ["1301"]

    def it_lists_the_main_message_first_then_the_extras():
        assert _config(" 1301 ", "1566, 1777").discord_role_message_ids == ["1301", "1566", "1777"]

    def it_drops_repeats():
        assert _config("1301", "1301 1566 1566").discord_role_message_ids == ["1301", "1566"]

    def it_is_empty_when_the_main_message_is_blank():
        assert _config("", "1566").discord_role_message_ids == []

    def it_defaults_to_blank_in_the_database():
        assert SiteConfiguration.load().discord_role_message_extra_ids == ""
