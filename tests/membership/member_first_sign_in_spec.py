"""BDD specs for the Member Portal launch's activation audience and the first sign-in link."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import override_settings
from django.utils import timezone
from factory.django import mute_signals

from membership.models import Member, login_code_url
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _linked(email: str, status: str = Member.Status.ACTIVE, **user_fields) -> Member:
    member = MemberFactory(status=status)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u{member.pk}", email=email, **user_fields)
    member.user = user
    member.save(update_fields=["user"])
    return member


def describe_awaiting_first_sign_in():
    def it_includes_active_members_with_an_email_whose_account_never_signed_in():
        unlinked = MemberFactory(_pre_signup_email="unlinked@example.com")
        linked_never = _linked("never@example.com")

        assert list(Member.objects.awaiting_first_sign_in()) == [unlinked, linked_never]

    def it_leaves_out_members_who_have_signed_in():
        _linked("seen@example.com", last_login=timezone.now())

        assert not Member.objects.awaiting_first_sign_in().exists()

    def it_leaves_out_members_with_no_email_to_reach():
        MemberFactory(_pre_signup_email="")

        assert not Member.objects.awaiting_first_sign_in().exists()

    def it_leaves_out_members_who_are_not_active():
        MemberFactory(_pre_signup_email="former@example.com", status=Member.Status.FORMER)
        MemberFactory(_pre_signup_email="invited@example.com", status=Member.Status.INVITED)

        assert not Member.objects.awaiting_first_sign_in().exists()

    def it_leaves_out_a_deactivated_account():
        _linked("gone@example.com", is_active=False)

        assert not Member.objects.awaiting_first_sign_in().exists()


def describe_signed_in():
    def it_is_the_active_members_whose_account_has_signed_in():
        seen = _linked("seen@example.com", last_login=timezone.now())
        _linked("never@example.com")
        MemberFactory(_pre_signup_email="unlinked@example.com")
        _linked("gone@example.com", last_login=timezone.now(), is_active=False)
        _linked("former@example.com", status=Member.Status.FORMER, last_login=timezone.now())

        assert list(Member.objects.signed_in()) == [seen]


def describe_login_code_url():
    @override_settings(DEBUG=False)
    def it_prefills_the_email_on_the_login_code_page_over_https():
        url = login_code_url("robin@example.com")

        assert url.startswith("https://")
        assert url.endswith("/accounts/login/code/?email=robin%40example.com")

    @override_settings(DEBUG=True)
    def it_uses_http_in_debug():
        assert login_code_url("robin@example.com").startswith("http://")


def describe_first_sign_in_url():
    def it_provisions_the_member_and_returns_their_prefilled_link():
        member = MemberFactory(_pre_signup_email="newbie@example.com")
        assert member.user_id is None

        url = member.first_sign_in_url()

        member.refresh_from_db()
        assert member.user_id is not None
        assert url.endswith("/accounts/login/code/?email=newbie%40example.com")

    def it_raises_when_the_member_has_no_email():
        member = MemberFactory(_pre_signup_email="")

        with pytest.raises(ValueError, match="no email on file"):
            member.first_sign_in_url()
