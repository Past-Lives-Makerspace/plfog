"""Classes admin is admin-only. Instructors manage their own world via the Teaching portal."""

from __future__ import annotations

from django.urls import reverse

from classes.factories import InstructorFactory, UserFactory


def describe_classes_admin_access():
    def it_lets_admins_into_every_tab(admin_user, client):
        client.force_login(admin_user)
        for name in (
            "classes:admin_classes",
            "classes:admin_categories",
            "classes:admin_registrations",
            "classes:admin_discount_codes",
            "classes:admin_settings",
        ):
            assert client.get(reverse(name)).status_code == 200, f"admin blocked from {name}"

    def it_forbids_instructors_from_admin(db, client):
        user = UserFactory(username="inst@example.com")
        InstructorFactory(user=user, instructor_slug="inst")
        client.force_login(user)
        response = client.get(reverse("classes:admin_classes"))
        assert response.status_code == 403

    def it_forbids_plain_members(member_user, client):
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_classes"))
        assert response.status_code == 403

    def it_redirects_anonymous_to_login(db, client):
        response = client.get(reverse("classes:admin_classes"))
        assert response.status_code == 302


def describe_instructor_discount_codes():
    def it_lets_instructors_manage_discount_codes(db, client):
        from classes.factories import DiscountCodeFactory

        user = UserFactory(username="dc-inst@example.com")
        InstructorFactory(user=user, instructor_slug="dc-inst")
        client.force_login(user)
        response = client.get(reverse("classes:instructor_discount_codes"))
        assert response.status_code == 200
        code = DiscountCodeFactory(code="INSTR10")
        response = client.get(reverse("classes:instructor_discount_codes"))
        assert code.code.encode() in response.content

    def it_lets_plain_members_access_teaching_portal(member_user, client):
        client.force_login(member_user)
        response = client.get(reverse("classes:instructor_discount_codes"))
        assert response.status_code == 200
