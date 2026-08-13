"""BDD specs for core.email_prefs — no-login access tokens for notification prefs.

Covers the signed token round-trip (make/read), resolving a user from an email
address (verified allauth alias or the direct User.email fallback), and
``finalize_manage_prefs_link`` swapping the footer placeholder for a real token
(or "" when the send can't be attributed to exactly one known member).
"""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core import mail

from core import email as core_email
from core.email_prefs import (
    PREFS_TOKEN_PLACEHOLDER,
    finalize_manage_prefs_link,
    make_prefs_token,
    read_prefs_token,
    user_for_email,
)
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _member_user(username: str, email: str | None = None) -> User:
    """A User linked to a Member with a verified primary EmailAddress."""
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=email or f"{username}@example.com")


def describe_make_and_read_prefs_token():
    def it_round_trips_to_the_same_user():
        user = _member_user("tok_roundtrip")
        token = make_prefs_token(user)
        resolved = read_prefs_token(token)
        assert resolved == user

    def it_returns_none_for_an_empty_token():
        assert read_prefs_token("") is None

    def it_returns_none_for_a_garbage_token():
        assert read_prefs_token("not-a-real-signed-value") is None

    def it_returns_none_for_a_tampered_token():
        user = _member_user("tok_tamper")
        token = make_prefs_token(user)
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
        assert read_prefs_token(tampered) is None

    def it_returns_none_when_the_signed_user_no_longer_exists():
        user = _member_user("tok_deleted")
        token = make_prefs_token(user)
        user.delete()
        assert read_prefs_token(token) is None


def describe_user_for_email():
    def it_finds_a_user_by_a_verified_allauth_email_address():
        user = _member_user("addr_owner", email="owner@example.com")
        EmailAddress.objects.create(user=user, email="alias@example.com", verified=True, primary=False)
        assert user_for_email("alias@example.com") == user

    def it_matches_case_insensitively():
        user = _member_user("addr_case", email="lowercase@example.com")
        assert user_for_email("LOWERCASE@EXAMPLE.COM") == user

    def it_falls_back_to_the_direct_user_email_when_no_email_address_row_matches():
        user = _member_user("addr_fallback", email="fallback@example.com")
        # Simulate a user with no allauth EmailAddress rows at all — user_for_email
        # must still resolve them from User.email directly.
        EmailAddress.objects.filter(user=user).delete()
        assert user_for_email("fallback@example.com") == user

    def it_returns_none_for_an_unknown_address():
        assert user_for_email("nobody-here@example.com") is None


def describe_finalize_manage_prefs_link():
    def it_replaces_the_placeholder_with_the_recipients_token_when_known_and_solo():
        user = _member_user("finalize_solo", email="solo@example.com")
        text = f"Manage prefs: {PREFS_TOKEN_PLACEHOLDER}"
        html = f"<a href='?t={PREFS_TOKEN_PLACEHOLDER}'>manage</a>"

        new_text, new_html = finalize_manage_prefs_link(text, html, ["solo@example.com"])

        assert PREFS_TOKEN_PLACEHOLDER not in new_text
        assert new_html is not None
        assert PREFS_TOKEN_PLACEHOLDER not in new_html
        token = new_text.removeprefix("Manage prefs: ")
        assert read_prefs_token(token) == user

    def it_empties_the_placeholder_for_a_multi_recipient_send():
        _member_user("finalize_multi_a", email="multi_a@example.com")
        _member_user("finalize_multi_b", email="multi_b@example.com")
        text = f"Manage prefs: {PREFS_TOKEN_PLACEHOLDER}"

        new_text, new_html = finalize_manage_prefs_link(text, None, ["multi_a@example.com", "multi_b@example.com"])

        assert new_text == "Manage prefs: "
        assert new_html is None

    def it_empties_the_placeholder_for_a_non_member_address():
        text = f"Manage prefs: {PREFS_TOKEN_PLACEHOLDER}"

        new_text, _new_html = finalize_manage_prefs_link(text, None, ["stranger@example.com"])

        assert new_text == "Manage prefs: "

    def it_leaves_bodies_unchanged_when_the_placeholder_is_absent():
        text = "No token link in this email."
        html = "<p>No token link in this email.</p>"

        new_text, new_html = finalize_manage_prefs_link(text, html, ["anyone@example.com"])

        assert new_text == text
        assert new_html == html

    def it_leaves_a_none_html_body_as_none_when_only_text_has_the_placeholder():
        _member_user("finalize_textonly", email="textonly@example.com")
        text = f"Manage prefs: {PREFS_TOKEN_PLACEHOLDER}"

        _new_text, new_html = finalize_manage_prefs_link(text, None, ["textonly@example.com"])

        assert new_html is None


def describe_send_personalizes_the_footer_link():
    def it_swaps_the_placeholder_for_a_real_token_link_in_the_sent_email():
        user = _member_user("send_member", email="send_member@example.com")

        core_email.send(
            to="send_member@example.com",
            subject="Hello",
            trigger_kind="test.prefs",
            text_body=f"Body text.\nManage: /settings/?tab=notifications&t={PREFS_TOKEN_PLACEHOLDER}",
            html_body=f"<p>Body</p><a href='/settings/?tab=notifications&t={PREFS_TOKEN_PLACEHOLDER}'>manage</a>",
        )

        message = mail.outbox[0]
        assert PREFS_TOKEN_PLACEHOLDER not in message.body
        assert "t=" in message.body
        token = message.body.rsplit("t=", 1)[1]
        assert read_prefs_token(token) == user

        html_alt = message.alternatives[0][0]
        assert PREFS_TOKEN_PLACEHOLDER not in html_alt
