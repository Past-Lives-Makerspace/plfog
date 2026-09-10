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

    def it_links_profile_as_the_last_tab(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_overview")).content.decode()
        profile_at = html.index(reverse("classes:teach_profile"))
        assert profile_at > html.index(reverse("classes:teach_registrations"))
        assert html.index("</nav>", profile_at) > profile_at

    def it_offers_a_live_catalog_link(instructor_fixture, client, settings):
        settings.BOOK_BASE_URL = "https://book.example.test"
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        assert b"https://book.example.test/classes/" in resp.content
        assert b"View live catalog" in resp.content


def describe_teach_portal_naming():
    def it_names_the_page_manage_my_classes(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_overview")).content.decode()
        assert ">Manage My Classes</h1>" in html
        # NOT a "&amp; Workshops" absence assertion: the CHANGELOG renders into every hub page
        # and quotes the old name, so that would pass or fail for the wrong reason.
        assert ">Manage Classes &amp; Workshops</h1>" not in html
        assert "Manage My Classes —" in html  # the <title>, which the tab name completes

    def it_labels_the_classes_tab_my_classes(instructor_fixture, client):
        """The Registrations tab's empty state already called it that; now the two agree."""
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_overview")).content.decode()
        assert f'href="{reverse("classes:teach_dashboard")}" class="vote-tab">My Classes</a>' in html

    def it_labels_the_profile_tab_instructor_profile(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_overview")).content.decode()
        assert f'href="{reverse("classes:teach_profile")}" class="vote-tab">Instructor Profile</a>' in html

    def it_leaves_the_admin_classes_tab_alone(admin_user, client, db):
        """Only the instructor portal's tab was renamed."""
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_overview")).content.decode()
        assert f'href="{reverse("classes:admin_classes")}" class="vote-tab">Classes</a>' in html


def describe_new_class_button():
    def it_matches_the_my_classes_tab_button(instructor_fixture, client):
        """It used to fall through to the borderless default, unlike its twin on My Classes."""
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_overview")).content.decode()
        create_url = reverse("classes:teach_class_create")
        expected = (
            f'<a href="{create_url}" class="hub-btn hub-btn--primary hub-btn--sm" '
            'data-help-key="teach.create-class">+ New Class</a>'
        )
        assert expected in html

    def it_keeps_the_tour_target_on_the_button(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_overview")).content.decode()
        assert 'data-help-key="teach.create-class"' in html
