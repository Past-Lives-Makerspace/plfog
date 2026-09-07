"""BDD specs for the post-deletion confirmation page (hub.views.account_deleted).

Public and anonymous-accessible: the member is signed out before landing here, so the
page must render for an anonymous visitor without bouncing to the login screen, and
confirm the deletion on its own (no reliance on a flash message).
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def describe_account_deleted():
    def it_renders_for_an_anonymous_user():
        response = Client().get(reverse("hub_account_deleted"))

        assert response.status_code == 200
        assert b"Your account has been deleted" in response.content

    def it_does_not_redirect_to_login():
        response = Client().get(reverse("hub_account_deleted"))

        assert response.status_code == 200
        assert "/accounts/login/" not in response.get("Location", "")
