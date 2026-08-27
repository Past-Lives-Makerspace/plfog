"""BDD specs pinning the removal of the per-class participant CSV exports (admin + teach).

The consolidated, filtered registrations export (``classes:admin_registrations_export``)
is the one remaining download; the per-class buttons and views are gone.
"""

from __future__ import annotations

import pytest
from django.urls import NoReverseMatch, reverse

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher-export@example.com")
    return InstructorFactory(user=user, full_legal_name="Teach Export", instructor_slug="teach-export")


def describe_admin_class_export_removed():
    def it_has_no_admin_export_url(db):
        with pytest.raises(NoReverseMatch):
            reverse("classes:admin_class_export", kwargs={"pk": 1})

    def it_404s_the_old_admin_export_path(admin_user, client):
        offering = ClassOfferingFactory()
        client.force_login(admin_user)
        resp = client.get(f"/classes/admin/{offering.pk}/registrations/export/")
        assert resp.status_code == 404

    def it_shows_no_export_button_on_admin_registrations(admin_user, client):
        offering = ClassOfferingFactory()
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        assert b"Export Data" not in resp.content


def describe_teach_class_export_removed():
    """Instructors see their roster on screen but can no longer download it — the
    student data stays inside the Member Portal (per the launch copy review)."""

    def it_has_no_teach_export_url(db):
        with pytest.raises(NoReverseMatch):
            reverse("classes:teach_class_export", kwargs={"pk": 1})

    def it_404s_the_old_export_path(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-export")
        client.force_login(instructor_fixture.user)
        resp = client.get(f"/classes/teach/classes/{mine.pk}/registrations/export/")
        assert resp.status_code == 404

    def it_shows_no_export_button_on_teach_registrations(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-btn")
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_class_registrations", kwargs={"pk": mine.pk}))
        assert resp.status_code == 200
        assert b"Export Data" not in resp.content
