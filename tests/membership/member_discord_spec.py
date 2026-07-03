"""Member Discord-DM linking: the verified-id fields, the property, and link/unlink."""

from __future__ import annotations

import pytest

from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def describe_Member_discord_linking():
    def describe_discord_is_linked():
        def it_is_false_by_default():
            member = MemberFactory()
            assert member.discord_user_id == ""
            assert member.discord_linked_at is None
            assert member.discord_is_linked is False

        def it_is_true_once_a_discord_user_id_is_set():
            member = MemberFactory(discord_user_id="123456789012345678")
            assert member.discord_is_linked is True

    def describe_link_discord():
        def it_stores_the_id_and_stamps_linked_at():
            member = MemberFactory()
            member.link_discord("123456789012345678")
            member.refresh_from_db()
            assert member.discord_user_id == "123456789012345678"
            assert member.discord_linked_at is not None
            assert member.discord_is_linked is True

        def it_strips_surrounding_whitespace_from_the_id():
            member = MemberFactory()
            member.link_discord("  42  ")
            member.refresh_from_db()
            assert member.discord_user_id == "42"

        def it_fills_a_blank_handle_from_the_linked_account():
            member = MemberFactory(discord_handle="")
            member.link_discord("123", handle="makerjo")
            member.refresh_from_db()
            assert member.discord_handle == "makerjo"

        def it_never_overwrites_a_handle_the_member_typed():
            member = MemberFactory(discord_handle="my_own_tag")
            member.link_discord("123", handle="makerjo")
            member.refresh_from_db()
            assert member.discord_handle == "my_own_tag"

        def it_leaves_the_handle_untouched_when_none_is_supplied():
            member = MemberFactory(discord_handle="")
            member.link_discord("123")
            member.refresh_from_db()
            assert member.discord_handle == ""

    def describe_unlink_discord():
        def it_clears_the_id_and_the_timestamp():
            member = MemberFactory()
            member.link_discord("999")
            member.unlink_discord()
            member.refresh_from_db()
            assert member.discord_user_id == ""
            assert member.discord_linked_at is None
            assert member.discord_is_linked is False
