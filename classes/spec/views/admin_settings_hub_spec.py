"""BDD specs for the admin Settings hub landing."""

from __future__ import annotations

from django.urls import reverse


def describe_admin_settings_hub():
    def it_gates_behind_admin_role(member_user, client, db):
        client.force_login(member_user)
        resp = client.get(reverse("classes:admin_settings_hub"))
        assert resp.status_code == 403

    def it_renders_for_admin(admin_user, client, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_settings_hub"))
        assert resp.status_code == 200

    def it_is_served_at_admin_settings(admin_user, client, db):
        assert reverse("classes:admin_settings_hub") == "/classes/admin/settings/"

    def it_links_to_each_config_area(admin_user, client, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_settings_hub"))
        for name in [
            "classes:admin_categories",
            "classes:admin_discount_codes",
            "classes:admin_registration_questions",
            "classes:admin_settings",
            "classes:admin_teaching_page_settings",
        ]:
            assert reverse(name).encode() in resp.content

    def it_splits_waivers_and_the_teaching_page_into_two_tiles(admin_user, client, db):
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_settings_hub")).content.decode()
        assert "Waivers &amp; Reminders</h3>" in content
        assert "Liability text, photo release text, and reminder timing." in content
        assert "Teaching Marketing Page</h3>" in content
        assert (
            "The page a member sees under Host a Workshop until an admin says yes. Every word of it, plus the money split."
            in content
        )
        assert "Host a Workshop</h3>" not in content
        waivers_tile = content.split("Waivers &amp; Reminders</h3>")[0].rsplit("<a ", 1)[1]
        assert reverse("classes:admin_settings") in waivers_tile
        teaching_tile = content.split("Teaching Marketing Page</h3>")[0].rsplit("<a ", 1)[1]
        assert reverse("classes:admin_teaching_page_settings") in teaching_tile


def describe_waivers_form_move():
    def it_serves_the_waivers_form_at_its_new_path(admin_user, client, db):
        assert reverse("classes:admin_settings") == "/classes/admin/settings/waivers/"

    def it_still_renders_the_waivers_form(admin_user, client, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_settings"))
        assert resp.status_code == 200
