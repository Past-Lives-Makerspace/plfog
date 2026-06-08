"""BDD-style tests for cross-surface session relay (relay_issue / relay_accept)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import Client

from classes.factories import UserFactory

User = get_user_model()


@pytest.fixture(autouse=True)
def _relay_settings(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    settings.ALLOWED_HOSTS = ["book.pastlives.space", "members.pastlives.space", "testserver"]
    settings.MEMBER_HOST = "members.pastlives.space"


@pytest.fixture
def members_client():
    return Client(HTTP_HOST="members.pastlives.space")


@pytest.fixture
def book_client():
    return Client(HTTP_HOST="book.pastlives.space")


def describe_relay_issue():
    def it_redirects_authenticated_user_to_book_accept_url(members_client, db):
        user = UserFactory()
        members_client.force_login(user)
        resp = members_client.get(
            "/auth/relay/",
            {"book_host": "book.pastlives.space", "next": "/account/"},
        )
        assert resp.status_code == 302
        location = resp["Location"]
        assert "book.pastlives.space/auth/relay/accept/" in location
        assert "token=" in location
        assert "next=" in location

    def it_redirects_unauthenticated_user_to_book_login(members_client, db):
        resp = members_client.get(
            "/auth/relay/",
            {"book_host": "book.pastlives.space", "next": "/account/"},
        )
        assert resp.status_code == 302
        location = resp["Location"]
        assert "book.pastlives.space/accounts/login/" in location

    def it_rejects_invalid_book_host(members_client, db):
        resp = members_client.get(
            "/auth/relay/",
            {"book_host": "evil.example.com", "next": "/account/"},
        )
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]
        assert "evil.example.com" not in resp["Location"]

    def it_rejects_missing_book_host(members_client, db):
        resp = members_client.get("/auth/relay/", {"next": "/account/"})
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]

    def it_rejects_book_host_with_at_sign(members_client, db):
        resp = members_client.get(
            "/auth/relay/",
            {"book_host": "user@book.pastlives.space", "next": "/account/"},
        )
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]

    def it_rejects_book_host_with_path(members_client, db):
        resp = members_client.get(
            "/auth/relay/",
            {"book_host": "book.pastlives.space/evil", "next": "/account/"},
        )
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]

    def it_resets_unsafe_next_to_root(members_client, db):
        user = UserFactory()
        members_client.force_login(user)
        resp = members_client.get(
            "/auth/relay/",
            {"book_host": "book.pastlives.space", "next": "http://evil.com/steal"},
        )
        assert resp.status_code == 302
        location = resp["Location"]
        assert "next=/" in location or "next=%2F" in location


def describe_relay_accept():
    def it_logs_in_user_from_valid_token(book_client, db):
        user = UserFactory()
        token = signing.dumps(user.pk, salt="session-relay")
        resp = book_client.get(
            "/auth/relay/accept/",
            {"token": token, "next": "/account/"},
        )
        assert resp.status_code == 302
        assert resp["Location"] == "/account/"

    def it_redirects_to_login_on_expired_token(book_client, db):
        from unittest.mock import patch
        from django.core import signing as django_signing

        user = UserFactory()
        token = signing.dumps(user.pk, salt="session-relay")
        with patch.object(django_signing, "loads", side_effect=django_signing.SignatureExpired("expired")):
            resp = book_client.get(
                "/auth/relay/accept/",
                {"token": token, "next": "/account/"},
            )
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]

    def it_redirects_to_login_on_bad_signature(book_client, db):
        resp = book_client.get(
            "/auth/relay/accept/",
            {"token": "not-a-valid-token", "next": "/account/"},
        )
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]

    def it_redirects_to_login_when_user_not_found(book_client, db):
        token = signing.dumps(99999999, salt="session-relay")
        resp = book_client.get(
            "/auth/relay/accept/",
            {"token": token, "next": "/account/"},
        )
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]

    def it_resets_unsafe_next_to_root(book_client, db):
        user = UserFactory()
        token = signing.dumps(user.pk, salt="session-relay")
        resp = book_client.get(
            "/auth/relay/accept/",
            {"token": token, "next": "http://evil.com/steal"},
        )
        assert resp.status_code == 302
        assert resp["Location"] == "/"
