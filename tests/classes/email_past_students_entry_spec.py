"""BDD specs for how an instructor finds My past students (#1042): the My Classes button, its
What's New entry, and the composer letting in an instructor whose classes are all unpublished."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from core.guidance import GUIDANCE
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

_COMPOSE_STUDENTS = f"{reverse('hub_compose')}?audience=students"


def _instructor(*, slug: str = "teacher-t"):
    MembershipPlanFactory()
    user = UserFactory(username="teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher T", instructor_slug=slug)


def describe_my_classes_button():
    def it_links_an_instructor_with_a_class_to_the_composer_with_past_students_picked(client):
        instructor = _instructor()
        ClassOfferingFactory(instructor=instructor, image="", gallery=0, status=ClassOffering.Status.DRAFT)
        client.force_login(instructor.user)

        html = client.get(reverse("classes:teach_dashboard")).content.decode()

        assert "data-email-past-students" in html
        assert f'href="{_COMPOSE_STUDENTS}"' in html
        assert "Email past students" in html

    def it_hides_the_button_from_an_instructor_with_no_classes(client):
        instructor = _instructor()
        client.force_login(instructor.user)

        html = client.get(reverse("classes:teach_dashboard")).content.decode()

        assert "data-email-past-students" not in html


def describe_whats_new_entry():
    def it_opens_the_composer_with_past_students_picked():
        assert GUIDANCE["classes.email-past-students"].url({}) == _COMPOSE_STUDENTS

    def it_names_past_students_in_the_email_members_steps():
        assert any("My past students" in step for step in GUIDANCE["admin.email-members"].steps)


def describe_compose_entry_for_past_students():
    def it_lets_in_an_instructor_whose_only_class_is_a_draft_and_who_has_no_instructor_page(client):
        instructor = _instructor(slug="")
        ClassOfferingFactory(instructor=instructor, image="", gallery=0, status=ClassOffering.Status.DRAFT)
        client.force_login(instructor.user)

        response = client.get(_COMPOSE_STUDENTS)

        assert response.status_code == 200
        assert '<option value="students"' in response.content.decode()

    def it_still_refuses_that_instructor_any_other_audience(client):
        instructor = _instructor(slug="")
        ClassOfferingFactory(instructor=instructor, image="", gallery=0, status=ClassOffering.Status.DRAFT)
        client.force_login(instructor.user)

        response = client.get(f"{reverse('hub_compose')}?audience=site")

        assert response.status_code == 302

    def it_refuses_a_member_who_teaches_nothing(client):
        MembershipPlanFactory()
        user = UserFactory(username="nobody@example.com")
        client.force_login(user)

        response = client.get(_COMPOSE_STUDENTS)

        assert response.status_code == 302
