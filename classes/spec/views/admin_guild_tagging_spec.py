"""BDD specs for the admin Guild-tagging bulk suggester."""

from __future__ import annotations

from django.urls import reverse

from classes.factories import CategoryFactory, ClassOfferingFactory
from tests.membership.factories import GuildFactory


def _guild_category(name):
    return CategoryFactory(name=name, guild=GuildFactory(name=name))


def describe_admin_guild_tagging():
    def describe_access():
        def it_redirects_anonymous_to_login(db, client):
            response = client.get(reverse("classes:admin_guild_tagging"))
            assert response.status_code == 302

        def it_forbids_plain_members(member_user, client):
            client.force_login(member_user)
            response = client.get(reverse("classes:admin_guild_tagging"))
            assert response.status_code == 403

        def it_allows_admins(admin_user, client):
            client.force_login(admin_user)
            response = client.get(reverse("classes:admin_guild_tagging"))
            assert response.status_code == 200

    def describe_get():
        def it_lists_a_guildless_offering_with_its_suggestion_preselected(admin_user, client, db):
            client.force_login(admin_user)
            metal = _guild_category("Metalworking")
            offering = ClassOfferingFactory(title="Blacksmithing 101", slug="bs-101")
            response = client.get(reverse("classes:admin_guild_tagging"))
            assert response.status_code == 200
            assert b"Blacksmithing 101" in response.content
            assert f'name="category_{offering.pk}"'.encode() in response.content
            assert f'value="{metal.pk}" selected'.encode() in response.content

        def it_excludes_offerings_already_in_a_guild_category(admin_user, client, db):
            client.force_login(admin_user)
            filed = ClassOfferingFactory(title="Already Filed", slug="filed", category=_guild_category("Woodworking"))
            guildless = ClassOfferingFactory(title="Needs A Guild", slug="needs-guild")
            response = client.get(reverse("classes:admin_guild_tagging"))
            assert response.status_code == 200
            assert f'name="category_{guildless.pk}"'.encode() in response.content
            assert f'name="category_{filed.pk}"'.encode() not in response.content

        def it_renders_the_empty_state_when_none_remain(admin_user, client, db):
            client.force_login(admin_user)
            response = client.get(reverse("classes:admin_guild_tagging"))
            assert response.status_code == 200
            assert b"All classes are filed under guild categories." in response.content

    def describe_post():
        def it_applies_a_valid_choice(admin_user, client, db):
            client.force_login(admin_user)
            metal = _guild_category("Metalworking")
            offering = ClassOfferingFactory(title="Blacksmithing 101", slug="bs-101")
            response = client.post(
                reverse("classes:admin_guild_tagging"),
                {f"category_{offering.pk}": str(metal.pk)},
            )
            assert response.status_code == 302
            assert response.url == reverse("classes:admin_guild_tagging")
            offering.refresh_from_db()
            assert offering.category_id == metal.pk

        def it_reports_the_count_in_a_message(admin_user, client, db):
            client.force_login(admin_user)
            metal = _guild_category("Metalworking")
            offering = ClassOfferingFactory(title="Blacksmithing 101", slug="bs-101")
            response = client.post(
                reverse("classes:admin_guild_tagging"),
                {f"category_{offering.pk}": str(metal.pk)},
                follow=True,
            )
            assert b"Re-filed 1 classes into guild categories." in response.content

        def it_ignores_empty_values(admin_user, client, db):
            client.force_login(admin_user)
            _guild_category("Metalworking")
            offering = ClassOfferingFactory(title="Blacksmithing 101", slug="bs-101")
            original = offering.category_id
            response = client.post(
                reverse("classes:admin_guild_tagging"),
                {f"category_{offering.pk}": ""},
            )
            assert response.status_code == 302
            offering.refresh_from_db()
            assert offering.category_id == original

        def it_ignores_a_non_guild_category_without_erroring(admin_user, client, db):
            client.force_login(admin_user)
            plain = CategoryFactory(name="Just A Category")  # no guild link
            offering = ClassOfferingFactory(title="Blacksmithing 101", slug="bs-101")
            original = offering.category_id
            response = client.post(
                reverse("classes:admin_guild_tagging"),
                {f"category_{offering.pk}": str(plain.pk)},
            )
            assert response.status_code == 302
            offering.refresh_from_db()
            assert offering.category_id == original

        def it_ignores_a_stale_offering_pk_without_erroring(admin_user, client, db):
            client.force_login(admin_user)
            metal = _guild_category("Metalworking")
            response = client.post(
                reverse("classes:admin_guild_tagging"),
                {"category_999999": str(metal.pk)},
            )
            assert response.status_code == 302
