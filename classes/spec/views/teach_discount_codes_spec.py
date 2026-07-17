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


def _self_approver():
    """An active teaching member who may approve their own discount codes."""
    user = UserFactory(username="dc-approver@example.com")
    InstructorFactory(user=user, instructor_slug="dc-approver", can_self_approve_discounts=True)
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


def describe_teach_discount_code_approve():
    def it_lets_a_self_approver_approve_their_own_pending_code(client):
        user = _self_approver()
        mine = DiscountCodeFactory(code="APPROVEME", created_by=user, is_approved=False)
        client.force_login(user)
        resp = client.post(reverse("classes:teach_discount_code_approve", kwargs={"pk": mine.pk}))
        assert resp.status_code == 302
        assert resp.url == reverse("classes:teach_discount_codes")
        mine.refresh_from_db()
        assert mine.is_approved is True

    def it_forbids_a_member_without_the_permission(client):
        user = _instructor()  # active member, but can_self_approve_discounts defaults False
        mine = DiscountCodeFactory(code="NOPERM", created_by=user, is_approved=False)
        client.force_login(user)
        resp = client.post(reverse("classes:teach_discount_code_approve", kwargs={"pk": mine.pk}))
        assert resp.status_code == 403
        mine.refresh_from_db()
        assert mine.is_approved is False

    def it_forbids_a_self_approver_on_someone_elses_code(client):
        other = UserFactory(username="dc-owner@example.com")
        code = DiscountCodeFactory(code="NOTMINE", created_by=other, is_approved=False)
        client.force_login(_self_approver())
        resp = client.post(reverse("classes:teach_discount_code_approve", kwargs={"pk": code.pk}))
        assert resp.status_code == 403
        code.refresh_from_db()
        assert code.is_approved is False

    def it_redirects_anonymous_users_to_login(client):
        code = DiscountCodeFactory(code="ANON", is_approved=False)
        resp = client.post(reverse("classes:teach_discount_code_approve", kwargs={"pk": code.pk}))
        assert resp.status_code == 302
        assert "login" in resp.url.lower()
        code.refresh_from_db()
        assert code.is_approved is False


def describe_approve_button_visibility():
    def it_shows_the_approve_button_on_an_approvable_pending_code(client):
        user = _self_approver()
        mine = DiscountCodeFactory(code="SHOWBTN", created_by=user, is_approved=False)
        client.force_login(user)
        resp = client.get(reverse("classes:teach_discount_codes"))
        approve_url = reverse("classes:teach_discount_code_approve", kwargs={"pk": mine.pk})
        assert approve_url.encode() in resp.content

    def it_hides_the_button_but_shows_pending_when_not_approvable(client):
        user = _instructor()  # owns the code but lacks the self-approve permission
        mine = DiscountCodeFactory(code="HIDEBTN", created_by=user, is_approved=False)
        client.force_login(user)
        resp = client.get(reverse("classes:teach_discount_codes"))
        approve_url = reverse("classes:teach_discount_code_approve", kwargs={"pk": mine.pk})
        assert approve_url.encode() not in resp.content
        assert b"Pending approval" in resp.content

    def it_shows_neither_on_an_approved_code(client):
        user = _self_approver()
        mine = DiscountCodeFactory(code="ALLGOOD", created_by=user, is_approved=True)
        client.force_login(user)
        resp = client.get(reverse("classes:teach_discount_codes"))
        approve_url = reverse("classes:teach_discount_code_approve", kwargs={"pk": mine.pk})
        assert approve_url.encode() not in resp.content
        assert b"Pending approval" not in resp.content
