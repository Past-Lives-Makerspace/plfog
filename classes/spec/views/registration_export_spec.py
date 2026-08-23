"""BDD specs for the per-class participant CSV export views (admin + teach)."""

from __future__ import annotations

import pytest
from django.urls import NoReverseMatch, reverse

from classes.factories import ClassOfferingFactory, InstructorFactory, RegistrationFactory, UserFactory


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher-export@example.com")
    return InstructorFactory(user=user, full_legal_name="Teach Export", instructor_slug="teach-export")


@pytest.fixture
def other_instructor(db):
    user = UserFactory(username="other-export@example.com")
    return InstructorFactory(user=user, full_legal_name="Other Export", instructor_slug="other-export")


def _csv_body(response) -> str:
    return b"".join(response.streaming_content).decode()


def describe_admin_class_export():
    def it_requires_admin_access(member_user, client):
        offering = ClassOfferingFactory()
        client.force_login(member_user)
        resp = client.get(reverse("classes:admin_class_export", kwargs={"pk": offering.pk}))
        assert resp.status_code == 403

    def it_streams_a_csv_for_any_class(admin_user, client):
        offering = ClassOfferingFactory(slug="admin-export")
        RegistrationFactory(class_offering=offering, first_name="Grace", email="grace@example.com")
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_export", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "text/csv"
        assert "attachment" in resp["Content-Disposition"]
        body = _csv_body(resp)
        assert "First Name" in body
        assert "grace@example.com" in body


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


def describe_export_button_on_registrations_tab():
    def it_shows_export_link_on_admin_registrations(admin_user, client):
        offering = ClassOfferingFactory()
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        assert reverse("classes:admin_class_export", kwargs={"pk": offering.pk}).encode() in resp.content
        assert b"Export Data" in resp.content

    def it_shows_no_export_button_on_teach_registrations(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-btn")
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_class_registrations", kwargs={"pk": mine.pk}))
        assert resp.status_code == 200
        assert b"Export Data" not in resp.content
