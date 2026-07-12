"""BDD specs for membership.selectors.member_for_verified_email."""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from factory.django import mute_signals

from membership.selectors import member_for_verified_email
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _linked_member(email: str, *, verified: bool = True):
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u_{email}", email=email)
    member = MemberFactory()
    member.user = user
    member.save(update_fields=["user"])
    EmailAddress.objects.create(user=user, email=email, verified=verified, primary=True)
    return member


def describe_member_for_verified_email():
    def it_returns_the_member_for_a_verified_email():
        member = _linked_member("jo@example.com")
        assert member_for_verified_email("jo@example.com") == member

    def it_is_case_insensitive():
        member = _linked_member("Jo@Example.com")
        assert member_for_verified_email("jo@example.com") == member

    def it_returns_none_when_the_email_is_unverified():
        _linked_member("jo@example.com", verified=False)
        assert member_for_verified_email("jo@example.com") is None

    def it_returns_none_when_no_account_matches():
        assert member_for_verified_email("nobody@example.com") is None

    def it_does_not_find_a_pre_signup_member_without_a_linked_user():
        # A Member with no linked User has no EmailAddress → never resolves.
        MemberFactory(_pre_signup_email="pre@example.com")
        assert member_for_verified_email("pre@example.com") is None
