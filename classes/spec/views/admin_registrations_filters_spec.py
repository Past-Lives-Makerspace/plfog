"""BDD specs for the consolidated Registrations list: role scope, filters, CSV export."""

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


def describe_admin_registrations_scope():
    def it_lets_an_instructor_see_only_their_classes_registrations(client):
        user, offering = _instructor_with_class()
        RegistrationFactory(class_offering=offering, email="mine@example.com")
        RegistrationFactory(email="theirs@example.com")  # a different class
        client.force_login(user)
        response = client.get(reverse("classes:admin_registrations"))
        assert response.status_code == 200
        assert b"mine@example.com" in response.content
        assert b"theirs@example.com" not in response.content

    def it_forbids_a_plain_member_with_no_classes(member_user, client):
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_registrations"))
        assert response.status_code == 403

    def it_shows_the_registrations_tab_to_an_instructor(client):
        user, _ = _instructor_with_class(username="teach2@example.com", slug="t2")
        client.force_login(user)
        response = client.get(reverse("classes:admin_registrations"))
        assert b"Registrations" in response.content


def describe_admin_registrations_filters():
    def it_filters_by_status(admin_user, client):
        offering = ClassOfferingFactory(slug="f-status")
        RegistrationFactory(
            class_offering=offering, email="confirmed@example.com", status=Registration.Status.CONFIRMED
        )
        RegistrationFactory(class_offering=offering, email="pending@example.com", status=Registration.Status.PENDING)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations"), {"status": Registration.Status.CONFIRMED})
        assert b"confirmed@example.com" in response.content
        assert b"pending@example.com" not in response.content

    def it_ignores_an_unknown_status(admin_user, client):
        offering = ClassOfferingFactory(slug="f-badstatus")
        RegistrationFactory(class_offering=offering, email="conf@example.com", status=Registration.Status.CONFIRMED)
        RegistrationFactory(class_offering=offering, email="pend@example.com", status=Registration.Status.PENDING)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations"), {"status": "bogus"})
        assert b"conf@example.com" in response.content
        assert b"pend@example.com" in response.content

    def it_filters_by_class(admin_user, client):
        a = ClassOfferingFactory(slug="f-class-a")
        b = ClassOfferingFactory(slug="f-class-b")
        RegistrationFactory(class_offering=a, email="in-a@example.com")
        RegistrationFactory(class_offering=b, email="in-b@example.com")
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations"), {"class": a.pk})
        assert b"in-a@example.com" in response.content
        assert b"in-b@example.com" not in response.content

    def it_ignores_a_non_numeric_class(admin_user, client):
        offering = ClassOfferingFactory(slug="f-badclass")
        RegistrationFactory(class_offering=offering, email="shown@example.com")
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations"), {"class": "abc"})
        assert b"shown@example.com" in response.content


def describe_admin_registrations_export():
    def it_streams_a_csv_for_admins(admin_user, client):
        RegistrationFactory(email="export-me@example.com")
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations_export"))
        assert response["Content-Type"] == "text/csv"
        body = b"".join(response.streaming_content).decode()
        assert "export-me@example.com" in body

    def it_scopes_the_export_to_an_instructors_classes(client):
        user, offering = _instructor_with_class(username="teach3@example.com", slug="t3")
        RegistrationFactory(class_offering=offering, email="mine-exp@example.com")
        RegistrationFactory(email="theirs-exp@example.com")
        client.force_login(user)
        response = client.get(reverse("classes:admin_registrations_export"))
        body = b"".join(response.streaming_content).decode()
        assert "mine-exp@example.com" in body
        assert "theirs-exp@example.com" not in body

    def it_honors_the_status_filter_in_the_export(admin_user, client):
        offering = ClassOfferingFactory(slug="exp-status")
        RegistrationFactory(class_offering=offering, email="c-exp@example.com", status=Registration.Status.CONFIRMED)
        RegistrationFactory(class_offering=offering, email="p-exp@example.com", status=Registration.Status.PENDING)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations_export"), {"status": Registration.Status.CONFIRMED})
        body = b"".join(response.streaming_content).decode()
        assert "c-exp@example.com" in body
        assert "p-exp@example.com" not in body
