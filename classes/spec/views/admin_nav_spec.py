"""BDD specs for the slimmed admin nav and the live-catalog link."""

from __future__ import annotations

from django.urls import reverse


def _tab_strip(html: str) -> str:
    """Just the nav tab strip.

    The whole rendered page also carries the CHANGELOG (every hub page's context does), which
    mentions tab names in prose — a bare full-page assertion cannot tell a tab from that noise.
    """
    start = html.index('role="tablist"')
    return html[start : html.index("</nav>", start)]


def describe_admin_nav():
    def it_shows_the_three_top_level_tabs(admin_user, client, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_overview"))
        body = resp.content
        assert reverse("classes:admin_overview").encode() in body
        assert reverse("classes:admin_classes").encode() in body
        assert reverse("classes:admin_settings_hub").encode() in body

    def it_drops_the_old_top_level_tabs(admin_user, client, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_overview"))
        # Categories/Questions are no longer nav tabs; reached via the Settings hub.
        assert b">Categories<" not in resp.content
        assert b">Questions<" not in resp.content

    def it_offers_a_live_catalog_link(admin_user, client, db, settings):
        settings.BOOK_BASE_URL = "https://book.example.test"
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_overview"))
        assert b"https://book.example.test/classes/" in resp.content
        assert b"View live catalog" in resp.content


def describe_catalog_activity_tab():
    """The activity feed used to light the Overview tab from inside it; it is its own tab now."""

    def it_sits_between_registrations_and_settings(admin_user, client, db):
        client.force_login(admin_user)
        tabs = _tab_strip(client.get(reverse("classes:admin_overview")).content.decode())
        activity_at = tabs.index(">Catalog Activity<")
        assert activity_at > tabs.index(">Registrations<")
        assert activity_at < tabs.index(">Settings<")

    def it_lights_on_the_activity_page(admin_user, client, db):
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_activity")).content.decode()
        activity_link = f'href="{reverse("classes:admin_activity")}" class="vote-tab vote-tab--active"'
        assert activity_link in html

    def it_leaves_the_overview_tab_unlit_on_the_activity_page(admin_user, client, db):
        """Overview used to light for activity too, which made the feed look like part of it."""
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_activity")).content.decode()
        overview_link = f'href="{reverse("classes:admin_overview")}" class="vote-tab"'
        assert overview_link in html

    def it_is_hidden_from_a_non_admin_who_can_reach_registrations(db, client):
        """The feed spans the whole catalog, so it is admin-only like Overview and Classes."""
        from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory

        user = UserFactory(username="reg-inst@example.com")
        instructor = InstructorFactory(user=user, instructor_slug="reg-inst")
        ClassOfferingFactory(instructor=instructor, slug="theirs")
        client.force_login(user)
        response = client.get(reverse("classes:admin_registrations"))
        assert response.status_code == 200, "fixture must actually land on the registrations tab"
        assert b"Catalog Activity" not in response.content


def describe_admin_page_title():
    def it_names_the_page_manage_all_classes(admin_user, client, db):
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_overview")).content.decode()
        assert ">Manage All Classes</h1>" in html
        assert "Manage Class Catalog" not in html
        assert "Manage All Classes —" in html  # the <title>, which the tab name completes
