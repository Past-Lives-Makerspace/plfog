"""BDD specs for the instructor dashboard (Plan 3)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher@example.com")
    instructor = InstructorFactory(user=user, display_name="Teacher T", slug="teacher-t")
    return instructor


@pytest.fixture
def other_instructor(db):
    user = UserFactory(username="other@example.com")
    return InstructorFactory(user=user, display_name="Other", slug="other")


def describe_instructor_access_gate():
    def it_blocks_non_instructor(member_user, client):
        client.force_login(member_user)
        response = client.get(reverse("classes:instructor_dashboard"))
        assert response.status_code == 403

    def it_blocks_anonymous(db, client):
        response = client.get(reverse("classes:instructor_dashboard"))
        assert response.status_code == 302  # login redirect

    def it_blocks_inactive_instructor(db, client):
        user = UserFactory(username="inactive@example.com")
        InstructorFactory(user=user, is_active=False)
        client.force_login(user)
        response = client.get(reverse("classes:instructor_dashboard"))
        assert response.status_code == 403


def describe_instructor_dashboard():
    def it_shows_only_my_classes(instructor_fixture, other_instructor, client):
        mine = ClassOfferingFactory(
            instructor=instructor_fixture,
            title="Mine Class",
            slug="mine-class",
            status=ClassOffering.Status.DRAFT,
        )
        ClassOfferingFactory(
            instructor=other_instructor,
            title="Theirs Class",
            slug="theirs-class",
            status=ClassOffering.Status.PUBLISHED,
        )
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:instructor_dashboard"))
        assert response.status_code == 200
        assert mine.title.encode() in response.content
        assert b"Theirs Class" not in response.content


def describe_instructor_create_class():
    def it_creates_a_draft_with_sessions(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        start = (timezone.now() + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")
        end = (timezone.now() + timedelta(days=7, hours=2)).strftime("%Y-%m-%dT%H:%M")
        response = client.post(
            reverse("classes:instructor_class_create"),
            {
                "title": "My New Class",
                "category": cat.pk,
                "description": "d",
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "price_cents": 5000,
                "member_discount_pct": 10,
                "capacity": 6,
                "scheduling_model": "fixed",
                "flexible_note": "",
                "recurring_pattern": "",
                "sessions-TOTAL_FORMS": "1",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "sessions-0-starts_at": start,
                "sessions-0-ends_at": end,
                "images-TOTAL_FORMS": "0",
                "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
                "action": "save",
            },
        )
        assert response.status_code == 302
        offering = ClassOffering.objects.get(title="My New Class")
        assert offering.instructor == instructor_fixture
        assert offering.status == ClassOffering.Status.DRAFT
        assert offering.sessions.count() == 1

    def it_submits_for_review_when_action_is_submit(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        response = client.post(
            reverse("classes:instructor_class_create"),
            {
                "title": "Submit-Me",
                "category": cat.pk,
                "description": "d",
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "price_cents": 5000,
                "member_discount_pct": 10,
                "capacity": 6,
                "scheduling_model": "flexible",
                "flexible_note": "",
                "recurring_pattern": "",
                "sessions-TOTAL_FORMS": "0",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "images-TOTAL_FORMS": "0",
                "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
                "action": "submit",
            },
        )
        assert response.status_code == 302
        offering = ClassOffering.objects.get(title="Submit-Me")
        assert offering.status == ClassOffering.Status.PENDING


def describe_instructor_edit_class():
    def it_refuses_editing_other_instructors_classes(instructor_fixture, other_instructor, client):
        theirs = ClassOfferingFactory(instructor=other_instructor, slug="theirs-edit")
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:instructor_class_edit", kwargs={"pk": theirs.pk}))
        assert response.status_code == 404

    def it_redirects_for_published_classes(instructor_fixture, client):
        mine = ClassOfferingFactory(
            instructor=instructor_fixture,
            slug="mine-published",
            status=ClassOffering.Status.PUBLISHED,
        )
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:instructor_class_edit", kwargs={"pk": mine.pk}))
        assert response.status_code == 302

    def it_renders_the_edit_form_for_drafts(instructor_fixture, client):
        mine = ClassOfferingFactory(
            instructor=instructor_fixture,
            slug="mine-draft",
            status=ClassOffering.Status.DRAFT,
        )
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:instructor_class_edit", kwargs={"pk": mine.pk}))
        assert response.status_code == 200


def describe_instructor_submit():
    def it_flips_draft_to_pending(instructor_fixture, client):
        mine = ClassOfferingFactory(
            instructor=instructor_fixture,
            slug="to-submit",
            status=ClassOffering.Status.DRAFT,
        )
        client.force_login(instructor_fixture.user)
        response = client.post(reverse("classes:instructor_class_submit", kwargs={"pk": mine.pk}))
        assert response.status_code == 302
        mine.refresh_from_db()
        assert mine.status == ClassOffering.Status.PENDING


def describe_instructor_registrations():
    def it_shows_only_my_registrations(instructor_fixture, other_instructor, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="m")
        theirs = ClassOfferingFactory(instructor=other_instructor, slug="t")
        r1 = RegistrationFactory(class_offering=mine, first_name="Mine", last_name="Guest")
        RegistrationFactory(class_offering=theirs, first_name="Other", last_name="Guest")
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:instructor_registrations"))
        assert response.status_code == 200
        assert r1.first_name.encode() in response.content
        assert b"Other" not in response.content


def describe_instructor_profile():
    def it_saves_profile_fields(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        response = client.post(
            reverse("classes:instructor_profile"),
            {
                "display_name": "New Name",
                "bio": "Updated bio",
                "website": "",
                "social_handle": "@whatever",
            },
        )
        assert response.status_code == 302
        instructor_fixture.refresh_from_db()
        assert instructor_fixture.display_name == "New Name"
        assert instructor_fixture.bio == "Updated bio"
