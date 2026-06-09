"""BDD specs for the slimmed teaching nav."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import InstructorFactory, UserFactory


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher T", instructor_slug="teacher-t")


def describe_teach_nav():
    def it_shows_overview_and_classes_tabs(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        assert reverse("classes:teach_overview").encode() in resp.content
        assert reverse("classes:teach_dashboard").encode() in resp.content

    def it_drops_the_old_tabs_from_the_nav(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        # Registrations/Discount Codes/Profile are no longer top-level tabs.
        # They survive as Overview "Quick links", but the <nav> tab strip is just two tabs.
        body = resp.content.split(b"</nav>")[0]
        assert reverse("classes:teach_registrations").encode() not in body
        assert reverse("classes:teach_profile").encode() not in body

    def it_offers_a_live_catalog_link(instructor_fixture, client, settings):
        settings.BOOK_BASE_URL = "https://book.example.test"
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        assert b"https://book.example.test/classes/" in resp.content
        assert b"View live catalog" in resp.content
