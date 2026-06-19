"""BDD specs for the "Offer on another set of dates" action (admin + teach)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import (
    ClassOfferingFactory,
    InstructorFactory,
    SeriesClassOfferingFactory,
    UserFactory,
)
from classes.models import ClassOffering


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="runner@example.com")
    return InstructorFactory(user=user, full_legal_name="Runner R", instructor_slug="runner-r")


def describe_admin_class_duplicate_run():
    def it_creates_a_grouped_draft_run_and_redirects_to_its_editor(admin_user, client, db):
        client.force_login(admin_user)
        original = SeriesClassOfferingFactory(
            title="Blacksmithing 101", slug="bs-orig", status=ClassOffering.Status.PUBLISHED, session_count=3
        )
        resp = client.post(reverse("classes:admin_class_duplicate_run", kwargs={"pk": original.pk}))
        run = ClassOffering.objects.exclude(pk=original.pk).get(title="Blacksmithing 101")
        assert resp.status_code == 302
        assert resp.url == reverse("classes:admin_class_edit", kwargs={"pk": run.pk})
        assert run.status == ClassOffering.Status.DRAFT
        assert run.grouping_key == original.grouping_key
        assert run.sessions.count() == 0

    def it_does_nothing_on_get_and_redirects_to_detail(admin_user, client, db):
        client.force_login(admin_user)
        original = ClassOfferingFactory(title="Solo", slug="solo")
        resp = client.get(reverse("classes:admin_class_duplicate_run", kwargs={"pk": original.pk}))
        assert resp.status_code == 302
        assert resp.url == reverse("classes:admin_class_detail", kwargs={"pk": original.pk})
        assert ClassOffering.objects.filter(title="Solo").count() == 1


def describe_teach_class_duplicate_run():
    def it_lets_an_instructor_spin_off_another_run_of_their_own_class(instructor_fixture, client, db):
        client.force_login(instructor_fixture.user)
        original = SeriesClassOfferingFactory(
            instructor=instructor_fixture,
            title="Forge Night",
            slug="forge-mine",
            status=ClassOffering.Status.DRAFT,
            session_count=2,
        )
        resp = client.post(reverse("classes:teach_class_duplicate_run", kwargs={"pk": original.pk}))
        run = ClassOffering.objects.exclude(pk=original.pk).get(title="Forge Night")
        assert resp.status_code == 302
        assert resp.url == reverse("classes:teach_class_edit", kwargs={"pk": run.pk})
        assert run.status == ClassOffering.Status.DRAFT
        assert run.instructor_id == instructor_fixture.pk

    def it_404s_for_another_instructors_class(instructor_fixture, client, db):
        other = InstructorFactory(user=UserFactory(username="someone@example.com"), instructor_slug="someone")
        theirs = ClassOfferingFactory(instructor=other, slug="not-mine")
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_duplicate_run", kwargs={"pk": theirs.pk}))
        assert resp.status_code == 404
