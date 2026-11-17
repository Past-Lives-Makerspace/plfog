"""Specs for ``membership.names`` (#617): an account is named by its member, never "email · email"."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser, User

from membership.models import Member
from membership.names import (
    greeting_form,
    legal_initial_form,
    member_label,
    user_display_name,
    user_label,
    user_name_or_email,
)
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _account(
    username: str, *, email: str = "", first: str = "", last: str = "", legal: str = "", preferred: str = ""
) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=email, first_name=first, last_name=last)
    Member.objects.filter(user=user).update(full_legal_name=legal, preferred_name=preferred)
    return User.objects.select_related("member").get(pk=user.pk)


def describe_member_label():
    def it_lists_the_preferred_name_with_the_legal_name_beside_it():
        assert member_label("Wren", "Quinn Ashdown") == "Wren (Quinn Ashdown)"

    def it_is_the_legal_name_alone_without_a_preferred_name():
        assert member_label("", "Quinn Ashdown") == "Quinn Ashdown"

    def it_is_one_name_when_the_two_are_the_same_name():
        assert member_label("  quinn ASHDOWN ", "Quinn Ashdown") == "quinn ASHDOWN"

    def it_is_the_preferred_name_alone_without_a_legal_name():
        assert member_label("Wren", "  ") == "Wren"

    def it_is_blank_with_no_name_at_all():
        assert member_label("", "") == ""

    def it_is_the_legal_name_alone_when_it_starts_with_the_preferred_name():
        assert member_label(" sam ", "Sam Reyes") == "Sam Reyes"

    def it_lists_both_when_the_preferred_name_is_only_the_start_of_a_word():
        assert member_label("Sam", "Samantha Reyes") == "Sam (Samantha Reyes)"

    def describe_the_initial_form():
        def it_gives_the_legal_first_name_and_last_initial():
            assert member_label("Wren", "Quinn Lee Ashdown", form="initial") == "Wren (Quinn A.)"

        def it_is_the_legal_initial_form_alone_when_it_starts_with_the_preferred_name():
            assert member_label("Sam", "Sam Reyes", form="initial") == "Sam R."

        def it_keeps_a_one_word_legal_name_whole():
            assert member_label("Wren", "Quinn", form="initial") == "Wren (Quinn)"

        def it_is_the_legal_name_whole_without_a_preferred_name():
            assert member_label("", "Quinn Ashdown", form="initial") == "Quinn Ashdown"

        def it_is_the_preferred_name_alone_when_the_two_match():
            assert member_label("Quinn Ashdown", "quinn ashdown", form="initial") == "Quinn Ashdown"


def describe_legal_initial_form():
    def it_keeps_the_first_name_and_the_last_names_initial():
        assert legal_initial_form("  Quinn  Lee Ashdown ") == "Quinn A."

    def it_keeps_one_word_whole():
        assert legal_initial_form("Quinn") == "Quinn"

    def it_is_blank_for_no_name():
        assert legal_initial_form("") == ""


def describe_greeting_form():
    def it_keeps_the_preferred_name_of_a_label():
        assert greeting_form("Wren (Quinn Ashdown)") == "Wren"

    def it_leaves_a_plain_name_as_it_is():
        assert greeting_form("Quinn Ashdown") == "Quinn Ashdown"

    def it_leaves_a_name_with_brackets_inside_it_as_it_is():
        assert greeting_form("Sam (Sammy) Reyes") == "Sam (Sammy) Reyes"

    def it_leaves_a_name_that_opens_with_a_bracket_as_it_is():
        assert greeting_form("(Wren)") == "(Wren)"


def describe_user_display_name():
    def it_uses_the_member_display_name_when_the_account_has_no_name():
        user = _account("dana@example.com", email="dana@example.com", legal="Dana Weaver")
        assert user_display_name(user) == "Dana Weaver"

    def it_prefers_the_members_preferred_name():
        user = _account("dee@example.com", email="dee@example.com", legal="Deirdre Weaver", preferred="Dee")
        assert user_display_name(user) == "Dee"

    def it_prefers_the_member_name_over_the_account_name():
        user = _account("acct@example.com", email="acct@example.com", first="Old", last="Name", legal="New Name")
        assert user_display_name(user) == "New Name"

    def it_falls_back_to_the_account_full_name_without_a_member_name():
        user = _account("sam@example.com", email="sam@example.com", first="Sam", last="Reyes")
        Member.objects.filter(user=user).update(full_legal_name="", preferred_name="")
        user = User.objects.select_related("member").get(pk=user.pk)
        assert user_display_name(user) == "Sam Reyes"

    def it_falls_back_to_the_account_full_name_without_a_member():
        user = _account("solo@example.com", email="solo@example.com", first="Solo", last="Acct")
        Member.objects.filter(user=user).delete()
        user = User.objects.get(pk=user.pk)
        assert user_display_name(user) == "Solo Acct"

    def it_is_blank_when_there_is_no_name_anywhere():
        user = _account("nobody@example.com", email="nobody@example.com")
        assert user_display_name(user) == ""

    def it_is_blank_for_an_anonymous_user():
        assert user_display_name(AnonymousUser()) == ""


def describe_user_label():
    def it_reads_name_then_email():
        user = _account("dana@example.com", email="dana@example.com", legal="Dana Weaver")
        assert user_label(user) == "Dana Weaver · dana@example.com"

    def it_shows_the_email_once_when_there_is_no_name():
        user = _account("nobody@example.com", email="nobody@example.com")
        assert user_label(user) == "nobody@example.com"

    def it_shows_the_email_once_when_the_name_is_the_email():
        user = _account("same@example.com", email="same@example.com", legal="SAME@example.com")
        assert user_label(user) == "same@example.com"

    def it_shows_the_name_alone_without_an_email():
        user = _account("noemail", legal="No Email")
        assert user_label(user) == "No Email"

    def it_shows_the_username_with_neither_name_nor_email():
        user = _account("bare-username")
        assert user_label(user) == "bare-username"


def describe_user_name_or_email():
    def it_is_the_name_when_there_is_one():
        user = _account("dana@example.com", email="dana@example.com", legal="Dana Weaver")
        assert user_name_or_email(user) == "Dana Weaver"

    def it_is_the_email_without_a_name():
        user = _account("someuser", email="nobody@example.com")
        assert user_name_or_email(user) == "nobody@example.com"

    def it_is_the_username_with_neither():
        user = _account("bare-username")
        assert user_name_or_email(user) == "bare-username"
