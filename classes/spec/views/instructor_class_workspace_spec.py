"""BDD specs for the instructor per-class Workspace tabs."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import (
    ClassOfferingFactory,
    DiscountCodeFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, Registration


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher T", instructor_slug="teacher-t")


@pytest.fixture
def other_instructor(db):
    user = UserFactory(username="other@example.com")
    return InstructorFactory(user=user, full_legal_name="Other", instructor_slug="other")


def describe_instructor_class_workspace_scope():
    def it_404s_for_another_instructors_class(instructor_fixture, other_instructor, client):
        theirs = ClassOfferingFactory(instructor=other_instructor, slug="theirs-ws")
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_class_detail", kwargs={"pk": theirs.pk}))
        assert resp.status_code == 404

    def it_blocks_non_members(db, client):
        offering = ClassOfferingFactory()
        resp = client.get(reverse("classes:instructor_class_detail", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302  # login redirect


def describe_instructor_overview_tab():
    def it_shows_summary_and_subnav(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-ws", status=ClassOffering.Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_class_detail", kwargs={"pk": mine.pk}))
        assert resp.status_code == 200
        assert reverse("classes:instructor_class_registrations", kwargs={"pk": mine.pk}).encode() in resp.content
        # Draft → Edit action available
        assert reverse("classes:instructor_class_edit", kwargs={"pk": mine.pk}).encode() in resp.content

    def it_hides_edit_for_published_classes(instructor_fixture, client):
        mine = ClassOfferingFactory(
            instructor=instructor_fixture, slug="mine-pub", status=ClassOffering.Status.PUBLISHED
        )
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_class_detail", kwargs={"pk": mine.pk}))
        assert reverse("classes:instructor_class_edit", kwargs={"pk": mine.pk}).encode() not in resp.content


def describe_instructor_registrations_tab():
    def it_shows_my_classs_registrant(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-regs")
        RegistrationFactory(class_offering=mine, first_name="Jess", last_name="Park")
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_class_registrations", kwargs={"pk": mine.pk}))
        assert resp.status_code == 200
        assert b"Jess" in resp.content


def describe_instructor_waitlist_tab():
    def it_lists_waitlisted(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-wait")
        RegistrationFactory(
            class_offering=mine, first_name="Wait", last_name="Lister", status=Registration.Status.WAITLISTED
        )
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_class_waitlist", kwargs={"pk": mine.pk}))
        assert resp.status_code == 200
        assert b"Wait" in resp.content


def describe_instructor_discount_codes_tab():
    def it_shows_a_class_scoped_code(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-codes")
        DiscountCodeFactory(code="MINE10", class_offering=mine)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_class_discount_codes", kwargs={"pk": mine.pk}))
        assert resp.status_code == 200
        assert b"MINE10" in resp.content
