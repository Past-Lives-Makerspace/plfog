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
    def it_shows_the_top_level_tabs(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        # Overview, Classes, Registrations, and Discount Codes are top-level tabs.
        # With the footer quick-links removed, these URLs appear only in the tab strip.
        assert reverse("classes:teach_overview").encode() in resp.content
        assert reverse("classes:teach_dashboard").encode() in resp.content
        assert reverse("classes:teach_registrations").encode() in resp.content
        assert reverse("classes:teach_discount_codes").encode() in resp.content

    def it_keeps_profile_out_of_the_nav(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        # Profile redirects to hub settings, so it's never linked as a teach tab.
        assert reverse("classes:teach_profile").encode() not in resp.content

    def it_offers_a_live_catalog_link(instructor_fixture, client, settings):
        settings.BOOK_BASE_URL = "https://book.example.test"
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        assert b"https://book.example.test/classes/" in resp.content
        assert b"View live catalog" in resp.content
