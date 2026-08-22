"""Regression: the "Can approve their own discount codes" toggle is admin-edit-only.

A board member asked whether the toggle leaks into the member directory. It never did —
it renders only through ``MemberAdminEditForm`` on the admin Manage Members edit page.
These specs pin that scope so a future form/template change can't quietly surface it.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

_FIELD_NAME = b"can_self_approve_discounts"
_LABEL_TEXT = b"Can approve their own discount codes"


def _create_superuser(client: Client, *, username: str = "admin") -> User:
    user = User.objects.create_superuser(username=username, email=f"{username}@x.com", password="p")
    client.login(username=username, password="p")
    return user


def describe_can_self_approve_discounts_scope():
    def it_does_not_appear_in_the_member_directory(client):
        _create_superuser(client)
        MemberFactory(show_in_directory=True, full_legal_name="Dora Directory")

        response = client.get(reverse("hub_member_directory"))

        assert response.status_code == 200
        assert _FIELD_NAME not in response.content
        assert _LABEL_TEXT not in response.content

    def it_does_not_appear_on_a_members_own_settings_page(client):
        _create_superuser(client)

        response = client.get(reverse("hub_user_settings"))

        assert response.status_code == 200
        assert _FIELD_NAME not in response.content
        assert _LABEL_TEXT not in response.content

    def it_appears_on_the_admin_member_edit_page(client):
        _create_superuser(client)
        target = MemberFactory(full_legal_name="Edith Editable")

        response = client.get(reverse("hub_admin_member_edit", args=[target.pk]))

        assert response.status_code == 200
        assert _FIELD_NAME in response.content
        assert _LABEL_TEXT in response.content
