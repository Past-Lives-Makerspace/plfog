"""BDD specs for the teaching-portal discount codes page: own (editable) vs site-wide (read-only)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import DiscountCodeFactory, InstructorFactory, UserFactory
from classes.models import DiscountCode

pytestmark = pytest.mark.django_db


def _instructor():
    user = UserFactory(username="dc-teacher@example.com")
    InstructorFactory(user=user, instructor_slug="dc-teacher")
    return user


def describe_teach_discount_codes():
    def it_shows_a_sitewide_admin_code_read_only(client):
        admin = UserFactory(username="dc-admin@example.com")
        code = DiscountCodeFactory(
            code="PLMEMBERS", created_by=admin, class_offering=None, description="Member discount"
        )
        client.force_login(_instructor())
        resp = client.get(reverse("classes:teach_discount_codes"))
        assert resp.status_code == 200
        assert b"PLMEMBERS" in resp.content
        assert b"Site-wide" in resp.content
        # No edit affordance for a code the instructor doesn't own.
        assert reverse("classes:teach_discount_code_edit", kwargs={"pk": code.pk}).encode() not in resp.content

    def it_shows_my_own_code_with_edit_controls(client):
        user = _instructor()
        mine = DiscountCodeFactory(code="MINE10", created_by=user, class_offering=None)
        client.force_login(user)
        resp = client.get(reverse("classes:teach_discount_codes"))
        assert b"MINE10" in resp.content
        assert reverse("classes:teach_discount_code_edit", kwargs={"pk": mine.pk}).encode() in resp.content

    def it_blocks_editing_a_code_i_did_not_create(client):
        admin = UserFactory(username="dc-admin2@example.com")
        code = DiscountCodeFactory(code="ADMINONLY", created_by=admin, class_offering=None)
        client.force_login(_instructor())
        resp = client.get(reverse("classes:teach_discount_code_edit", kwargs={"pk": code.pk}))
        assert resp.status_code == 404

    def it_blocks_deleting_a_code_i_did_not_create(client):
        admin = UserFactory(username="dc-admin3@example.com")
        code = DiscountCodeFactory(code="NODELETE", created_by=admin, class_offering=None)
        client.force_login(_instructor())
        resp = client.post(reverse("classes:teach_discount_code_delete", kwargs={"pk": code.pk}))
        assert resp.status_code == 404
        assert DiscountCode.objects.filter(pk=code.pk).exists()

    def it_lets_me_delete_my_own_code(client):
        user = _instructor()
        mine = DiscountCodeFactory(code="MINEDEL", created_by=user)
        client.force_login(user)
        resp = client.post(reverse("classes:teach_discount_code_delete", kwargs={"pk": mine.pk}))
        assert resp.status_code == 302
        assert not DiscountCode.objects.filter(pk=mine.pk).exists()
