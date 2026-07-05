"""BDD specs for the home onboarding-checklist dismiss endpoint (``onboarding_dismiss``)."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _linked_user(client: Client, username: str = "u1") -> tuple[User, Member]:
    """A logged-in user with an auto-linked Member (a plan must exist first)."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user, user.member


def _unlinked_user(client: Client, username: str = "nomember") -> User:
    """A logged-in user with no linked Member (unlinked account)."""
    user = User.objects.create_user(username=username, password="pass")
    Member.objects.filter(user=user).delete()
    client.login(username=username, password="pass")
    return user


def _toast(response) -> dict:
    """Parse the ``showToast`` payload from an HTMX response's HX-Trigger header."""
    return json.loads(response["HX-Trigger"])["showToast"]


def describe_onboarding_dismiss():
    def it_stamps_and_returns_empty_200_with_an_info_toast(client: Client):
        _user, member = _linked_user(client, "dismisser")
        assert member.onboarding_dismissed_at is None

        response = client.post(reverse("hub_onboarding_dismiss"))

        # Empty 200 (not 204) so the card's outerHTML swap removes it.
        assert response.status_code == 200
        assert response.content == b""
        member.refresh_from_db()
        assert member.onboarding_dismissed_at is not None
        toast = _toast(response)
        assert toast["type"] == "info"
        assert "finish setup" in toast["message"]

    def it_rejects_get(client: Client):
        _linked_user(client, "getter")

        response = client.get(reverse("hub_onboarding_dismiss"))

        assert response.status_code == 405

    def it_requires_login(client: Client):
        response = client.post(reverse("hub_onboarding_dismiss"))

        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_is_graceful_for_an_unlinked_account(client: Client):
        _unlinked_user(client, "orphan")

        response = client.post(reverse("hub_onboarding_dismiss"))

        # No Member to stamp, but still an empty 200 + toast — no crash.
        assert response.status_code == 200
        assert response.content == b""
        assert _toast(response)["type"] == "info"
