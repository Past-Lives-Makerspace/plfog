"""Full-stack login-by-code specs proving a deactivated account cannot sign in.

Drives the real allauth request-code -> confirm-code flow through the Django test
client. A self-service-deleted member has ``user.is_active=False``; neither the real
emailed code nor the app-store golden ticket may complete a login for them. An active
account still logs in with the golden ticket (regression guard for the reviewer account).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

GOLDEN = "59157bd9bbf9873fd724ec09eb13bbd2"


def _login_ready_user(username: str):
    """A Member linked to an active User with a verified primary email (login-by-code ready)."""
    member = MemberFactory(_pre_signup_email=f"{username}@example.com")
    provision_user_for_member(member)
    return member.user


def _request_login_code(client: Client, email: str, *, code: str):
    """Post the request-code step, forcing allauth to stash a known code for confirmation."""
    with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value=code):
        return client.post(reverse("account_request_login_code"), {"email": email})


def _confirm_login_code(client: Client, code: str):
    return client.post(reverse("account_confirm_login_code"), {"code": code})


def describe_login_by_code_for_a_deactivated_account():
    def it_blocks_the_real_code_when_the_account_is_deactivated_mid_login(monkeypatch):
        monkeypatch.delenv("PLAY_REVIEW_CODE", raising=False)
        user = _login_ready_user("real_inactive")
        client = Client()
        _request_login_code(client, user.email, code="ABCDEF")

        user.is_active = False
        user.save(update_fields=["is_active"])
        response = _confirm_login_code(client, "ABCDEF")

        assert "_auth_user_id" not in client.session
        assert reverse("account_inactive") in response["Location"]

    def it_blocks_the_golden_ticket_when_the_account_is_deactivated_mid_login(monkeypatch):
        monkeypatch.setenv("PLAY_REVIEW_CODE", GOLDEN)
        user = _login_ready_user("golden_inactive")
        client = Client()
        _request_login_code(client, user.email, code="ABCDEF")

        user.is_active = False
        user.save(update_fields=["is_active"])
        _confirm_login_code(client, GOLDEN)

        assert "_auth_user_id" not in client.session


def describe_login_by_code_for_an_active_account():
    def it_still_logs_in_with_the_golden_ticket(monkeypatch):
        monkeypatch.setenv("PLAY_REVIEW_CODE", GOLDEN)
        user = _login_ready_user("golden_active")
        client = Client()
        _request_login_code(client, user.email, code="ABCDEF")

        _confirm_login_code(client, GOLDEN)

        assert client.session.get("_auth_user_id") == str(user.pk)
