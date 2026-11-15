"""Regression: the "Can approve their own discount codes" toggle is gone (#1185).

Instructors make live codes themselves (#1184), so nobody approves their own; the admin member
edit page no longer offers the toggle.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def describe_the_retired_self_approve_toggle():
    def it_is_not_on_the_admin_member_edit_page(client: Client):
        User.objects.create_superuser(username="admin", email="admin@x.com", password="p")
        client.login(username="admin", password="p")
        target = MemberFactory(full_legal_name="Edith Editable")

        response = client.get(reverse("hub_admin_member_edit", args=[target.pk]))

        assert response.status_code == 200
        assert b"can_self_approve_discounts" not in response.content
