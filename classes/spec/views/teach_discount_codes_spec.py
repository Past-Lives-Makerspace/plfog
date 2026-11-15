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
        assert b"<td data-code-scope>Every class</td>" in resp.content
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


def describe_approve_button_visibility():
    """#1184: an instructor's code is live at once, so their page shows no Approve and no pending state."""

    def it_shows_no_approve_button(client):
        user = _instructor()
        mine = DiscountCodeFactory(code="SHOWBTN", created_by=user, is_approved=False)
        client.force_login(user)
        resp = client.get(reverse("classes:teach_discount_codes"))
        assert f"/classes/teach/discount-codes/{mine.pk}/approve/".encode() not in resp.content

    def it_reads_an_old_unapproved_code_as_inactive_not_pending(client):
        user = _instructor()
        DiscountCodeFactory(code="HIDEBTN", created_by=user, is_approved=False)
        client.force_login(user)
        html = client.get(reverse("classes:teach_discount_codes")).content.decode()
        assert "Pending approval" not in html
        assert '<span style="color:var(--hub-text-muted);">Inactive</span>' in html

    def it_shows_an_approved_active_code_as_active(client):
        user = _instructor()
        DiscountCodeFactory(code="ALLGOOD", created_by=user, is_approved=True)
        client.force_login(user)
        html = client.get(reverse("classes:teach_discount_codes")).content.decode()
        assert '<span style="color:var(--hub-success, #4caf50);">Active</span>' in html
        assert "Pending approval" not in html


def describe_an_instructors_code_on_my_discount_codes():
    """AC6 of #764: a code an admin aimed at this instructor lists with their own, labelled, read only."""

    def _jacqueline():
        user = UserFactory(username="dc-jacqueline@example.com")
        teacher = InstructorFactory(user=user, instructor_slug="dc-jacqueline", full_legal_name="Jacqueline Sowell")
        return user, teacher

    def _seed(teacher):
        admin = UserFactory(username="dc-admin-764@example.com")
        mine = DiscountCodeFactory(code="JACQUELINE30", class_offering=None, instructor=teacher, created_by=admin)
        DiscountCodeFactory(code="OTHERS30", class_offering=None, instructor=InstructorFactory(), created_by=admin)
        return mine

    def it_lists_the_code_with_its_scope_and_no_controls_in_the_direct_flow(client):
        user, teacher = _jacqueline()
        mine = _seed(teacher)
        client.force_login(user)
        html = client.get(reverse("classes:teach_discount_codes")).content.decode()
        assert "JACQUELINE30" in html
        assert '<span class="hub-pill hub-pill--neutral" data-scope-instructor>Jacqueline Sowell</span>' in html
        assert "OTHERS30" not in html
        assert reverse("classes:teach_discount_code_edit", kwargs={"pk": mine.pk}) not in html
        assert reverse("classes:teach_discount_code_delete", kwargs={"pk": mine.pk}) not in html
        # Not a site wide code either: with no everywhere code seeded, that table never renders.
        assert "Site-Wide Codes" not in html
