"""BDD specs for the slimmed admin nav and the live-catalog link."""

from __future__ import annotations

from django.urls import reverse


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
