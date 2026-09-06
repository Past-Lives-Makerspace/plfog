"""BDD specs for the teaching per-class Workspace tabs."""

from __future__ import annotations

import pytest
from django.core import mail
from django.urls import reverse

from classes.factories import (
    ClassOfferingFactory,
    DiscountCodeFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, Registration


@pytest.fixture(autouse=True)
def _email_outbox(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox = []
    yield
    mail.outbox = []


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher T", instructor_slug="teacher-t")


@pytest.fixture
def other_instructor(db):
    user = UserFactory(username="other@example.com")
    return InstructorFactory(user=user, full_legal_name="Other", instructor_slug="other")


def describe_teach_class_workspace_scope():
    def it_404s_for_another_instructors_class(instructor_fixture, other_instructor, client):
        theirs = ClassOfferingFactory(instructor=other_instructor, slug="theirs-ws")
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_class_detail", kwargs={"pk": theirs.pk}))
        assert resp.status_code == 404

    def it_blocks_non_members(db, client):
        offering = ClassOfferingFactory()
        resp = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302  # login redirect

    def it_404s_every_tab_for_a_foreign_class(instructor_fixture, other_instructor, client):
        theirs = ClassOfferingFactory(instructor=other_instructor, slug="theirs-tabs")
        client.force_login(instructor_fixture.user)
        for name in [
            "classes:teach_class_registrations",
            "classes:teach_class_waitlist",
            "classes:teach_class_discount_codes",
        ]:
            resp = client.get(reverse(name, kwargs={"pk": theirs.pk}))
            assert resp.status_code == 404, name

    def it_404s_emailing_a_foreign_class(instructor_fixture, other_instructor, client):
        theirs = ClassOfferingFactory(instructor=other_instructor, slug="theirs-mail")
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_email", kwargs={"pk": theirs.pk}), data={})
        assert resp.status_code == 404


def describe_teach_class_email():
    def it_emails_my_classs_registrant_and_returns_to_the_tab(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mail-mine")
        reg = RegistrationFactory(class_offering=mine, email="s@example.com", status=Registration.Status.CONFIRMED)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_email", kwargs={"pk": mine.pk}),
            {"subject": "Hi", "body": "Hello", "registration_ids": [reg.pk]},
        )
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:teach_class_registrations", kwargs={"pk": mine.pk})
        assert len(mail.outbox) == 1

    def it_refuses_to_email_a_different_class_of_mine(instructor_fixture, client):
        class_a = ClassOfferingFactory(instructor=instructor_fixture, slug="mail-a")
        class_b = ClassOfferingFactory(instructor=instructor_fixture, slug="mail-b")
        reg_b = RegistrationFactory(class_offering=class_b, email="b@example.com", status=Registration.Status.CONFIRMED)
        client.force_login(instructor_fixture.user)
        # Posting class B's registrant to class A's email endpoint must be rejected.
        resp = client.post(
            reverse("classes:teach_class_email", kwargs={"pk": class_a.pk}),
            {"subject": "Hi", "body": "Hello", "registration_ids": [reg_b.pk]},
        )
        assert resp.status_code == 302
        assert len(mail.outbox) == 0

    def it_redirects_to_the_tab_on_invalid_form(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mail-invalid")
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_email", kwargs={"pk": mine.pk}),
            {"subject": "", "body": ""},
        )
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:teach_class_registrations", kwargs={"pk": mine.pk})
        assert len(mail.outbox) == 0


def describe_teach_overview_tab():
    def it_shows_summary_and_subnav(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-ws", status=ClassOffering.Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_class_detail", kwargs={"pk": mine.pk}))
        assert resp.status_code == 200
        assert reverse("classes:teach_class_registrations", kwargs={"pk": mine.pk}).encode() in resp.content
        # Draft → Edit action available
        assert reverse("classes:teach_class_edit", kwargs={"pk": mine.pk}).encode() in resp.content

    def it_offers_light_edit_details_for_an_upcoming_published_class(instructor_fixture, client):
        mine = ClassOfferingFactory(
            instructor=instructor_fixture, slug="mine-pub", status=ClassOffering.Status.PUBLISHED
        )
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_class_detail", kwargs={"pk": mine.pk}))
        assert reverse("classes:teach_class_edit", kwargs={"pk": mine.pk}).encode() in resp.content
        assert b"Edit details" in resp.content


def describe_teach_registrations_tab():
    def it_shows_my_classs_registrant(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-regs")
        RegistrationFactory(class_offering=mine, first_name="Jess", last_name="Park")
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_class_registrations", kwargs={"pk": mine.pk}))
        assert resp.status_code == 200
        assert b"Jess" in resp.content


def describe_teach_waitlist_tab():
    def it_lists_waitlisted(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-wait")
        RegistrationFactory(
            class_offering=mine, first_name="Wait", last_name="Lister", status=Registration.Status.WAITLISTED
        )
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_class_waitlist", kwargs={"pk": mine.pk}))
        assert resp.status_code == 200
        assert b"Wait" in resp.content


def describe_teach_discount_codes_tab():
    def it_shows_a_class_scoped_code(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-codes")
        DiscountCodeFactory(code="MINE10", class_offering=mine)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_class_discount_codes", kwargs={"pk": mine.pk}))
        assert resp.status_code == 200
        assert b"MINE10" in resp.content
