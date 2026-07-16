"""BDD specs for the admin Categories tab."""

from __future__ import annotations

from django.urls import reverse


def describe_admin_categories():
    def it_lists_categories(admin_user, client, db):
        from classes.factories import CategoryFactory

        client.force_login(admin_user)
        CategoryFactory(name="Pottery")
        response = client.get(reverse("classes:admin_categories"))
        assert response.status_code == 200
        assert b"Pottery" in response.content

    def it_labels_the_tab_as_guilds(admin_user, client, db):
        from classes.factories import CategoryFactory

        client.force_login(admin_user)
        CategoryFactory(name="Pottery")
        response = client.get(reverse("classes:admin_categories"))
        assert response.status_code == 200
        assert b"+ New Guild Type" in response.content
        assert b"Search Guild Types" in response.content

    def it_shows_a_guild_empty_state_when_none_exist(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_categories"))
        assert response.status_code == 200
        assert b"No Guild Types yet" in response.content

    def it_renders_the_create_form_on_get(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_category_create"))
        assert response.status_code == 200

    def it_creates_a_category(admin_user, client, db):
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_category_create"),
            {"name": "Pottery", "slug": "pottery", "sort_order": 0},
        )
        assert response.status_code == 302
        from classes.models import Category

        assert Category.objects.filter(slug="pottery").exists()

    def it_renders_the_edit_form_on_get(admin_user, client, db):
        from classes.factories import CategoryFactory

        client.force_login(admin_user)
        cat = CategoryFactory(name="Existing")
        response = client.get(reverse("classes:admin_category_edit", kwargs={"pk": cat.pk}))
        assert response.status_code == 200

    def it_edits_a_category(admin_user, client, db):
        from classes.factories import CategoryFactory

        client.force_login(admin_user)
        cat = CategoryFactory(name="Old")
        response = client.post(
            reverse("classes:admin_category_edit", kwargs={"pk": cat.pk}),
            {"name": "New", "slug": cat.slug, "sort_order": 1},
        )
        assert response.status_code == 302
        cat.refresh_from_db()
        assert cat.name == "New"

    def it_deletes_a_category(admin_user, client, db):
        from classes.factories import CategoryFactory
        from classes.models import Category

        client.force_login(admin_user)
        cat = CategoryFactory()
        response = client.post(reverse("classes:admin_category_delete", kwargs={"pk": cat.pk}))
        assert response.status_code == 302
        assert not Category.objects.filter(pk=cat.pk).exists()

    def it_ignores_get_on_delete_and_redirects(admin_user, client, db):
        from classes.factories import CategoryFactory
        from classes.models import Category

        client.force_login(admin_user)
        cat = CategoryFactory()
        response = client.get(reverse("classes:admin_category_delete", kwargs={"pk": cat.pk}))
        assert response.status_code == 302
        assert Category.objects.filter(pk=cat.pk).exists()

    def it_gates_create_behind_admin_role(member_user, client, db):
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_category_create"))
        assert response.status_code == 403
