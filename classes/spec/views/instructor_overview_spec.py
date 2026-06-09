"""BDD specs for the instructor Overview dashboard."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import (
    ClassOfferingFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, Registration
from membership.models import Member


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher T", instructor_slug="teacher-t")


@pytest.fixture
def other_instructor(db):
    user = UserFactory(username="other@example.com")
    return InstructorFactory(user=user, full_legal_name="Other", instructor_slug="other")


def describe_instructor_overview():
    def it_is_served_at_the_instructor_root(db):
        assert reverse("classes:instructor_overview") == "/classes/instructor/"

    def it_blocks_anonymous(db, client):
        resp = client.get(reverse("classes:instructor_overview"))
        assert resp.status_code == 302

    def it_blocks_inactive_members(db, client):
        user = UserFactory(username="inactive@example.com")
        InstructorFactory(user=user, status=Member.Status.FORMER)
        client.force_login(user)
        resp = client.get(reverse("classes:instructor_overview"))
        assert resp.status_code == 403

    def it_renders_for_an_active_member(member_user, client):
        client.force_login(member_user)
        resp = client.get(reverse("classes:instructor_overview"))
        assert resp.status_code == 200

    def describe_empty_state():
        def it_offers_create_first_class_when_they_have_none(instructor_fixture, client):
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            assert resp.context["has_classes"] is False
            assert reverse("classes:instructor_class_create").encode() in resp.content

    def describe_needs_attention():
        def it_lists_my_pending_class(instructor_fixture, client):
            ClassOfferingFactory(
                instructor=instructor_fixture,
                title="Forge Night",
                slug="forge",
                status=ClassOffering.Status.PENDING,
            )
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            assert b"Forge Night" in resp.content

        def it_does_not_show_another_instructors_class(instructor_fixture, other_instructor, client):
            ClassOfferingFactory(
                instructor=other_instructor,
                title="Not Mine",
                slug="notmine",
                status=ClassOffering.Status.PENDING,
            )
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            assert b"Not Mine" not in resp.content

    def describe_recent_signups():
        def it_shows_a_recent_registrant_on_my_class(instructor_fixture, client):
            mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine")
            RegistrationFactory(class_offering=mine, first_name="Jess", last_name="Park")
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            assert b"Jess" in resp.content

        def it_does_not_show_another_instructors_registrant(instructor_fixture, other_instructor, client):
            theirs = ClassOfferingFactory(instructor=other_instructor, slug="theirs-regs")
            RegistrationFactory(class_offering=theirs, first_name="NotMine", last_name="Guest")
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            assert b"NotMine" not in resp.content

    def describe_waitlist():
        def it_shows_a_class_with_an_active_waitlist(instructor_fixture, client):
            mine = ClassOfferingFactory(instructor=instructor_fixture, title="Wheel 101", slug="wheel")
            RegistrationFactory(class_offering=mine, status=Registration.Status.WAITLISTED)
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            assert b"Active waitlists" in resp.content
            assert b"Wheel 101" in resp.content

        def it_hides_the_waitlist_section_when_none(instructor_fixture, client):
            ClassOfferingFactory(instructor=instructor_fixture, slug="no-wait")
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            assert b"Active waitlists" not in resp.content

    def describe_quick_links():
        def it_links_to_registrations_codes_and_profile(instructor_fixture, client):
            # Quick links live on the populated Overview (they used to be nav tabs,
            # which Phase 2 dropped), so give the instructor a class to render them.
            ClassOfferingFactory(instructor=instructor_fixture, slug="quicklinks")
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            for name in [
                "classes:instructor_registrations",
                "classes:instructor_discount_codes",
                "classes:instructor_profile",
            ]:
                assert reverse(name).encode() in resp.content

    def describe_stats():
        def it_counts_my_published_classes(instructor_fixture, client):
            ClassOfferingFactory(instructor=instructor_fixture, slug="pub", status=ClassOffering.Status.PUBLISHED)
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            assert resp.context["stats"]["published"] == 1
