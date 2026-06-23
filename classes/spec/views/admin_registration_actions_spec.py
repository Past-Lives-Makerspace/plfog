"""BDD specs for admin registration actions: detail scoping, move, and refund."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import ClassOfferingFactory, InstructorFactory, RegistrationFactory, UserFactory
from classes.models import Registration

pytestmark = pytest.mark.django_db


def _instructor_with_class(username="teacher@example.com", slug="t-class"):
    user = UserFactory(username=username)
    member = InstructorFactory(user=user)
    offering = ClassOfferingFactory(slug=slug, instructor=member)
    return user, offering


def describe_registration_detail_scope():
    def it_lets_an_instructor_view_their_classes_registration(client):
        user, offering = _instructor_with_class()
        reg = RegistrationFactory(class_offering=offering, first_name="Mine")
        client.force_login(user)
        response = client.get(reverse("classes:admin_registration_detail", kwargs={"pk": reg.pk}))
        assert response.status_code == 200
        assert b"Mine" in response.content

    def it_hides_admin_actions_from_an_instructor(client):
        user, offering = _instructor_with_class(username="t-noact@example.com", slug="t-noact")
        reg = RegistrationFactory(class_offering=offering)
        client.force_login(user)
        response = client.get(reverse("classes:admin_registration_detail", kwargs={"pk": reg.pk}))
        assert b"Mark Refunded" not in response.content

    def it_404s_for_a_registration_outside_an_instructors_classes(client):
        user, _ = _instructor_with_class(username="t-404@example.com", slug="t-404")
        other = RegistrationFactory()  # a different class
        client.force_login(user)
        response = client.get(reverse("classes:admin_registration_detail", kwargs={"pk": other.pk}))
        assert response.status_code == 404

    def it_shows_admin_actions_to_an_admin(admin_user, client):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registration_detail", kwargs={"pk": reg.pk}))
        assert b"Mark Refunded" in response.content


def describe_admin_registration_move():
    def it_moves_a_registration_to_another_class(admin_user, client):
        src = ClassOfferingFactory(slug="mvv-src")
        dst = ClassOfferingFactory(slug="mvv-dst")
        reg = RegistrationFactory(class_offering=src, status=Registration.Status.CONFIRMED)
        client.force_login(admin_user)
        response = client.post(reverse("classes:admin_registration_move", kwargs={"pk": reg.pk}), {"target": dst.pk})
        assert response.status_code == 302
        reg.refresh_from_db()
        assert reg.class_offering_id == dst.pk

    def it_rejects_an_invalid_target_and_leaves_the_class_unchanged(admin_user, client):
        src = ClassOfferingFactory(slug="mvv-bad-src")
        reg = RegistrationFactory(class_offering=src, status=Registration.Status.CONFIRMED)
        client.force_login(admin_user)
        response = client.post(reverse("classes:admin_registration_move", kwargs={"pk": reg.pk}), {"target": ""})
        assert response.status_code == 302
        reg.refresh_from_db()
        assert reg.class_offering_id == src.pk

    def it_forbids_a_non_admin_from_moving(client):
        user, offering = _instructor_with_class(username="t-mvforbid@example.com", slug="t-mvf")
        reg = RegistrationFactory(class_offering=offering)
        dst = ClassOfferingFactory(slug="mvf-dst")
        client.force_login(user)
        response = client.post(reverse("classes:admin_registration_move", kwargs={"pk": reg.pk}), {"target": dst.pk})
        assert response.status_code == 403


def describe_admin_registration_refund():
    def it_marks_a_registration_refunded(admin_user, client):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED)
        client.force_login(admin_user)
        response = client.post(reverse("classes:admin_registration_refund", kwargs={"pk": reg.pk}), {"reason": "dup"})
        assert response.status_code == 302
        reg.refresh_from_db()
        assert reg.status == Registration.Status.REFUNDED

    def it_rejects_a_get(admin_user, client):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registration_refund", kwargs={"pk": reg.pk}))
        assert response.status_code == 405
        reg.refresh_from_db()
        assert reg.status == Registration.Status.CONFIRMED

    def it_forbids_a_non_admin_from_refunding(client):
        user, offering = _instructor_with_class(username="t-rfforbid@example.com", slug="t-rff")
        reg = RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        client.force_login(user)
        response = client.post(reverse("classes:admin_registration_refund", kwargs={"pk": reg.pk}))
        assert response.status_code == 403
        reg.refresh_from_db()
        assert reg.status == Registration.Status.CONFIRMED
