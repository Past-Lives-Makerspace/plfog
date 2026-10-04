"""Specs for ``membership.names`` (#617): an account is named by its member, never "email · email"."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser, User

from membership.models import Member
from membership.names import user_display_name, user_label, user_name_or_email
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _account(
    username: str, *, email: str = "", first: str = "", last: str = "", legal: str = "", preferred: str = ""
) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=email, first_name=first, last_name=last)
    Member.objects.filter(user=user).update(full_legal_name=legal, preferred_name=preferred)
    return User.objects.select_related("member").get(pk=user.pk)


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
