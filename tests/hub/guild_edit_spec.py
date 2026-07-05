"""BDD specs for the hub guild edit + product create/delete views."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from django.core.files.uploadedfile import SimpleUploadedFile

from billing.models import Product, ProductRevenueSplit
from hub.forms import GuildEditForm
from membership.models import Guild, GuildFAQItem, GuildImage, GuildLink, Member
from tests.membership.factories import (
    GuildAnnouncementFactory,
    GuildFactory,
    GuildFAQItemFactory,
    GuildLinkFactory,
    MembershipPlanFactory,
)

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    """Create a user and set the linked Member's fog_role."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member  # auto-linked via signal
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _product_post_payload(guild) -> dict:
    return {
        "name": "Test Bag",
        "price": "12.00",
        "guild": str(guild.pk),
        "splits-TOTAL_FORMS": "2",
        "splits-INITIAL_FORMS": "0",
        "splits-MIN_NUM_FORMS": "1",
        "splits-MAX_NUM_FORMS": "1000",
        "splits-0-recipient_type": "admin",
        "splits-0-guild": "",
        "splits-0-percent": "20",
        "splits-1-recipient_type": "guild",
        "splits-1-guild": str(guild.pk),
        "splits-1-percent": "80",
    }


def _empty_guild_formsets() -> dict:
    """Minimal management-form data so the guild_edit FAQ/Link formsets validate."""
    return {
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
        "links-TOTAL_FORMS": "0",
        "links-INITIAL_FORMS": "0",
        "links-MIN_NUM_FORMS": "0",
        "links-MAX_NUM_FORMS": "1000",
    }


@pytest.mark.django_db
def describe_guild_product_create():
    def it_admin_can_create_a_product(client: Client):
        _user_with_role("admin1", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="admin1", password="pass")
        url = reverse("hub_guild_product_create", args=[guild.pk])
        response = client.post(url, data=_product_post_payload(guild))
        assert response.status_code == 302
        assert Product.objects.count() == 1
        product = Product.objects.first()
        assert product.guild == guild
        assert product.splits.count() == 2

    def it_guild_officer_can_create_a_product(client: Client):
        _user_with_role("officer1", fog_role=Member.FogRole.GUILD_OFFICER)
        guild = GuildFactory()
        client.login(username="officer1", password="pass")
        url = reverse("hub_guild_product_create", args=[guild.pk])
        response = client.post(url, data=_product_post_payload(guild))
        assert response.status_code == 302
        assert Product.objects.count() == 1

    def it_guild_lead_can_create_a_product_for_their_guild(client: Client):
        user = _user_with_role("lead1", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="lead1", password="pass")
        url = reverse("hub_guild_product_create", args=[guild.pk])
        response = client.post(url, data=_product_post_payload(guild))
        assert response.status_code == 302
        assert Product.objects.count() == 1

    def it_regular_member_cannot_create_a_product(client: Client):
        _user_with_role("reg1", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="reg1", password="pass")
        url = reverse("hub_guild_product_create", args=[guild.pk])
        response = client.post(url, data=_product_post_payload(guild))
        assert response.status_code == 403
        assert Product.objects.count() == 0

    def it_anonymous_user_is_redirected_to_login(client: Client):
        guild = GuildFactory()
        url = reverse("hub_guild_product_create", args=[guild.pk])
        response = client.post(url, data=_product_post_payload(guild))
        # @login_required redirects before the permission check fires.
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]
        assert Product.objects.count() == 0

    def it_rejects_invalid_sum_and_does_not_create(client: Client):
        _user_with_role("adminbad", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="adminbad", password="pass")
        url = reverse("hub_guild_product_create", args=[guild.pk])
        data = _product_post_payload(guild)
        data["splits-1-percent"] = "70"  # total 90 != 100
        response = client.post(url, data=data)
        assert response.status_code == 302
        assert Product.objects.count() == 0


@pytest.mark.django_db
def describe_guild_product_update():
    def _make_product(guild, *, name: str = "Original", price: str = "5.00") -> Product:
        product = Product.objects.create(name=name, price=Decimal(price), guild=guild)
        ProductRevenueSplit.objects.create(
            product=product,
            recipient_type="admin",
            guild=None,
            percent=Decimal("20"),
        )
        ProductRevenueSplit.objects.create(
            product=product,
            recipient_type="guild",
            guild=guild,
            percent=Decimal("80"),
        )
        return product

    def _update_payload(guild, *, name: str = "Renamed", price: str = "20.00") -> dict:
        data = _product_post_payload(guild)
        data["name"] = name
        data["price"] = price
        return data

    def it_updates_a_product_as_admin(client: Client):
        _user_with_role("admin_upd", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        product = _make_product(guild)
        client.login(username="admin_upd", password="pass")
        url = reverse("hub_guild_product_update", args=[guild.pk, product.pk])
        response = client.post(url, data=_update_payload(guild))
        assert response.status_code == 302
        product.refresh_from_db()
        assert product.name == "Renamed"
        assert product.price == Decimal("20.00")
        assert Product.objects.count() == 1

    def it_allows_guild_officer_to_update(client: Client):
        _user_with_role("officer_upd", fog_role=Member.FogRole.GUILD_OFFICER)
        guild = GuildFactory()
        product = _make_product(guild)
        client.login(username="officer_upd", password="pass")
        url = reverse("hub_guild_product_update", args=[guild.pk, product.pk])
        response = client.post(url, data=_update_payload(guild, name="OfficerEdit"))
        assert response.status_code == 302
        product.refresh_from_db()
        assert product.name == "OfficerEdit"

    def it_allows_the_guild_lead_to_update(client: Client):
        user = _user_with_role("lead_upd", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member)
        product = _make_product(guild)
        client.login(username="lead_upd", password="pass")
        url = reverse("hub_guild_product_update", args=[guild.pk, product.pk])
        response = client.post(url, data=_update_payload(guild, name="LeadEdit"))
        assert response.status_code == 302
        product.refresh_from_db()
        assert product.name == "LeadEdit"

    def it_rejects_a_regular_member_with_403(client: Client):
        _user_with_role("reg_upd", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        product = _make_product(guild)
        client.login(username="reg_upd", password="pass")
        url = reverse("hub_guild_product_update", args=[guild.pk, product.pk])
        response = client.post(url, data=_update_payload(guild, name="Hacked"))
        assert response.status_code == 403
        product.refresh_from_db()
        assert product.name == "Original"

    def it_rejects_get_requests_with_405(client: Client):
        _user_with_role("admin_get", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        product = _make_product(guild)
        client.login(username="admin_get", password="pass")
        url = reverse("hub_guild_product_update", args=[guild.pk, product.pk])
        response = client.get(url)
        assert response.status_code == 405

    def it_shows_form_errors_and_redirects_back_when_invalid(client: Client):
        _user_with_role("admin_inv", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        product = _make_product(guild)
        client.login(username="admin_inv", password="pass")
        url = reverse("hub_guild_product_update", args=[guild.pk, product.pk])
        data = _update_payload(guild)
        data["name"] = ""  # required
        response = client.post(url, data=data, follow=True)
        assert response.status_code == 200
        product.refresh_from_db()
        assert product.name == "Original"
        msgs = [str(m) for m in response.context["messages"]]
        assert any("name" in m.lower() for m in msgs)

    def it_updates_the_revenue_splits_when_they_change(client: Client):
        _user_with_role("admin_split", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        product = _make_product(guild)
        # Original: 20% admin / 80% guild. Flip to 50/50.
        client.login(username="admin_split", password="pass")
        url = reverse("hub_guild_product_update", args=[guild.pk, product.pk])
        data = _update_payload(guild)
        data["splits-0-percent"] = "50"
        data["splits-1-percent"] = "50"
        response = client.post(url, data=data)
        assert response.status_code == 302
        product.refresh_from_db()
        assert product.splits.count() == 2
        admin_split = product.splits.get(recipient_type="admin")
        guild_split = product.splits.get(recipient_type="guild")
        assert admin_split.percent == Decimal("50.00")
        assert guild_split.percent == Decimal("50.00")
        assert guild_split.guild_id == guild.pk


@pytest.mark.django_db
def describe_guild_product_delete():
    def _make_product(guild):
        product = Product.objects.create(name="x", price=Decimal("5.00"), guild=guild)
        ProductRevenueSplit.objects.create(
            product=product,
            recipient_type="admin",
            guild=None,
            percent=Decimal("100"),
        )
        return product

    def it_admin_can_delete_a_product(client: Client):
        _user_with_role("admin_del", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        product = _make_product(guild)
        client.login(username="admin_del", password="pass")
        url = reverse("hub_guild_product_delete", args=[guild.pk, product.pk])
        response = client.post(url)
        assert response.status_code == 302
        assert Product.objects.count() == 0

    def it_guild_lead_can_delete_their_products(client: Client):
        user = _user_with_role("lead_del", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member)
        product = _make_product(guild)
        client.login(username="lead_del", password="pass")
        url = reverse("hub_guild_product_delete", args=[guild.pk, product.pk])
        response = client.post(url)
        assert response.status_code == 302
        assert Product.objects.count() == 0

    def it_regular_member_cannot_delete(client: Client):
        _user_with_role("reg_del", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        product = _make_product(guild)
        client.login(username="reg_del", password="pass")
        url = reverse("hub_guild_product_delete", args=[guild.pk, product.pk])
        response = client.post(url)
        assert response.status_code == 403
        assert Product.objects.count() == 1


@pytest.mark.django_db
def describe_guild_edit():
    def it_admin_can_edit_name_and_about(client: Client):
        _user_with_role("admin_e", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Old", about="Old about")
        client.login(username="admin_e", password="pass")
        url = reverse("hub_guild_edit", args=[guild.pk])
        response = client.post(url, data={"name": "New Name", "about": "New about", **_empty_guild_formsets()})
        assert response.status_code == 302
        guild.refresh_from_db()
        assert guild.name == "New Name"
        assert guild.about == "New about"

    def it_guild_lead_can_edit_their_guild(client: Client):
        user = _user_with_role("lead_e", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member, name="Old")
        client.login(username="lead_e", password="pass")
        url = reverse("hub_guild_edit", args=[guild.pk])
        response = client.post(url, data={"name": "Lead Edit", "about": "", **_empty_guild_formsets()})
        assert response.status_code == 302
        guild.refresh_from_db()
        assert guild.name == "Lead Edit"

    def it_regular_member_cannot_edit(client: Client):
        _user_with_role("reg_e", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(name="Keep")
        client.login(username="reg_e", password="pass")
        url = reverse("hub_guild_edit", args=[guild.pk])
        response = client.post(url, data={"name": "Hacked", "about": ""})
        assert response.status_code == 403
        guild.refresh_from_db()
        assert guild.name == "Keep"

    def it_anonymous_is_redirected_to_login(client: Client):
        guild = GuildFactory(name="Keep")
        url = reverse("hub_guild_edit", args=[guild.pk])
        response = client.post(url, data={"name": "X", "about": ""})
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]
        guild.refresh_from_db()
        assert guild.name == "Keep"

    def it_surfaces_form_errors_when_invalid(client: Client):
        # name is required → posting an empty name re-renders the page with an inline field error.
        _user_with_role("admin_einv", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Keep")
        client.login(username="admin_einv", password="pass")
        url = reverse("hub_guild_edit", args=[guild.pk])
        response = client.post(url, data={"name": "", "about": "new about", **_empty_guild_formsets()})
        assert response.status_code == 200
        # Guild was not changed
        guild.refresh_from_db()
        assert guild.name == "Keep"
        # The form re-renders with an inline error on the name field
        assert "name" in response.context["form"].errors

    def it_redirects_to_the_detail_page_after_a_normal_save(client: Client):
        _user_with_role("admin_det", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Keep")
        client.login(username="admin_det", password="pass")
        url = reverse("hub_guild_edit", args=[guild.pk])
        response = client.post(url, data={"name": "Keep", "about": "", **_empty_guild_formsets()})
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_guild_detail", args=[guild.slug])

    def it_deletes_a_link_via_its_own_save_view_and_returns_to_the_content_tab(client: Client):
        # Link deletion moved OUT of the main edit form into guild_links_save, which posts to its
        # own action and redirects back to ?tab=content. (Was the old main-form `after=edit` flow.)
        _user_with_role("admin_lnk", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Forge")
        link = GuildLinkFactory(guild=guild, label="Old Discord", url="https://discord.gg/x")
        client.login(username="admin_lnk", password="pass")
        url = reverse("hub_guild_links_save", args=[guild.pk])
        data = {
            "links-TOTAL_FORMS": "1",
            "links-INITIAL_FORMS": "1",
            "links-MIN_NUM_FORMS": "0",
            "links-MAX_NUM_FORMS": "1000",
            "links-0-id": str(link.pk),
            "links-0-label": link.label,
            "links-0-url": link.url,
            "links-0-sort_order": "0",
            "links-0-DELETE": "on",
        }
        response = client.post(url, data=data)
        assert response.status_code == 302
        # Lands back on the FAQ & Links tab ...
        assert response["Location"] == reverse("hub_guild_edit", args=[guild.pk]) + "?tab=content"
        # ... and the link is gone.
        assert not guild.links.filter(pk=link.pk).exists()


@pytest.mark.django_db
def describe_guild_product_create_errors():
    def it_flashes_form_errors_when_required_fields_missing(client: Client):
        _user_with_role("admin_pcerr", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="admin_pcerr", password="pass")
        url = reverse("hub_guild_product_create", args=[guild.pk])
        # Missing name + price → ProductForm errors. Splits formset is empty.
        data = {
            "name": "",
            "price": "",
            "guild": str(guild.pk),
            "splits-TOTAL_FORMS": "1",
            "splits-INITIAL_FORMS": "0",
            "splits-MIN_NUM_FORMS": "1",
            "splits-MAX_NUM_FORMS": "1000",
            "splits-0-recipient_type": "admin",
            "splits-0-guild": "",
            "splits-0-percent": "100",
        }
        response = client.post(url, data=data, follow=True)
        assert response.status_code == 200
        msgs = [str(m) for m in response.context["messages"]]
        # Flash messages should mention at least one of the missing fields
        assert any("name" in m.lower() or "price" in m.lower() for m in msgs)
        assert Product.objects.count() == 0

    def it_flashes_non_form_errors_when_splits_dont_sum_to_100(client: Client):
        _user_with_role("admin_pcsum", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="admin_pcsum", password="pass")
        url = reverse("hub_guild_product_create", args=[guild.pk])
        data = _product_post_payload(guild)
        data["splits-1-percent"] = "70"  # 20 + 70 = 90 ≠ 100
        response = client.post(url, data=data, follow=True)
        assert response.status_code == 200
        msgs = [str(m) for m in response.context["messages"]]
        assert any("100" in m or "Splits" in m for m in msgs)
        assert Product.objects.count() == 0

    def it_flashes_per_row_errors_when_a_row_is_invalid(client: Client):
        _user_with_role("admin_pcrow", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="admin_pcrow", password="pass")
        url = reverse("hub_guild_product_create", args=[guild.pk])
        data = _product_post_payload(guild)
        # Make row 0 invalid: blank percent
        data["splits-0-percent"] = ""
        response = client.post(url, data=data, follow=True)
        assert response.status_code == 200
        msgs = [str(m) for m in response.context["messages"]]
        assert any("Split row" in m for m in msgs)
        assert Product.objects.count() == 0


@pytest.mark.django_db
def describe_guild_detail_edit_buttons():
    def it_shows_edit_buttons_for_admin(client: Client):
        _user_with_role("admin_btn", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="admin_btn", password="pass")
        response = client.get(f"/guilds/{guild.slug}/")
        assert response.status_code == 200
        assert b'@click="openProductCreateModal()"' in response.content
        # The edit modal is now a full page — the hero links to it.
        assert f"/guilds/{guild.pk}/edit/".encode() in response.content

    def it_shows_edit_buttons_for_guild_lead(client: Client):
        user = _user_with_role("lead_btn", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="lead_btn", password="pass")
        response = client.get(f"/guilds/{guild.slug}/")
        assert b'@click="openProductCreateModal()"' in response.content
        assert f"/guilds/{guild.pk}/edit/".encode() in response.content

    def it_hides_edit_buttons_for_regular_member(client: Client):
        _user_with_role("reg_btn", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="reg_btn", password="pass")
        response = client.get(f"/guilds/{guild.slug}/")
        # Check the Alpine dispatch string for Add Product and the edit-page
        # link — the literal button labels ("Edit Guild Page" / "Add Product")
        # also appear in the changelog modal on every page, so those aren't
        # reliable markers.
        assert b'@click="openProductCreateModal()"' not in response.content
        assert f"/guilds/{guild.pk}/edit/".encode() not in response.content


def _tiny_png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf"
        b"\xc0\x00\x00\x00\x03\x00\x01\x5b\x0d\xc1\x6a\x00\x00\x00\x00IEND\xae"
        b"B`\x82"
    )


@pytest.mark.django_db
def describe_guild_banner_delete():
    """Tests for the POST-only guild banner clearing endpoint."""

    def it_requires_login(client: Client):
        guild = GuildFactory()

        response = client.post(reverse("hub_guild_banner_delete", args=[guild.pk]))

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_rejects_non_POST_requests(client: Client):
        _user_with_role("b_get", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="b_get", password="pass")

        response = client.get(reverse("hub_guild_banner_delete", args=[guild.pk]))

        assert response.status_code == 405

    def it_forbids_regular_members(client: Client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        _user_with_role("b_reg", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(banner_image=SimpleUploadedFile("keep.png", _tiny_png_bytes(), content_type="image/png"))
        original_name = guild.banner_image.name
        client.login(username="b_reg", password="pass")

        response = client.post(reverse("hub_guild_banner_delete", args=[guild.pk]))

        assert response.status_code == 403
        guild.refresh_from_db()
        assert guild.banner_image.name == original_name

    def it_clears_the_banner_and_redirects_to_guild_detail(client: Client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        _user_with_role("b_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(banner_image=SimpleUploadedFile("banner.png", _tiny_png_bytes(), content_type="image/png"))
        assert guild.banner_image
        client.login(username="b_admin", password="pass")

        response = client.post(reverse("hub_guild_banner_delete", args=[guild.pk]))

        assert response.status_code == 302
        assert response.url == f"/guilds/{guild.slug}/"
        guild.refresh_from_db()
        assert not guild.banner_image

    def it_is_a_noop_when_no_banner_is_set(client: Client):
        _user_with_role("b_empty", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="b_empty", password="pass")

        response = client.post(reverse("hub_guild_banner_delete", args=[guild.pk]))

        assert response.status_code == 302
        assert response.url == f"/guilds/{guild.slug}/"


@pytest.mark.django_db
def describe_GuildEditForm():
    def it_accepts_the_new_fields():
        form = GuildEditForm(
            data={
                "name": "Painters",
                "about": "We paint",
                "calendar_color": "#4B9FEE",
                "youtube_url": "https://youtube.com/watch?v=abc12345678",
                "meeting_schedule": "Tuesdays 6pm",
                "contact_email": "p@example.com",
                "show_members": "on",
            }
        )
        assert form.is_valid(), form.errors

    def it_saves_a_custom_faq_label():
        guild = GuildFactory(name="Ceramics")
        form = GuildEditForm(
            data={"name": "Ceramics", "calendar_color": "#4B9FEE", "faq_label": "Ceramics Info"},
            instance=guild,
        )
        assert form.is_valid(), form.errors
        assert form.save().faq_label == "Ceramics Info"

    def it_coerces_a_blank_faq_label_back_to_FAQ():
        guild = GuildFactory(name="Weavers")
        form = GuildEditForm(
            data={"name": "Weavers", "calendar_color": "#4B9FEE", "faq_label": ""},
            instance=guild,
        )
        assert form.is_valid(), form.errors
        assert form.save().faq_label == "FAQ"

    def it_makes_a_guild_private_when_make_private_is_checked():
        guild = GuildFactory(name="Secret Guild", is_public=True)
        form = GuildEditForm(
            data={"name": "Secret Guild", "calendar_color": "#4B9FEE", "make_private": "on"},
            instance=guild,
        )
        assert form.is_valid(), form.errors
        assert form.save().is_public is False

    def it_makes_a_guild_public_when_make_private_is_unchecked():
        guild = GuildFactory(name="Reopened Guild", is_public=False)
        form = GuildEditForm(
            data={"name": "Reopened Guild", "calendar_color": "#4B9FEE"},  # make_private omitted = unchecked
            instance=guild,
        )
        assert form.is_valid(), form.errors
        assert form.save().is_public is True

    def it_initializes_make_private_from_the_stored_is_public_value():
        public = GuildFactory(name="Open Guild", is_public=True)
        private = GuildFactory(name="Closed Guild", is_public=False)
        assert GuildEditForm(instance=public).fields["make_private"].initial is False
        assert GuildEditForm(instance=private).fields["make_private"].initial is True


@pytest.mark.django_db
def describe_guild_edit_page():
    def it_renders_the_edit_form_for_an_editor(client: Client):
        _user_with_role("admin_pg", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="admin_pg", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 200
        assert b"Meeting cadence" in response.content

    def it_forbids_non_editors(client: Client):
        _user_with_role("plain_pg", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="plain_pg", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 403

    def it_saves_basic_fields_on_post(client: Client):
        # The main edit form now covers only Basic/Meetings/Images; FAQ saves through its own
        # view (guild_faq_save), exercised in describe_guild_faq_save.
        _user_with_role("admin_save", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Old")
        client.login(username="admin_save", password="pass")
        response = client.post(
            reverse("hub_guild_edit", args=[guild.pk]),
            {
                "name": "New Name",
                "about": "About",
                "calendar_color": "#4B9FEE",
                "youtube_url": "",
                "meeting_schedule": "Tuesdays",
                "contact_email": "",
            },
        )
        assert response.status_code == 302
        guild.refresh_from_db()
        assert guild.name == "New Name"


@pytest.mark.django_db
def describe_guild_edit_delete_controls():
    def it_renders_per_image_and_announcement_delete_controls(client: Client):
        _user_with_role("admin_del", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        img = GuildImage.objects.create(guild=guild, image=SimpleUploadedFile("g.png", _PNG))
        announcement = GuildAnnouncementFactory(guild=guild)
        client.login(username="admin_del", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 200

        # Gallery manager should be present with the image ID
        assert f'data-id="{img.pk}"'.encode() in response.content
        # AJAX prefixes should be present
        assert f"/guilds/{guild.pk}/images/".encode() in response.content

        # Announcement delete control is still a standard modal trigger
        assert f"del-ann-{announcement.pk}".encode() in response.content
        assert reverse("hub_guild_announcement_delete", args=[guild.pk, announcement.pk]).encode() in response.content

    def it_renders_a_red_immediate_delete_button_for_each_saved_link(client: Client):
        _user_with_role("admin_lnkbtn", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        GuildLinkFactory(guild=guild, label="Discord")
        client.login(username="admin_lnkbtn", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 200
        # A red button (not a save-time toggle switch) that submits its own form immediately.
        # (Links now lives in its own form posting to guild_links_save, so the old main-form
        # `after` hidden-input trick is gone — see describe_guild_content_tab_template.)
        assert b"Delete this link" in response.content
        assert b"requestSubmit()" in response.content


@pytest.mark.django_db
def describe_guild_edit_tabs():
    def it_splits_the_long_form_into_tabbed_sections(client: Client):
        _user_with_role("admin_tabs", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="admin_tabs", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 200
        # Alpine tab state reads ?tab= from the URL, defaulting to Basic Information.
        assert b"new URLSearchParams(window.location.search).get('tab') || 'basic'" in response.content
        # Every tab — including the formerly-standalone Meeting Notes / Events / Orientations — is now
        # a switchable in-page button driven by the same Alpine `section` state.
        for tab in (
            b"basic",
            b"meetings",
            b"meeting_notes",
            b"events",
            b"orientations",
            b"images",
            b"content",
            b"announcements",
            b"staff",
        ):
            assert b"section = '" + tab + b"'" in response.content
        # Calendar Integration and FAQ/Links live under tabs (using discretion).
        assert b"Calendar Integration" in response.content
        assert b"FAQ &amp; Links" in response.content
        # The formerly-standalone sections now render inline on the same page.
        assert b"+ Add meeting notes" in response.content
        assert b"+ Add event" in response.content
        assert b"Recurring hours" in response.content
        assert b"Save orientation settings" in response.content

    def it_lays_short_inputs_out_in_two_columns(client: Client):
        _user_with_role("admin_grid", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="admin_grid", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 200
        assert b'class="pl-form-grid"' in response.content

    def it_offers_a_button_to_add_more_faq_questions(client: Client):
        _user_with_role("admin_faq_add", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="admin_faq_add", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 200
        # The clone-source template and the add button are both present.
        assert b'id="faq-empty-template"' in response.content
        assert b"+ Add a question" in response.content

    def it_offers_a_button_to_add_more_links(client: Client):
        _user_with_role("admin_link_add", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="admin_link_add", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 200
        # The clone-source template and the add button are both present.
        assert b'id="link-empty-template"' in response.content
        assert b"+ Add a link" in response.content


@pytest.mark.django_db
def describe_guild_faq_save():
    """FAQ now saves through its own form/view, separate from the main edit form."""

    def it_adds_a_new_faq_row_and_redirects_to_the_content_tab(client: Client):
        _user_with_role("faq_add", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="faq_add", password="pass")
        data = {
            "faq-TOTAL_FORMS": "1",
            "faq-INITIAL_FORMS": "0",
            "faq-MIN_NUM_FORMS": "0",
            "faq-MAX_NUM_FORMS": "1000",
            "faq-0-question": "What should I bring?",
            "faq-0-answer": "Just yourself.",
            "faq-0-sort_order": "0",
        }
        response = client.post(reverse("hub_guild_faq_save", args=[guild.pk]), data=data, follow=True)
        assert response.status_code == 200
        assert response.redirect_chain[-1][0] == reverse("hub_guild_edit", args=[guild.pk]) + "?tab=content"
        assert GuildFAQItem.objects.filter(guild=guild, question="What should I bring?").exists()
        msgs = [str(m) for m in response.context["messages"]]
        assert "FAQ saved." in msgs

    def _faq_post(question: str = "Q?", answer: str = "A", **extra: str) -> dict[str, str]:
        """One unsaved FAQ row's POST payload (management form + the row fields)."""
        data = {
            "faq-TOTAL_FORMS": "1",
            "faq-INITIAL_FORMS": "0",
            "faq-MIN_NUM_FORMS": "0",
            "faq-MAX_NUM_FORMS": "1000",
            "faq-0-question": question,
            "faq-0-answer": answer,
            "faq-0-sort_order": "0",
        }
        data.update(extra)
        return data

    def it_saves_a_youtube_video_on_an_answer(client: Client):
        _user_with_role("faq_vid", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="faq_vid", password="pass")
        data = _faq_post(**{"faq-0-video_url": "https://youtu.be/dQw4w9WgXcQ"})
        client.post(reverse("hub_guild_faq_save", args=[guild.pk]), data=data)
        faq = GuildFAQItem.objects.get(guild=guild)
        assert faq.video_url == "https://youtu.be/dQw4w9WgXcQ"

    def it_rejects_a_non_youtube_video(client: Client):
        _user_with_role("faq_vidbad", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="faq_vidbad", password="pass")
        data = _faq_post(**{"faq-0-video_url": "https://vimeo.com/123"})
        response = client.post(reverse("hub_guild_faq_save", args=[guild.pk]), data=data, follow=True)
        assert any("Couldn't save the FAQ" in str(m) for m in response.context["messages"])
        assert not GuildFAQItem.objects.filter(guild=guild).exists()

    def it_saves_a_document_link(client: Client):
        _user_with_role("faq_link", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="faq_link", password="pass")
        data = _faq_post(**{"faq-0-document_url": "https://docs.example/guide"})
        client.post(reverse("hub_guild_faq_save", args=[guild.pk]), data=data)
        faq = GuildFAQItem.objects.get(guild=guild)
        assert faq.document_url == "https://docs.example/guide"

    def it_uploads_a_document_file(client: Client):
        _user_with_role("faq_file", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="faq_file", password="pass")
        data = _faq_post()
        data["faq-0-document"] = SimpleUploadedFile("handbook.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        client.post(reverse("hub_guild_faq_save", args=[guild.pk]), data=data)
        faq = GuildFAQItem.objects.get(guild=guild)
        assert faq.document.name.endswith(".pdf")

    def it_rejects_a_file_and_a_link_together(client: Client):
        _user_with_role("faq_both", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="faq_both", password="pass")
        data = _faq_post(**{"faq-0-document_url": "https://docs.example/guide"})
        data["faq-0-document"] = SimpleUploadedFile("dup.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        response = client.post(reverse("hub_guild_faq_save", args=[guild.pk]), data=data, follow=True)
        assert any("Couldn't save the FAQ" in str(m) for m in response.context["messages"])
        assert not GuildFAQItem.objects.filter(guild=guild).exists()

    def it_edits_an_existing_faq_rows_text(client: Client):
        _user_with_role("faq_edit", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        faq = GuildFAQItemFactory(guild=guild, question="Old?", answer="Old answer")
        client.login(username="faq_edit", password="pass")
        data = {
            "faq-TOTAL_FORMS": "1",
            "faq-INITIAL_FORMS": "1",
            "faq-MIN_NUM_FORMS": "0",
            "faq-MAX_NUM_FORMS": "1000",
            "faq-0-id": str(faq.pk),
            "faq-0-question": "New?",
            "faq-0-answer": "New answer",
            "faq-0-sort_order": "0",
        }
        response = client.post(reverse("hub_guild_faq_save", args=[guild.pk]), data=data)
        assert response.status_code == 302
        faq.refresh_from_db()
        assert faq.question == "New?"
        assert faq.answer == "New answer"

    def it_deletes_a_saved_faq_row_when_delete_is_checked(client: Client):
        _user_with_role("faq_del", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        faq = GuildFAQItemFactory(guild=guild)
        client.login(username="faq_del", password="pass")
        data = {
            "faq-TOTAL_FORMS": "1",
            "faq-INITIAL_FORMS": "1",
            "faq-MIN_NUM_FORMS": "0",
            "faq-MAX_NUM_FORMS": "1000",
            "faq-0-id": str(faq.pk),
            "faq-0-question": faq.question,
            "faq-0-answer": faq.answer,
            "faq-0-sort_order": "0",
            "faq-0-DELETE": "on",
        }
        response = client.post(reverse("hub_guild_faq_save", args=[guild.pk]), data=data)
        assert response.status_code == 302
        assert not GuildFAQItem.objects.filter(pk=faq.pk).exists()

    def it_saves_cleanly_with_extra_zero_no_blank_row_block(client: Client):
        # With extra=0 a POST whose TOTAL_FORMS covers only the real rows saves without a
        # phantom blank row failing required-field validation — the bug this whole split fixes.
        _user_with_role("faq_x0", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        faq = GuildFAQItemFactory(guild=guild, question="Keep?", answer="Keep")
        client.login(username="faq_x0", password="pass")
        data = {
            "faq-TOTAL_FORMS": "1",
            "faq-INITIAL_FORMS": "1",
            "faq-MIN_NUM_FORMS": "0",
            "faq-MAX_NUM_FORMS": "1000",
            "faq-0-id": str(faq.pk),
            "faq-0-question": "Keep?",
            "faq-0-answer": "Keep",
            "faq-0-sort_order": "0",
        }
        response = client.post(reverse("hub_guild_faq_save", args=[guild.pk]), data=data, follow=True)
        assert response.status_code == 200
        assert "FAQ saved." in [str(m) for m in response.context["messages"]]

    def it_flashes_an_error_when_a_row_is_invalid(client: Client):
        _user_with_role("faq_inv", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="faq_inv", password="pass")
        data = {
            "faq-TOTAL_FORMS": "1",
            "faq-INITIAL_FORMS": "0",
            "faq-MIN_NUM_FORMS": "0",
            "faq-MAX_NUM_FORMS": "1000",
            "faq-0-question": "",  # required → invalid
            "faq-0-answer": "An answer with no question",
            "faq-0-sort_order": "0",
        }
        response = client.post(reverse("hub_guild_faq_save", args=[guild.pk]), data=data, follow=True)
        assert response.status_code == 200
        msgs = [str(m) for m in response.context["messages"]]
        assert any("Couldn't save the FAQ" in m for m in msgs)
        assert not GuildFAQItem.objects.filter(guild=guild).exists()

    def it_forbids_non_editors(client: Client):
        _user_with_role("faq_403", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="faq_403", password="pass")
        data = {
            "faq-TOTAL_FORMS": "0",
            "faq-INITIAL_FORMS": "0",
            "faq-MIN_NUM_FORMS": "0",
            "faq-MAX_NUM_FORMS": "1000",
        }
        response = client.post(reverse("hub_guild_faq_save", args=[guild.pk]), data=data)
        assert response.status_code == 403

    def it_rejects_get_requests(client: Client):
        _user_with_role("faq_get", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="faq_get", password="pass")
        response = client.get(reverse("hub_guild_faq_save", args=[guild.pk]))
        assert response.status_code == 405


@pytest.mark.django_db
def describe_guild_links_save():
    """Links now saves through its own form/view, separate from the main edit form."""

    def it_adds_a_new_link_row_and_redirects_to_the_content_tab(client: Client):
        _user_with_role("lnk_add", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="lnk_add", password="pass")
        data = {
            "links-TOTAL_FORMS": "1",
            "links-INITIAL_FORMS": "0",
            "links-MIN_NUM_FORMS": "0",
            "links-MAX_NUM_FORMS": "1000",
            "links-0-label": "Discord",
            "links-0-url": "https://discord.gg/abc",
            "links-0-sort_order": "0",
        }
        response = client.post(reverse("hub_guild_links_save", args=[guild.pk]), data=data, follow=True)
        assert response.status_code == 200
        assert response.redirect_chain[-1][0] == reverse("hub_guild_edit", args=[guild.pk]) + "?tab=content"
        assert GuildLink.objects.filter(guild=guild, label="Discord").exists()
        assert "Links saved." in [str(m) for m in response.context["messages"]]

    def it_edits_an_existing_link(client: Client):
        _user_with_role("lnk_edit", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        link = GuildLinkFactory(guild=guild, label="Old", url="https://old.example.com")
        client.login(username="lnk_edit", password="pass")
        data = {
            "links-TOTAL_FORMS": "1",
            "links-INITIAL_FORMS": "1",
            "links-MIN_NUM_FORMS": "0",
            "links-MAX_NUM_FORMS": "1000",
            "links-0-id": str(link.pk),
            "links-0-label": "New",
            "links-0-url": "https://new.example.com",
            "links-0-sort_order": "0",
        }
        response = client.post(reverse("hub_guild_links_save", args=[guild.pk]), data=data)
        assert response.status_code == 302
        link.refresh_from_db()
        assert link.label == "New"
        assert link.url == "https://new.example.com"

    def it_deletes_only_the_flagged_link_and_leaves_faq_untouched(client: Client):
        # The Links Delete button posts to its OWN form now, so deleting one link removes
        # only that row — other links survive and FAQ rows (a different form) are untouched.
        _user_with_role("lnk_iso", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        keep = GuildLinkFactory(guild=guild, label="Keep")
        drop = GuildLinkFactory(guild=guild, label="Drop")
        faq = GuildFAQItemFactory(guild=guild, question="Survives?")
        client.login(username="lnk_iso", password="pass")
        data = {
            "links-TOTAL_FORMS": "2",
            "links-INITIAL_FORMS": "2",
            "links-MIN_NUM_FORMS": "0",
            "links-MAX_NUM_FORMS": "1000",
            "links-0-id": str(keep.pk),
            "links-0-label": keep.label,
            "links-0-url": keep.url,
            "links-0-sort_order": "0",
            "links-1-id": str(drop.pk),
            "links-1-label": drop.label,
            "links-1-url": drop.url,
            "links-1-sort_order": "1",
            "links-1-DELETE": "on",
        }
        response = client.post(reverse("hub_guild_links_save", args=[guild.pk]), data=data)
        assert response.status_code == 302
        assert GuildLink.objects.filter(pk=keep.pk).exists()
        assert not GuildLink.objects.filter(pk=drop.pk).exists()
        assert GuildFAQItem.objects.filter(pk=faq.pk).exists()

    def it_forbids_non_editors(client: Client):
        _user_with_role("lnk_403", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="lnk_403", password="pass")
        data = {
            "links-TOTAL_FORMS": "0",
            "links-INITIAL_FORMS": "0",
            "links-MIN_NUM_FORMS": "0",
            "links-MAX_NUM_FORMS": "1000",
        }
        response = client.post(reverse("hub_guild_links_save", args=[guild.pk]), data=data)
        assert response.status_code == 403


@pytest.mark.django_db
def describe_guild_content_tab_template():
    """Template-level guarantees for the FAQ & Links tab after the form split."""

    def it_renders_each_section_as_its_own_form_posting_to_its_save_view(client: Client):
        _user_with_role("ct_forms", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ct_forms", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 200
        assert reverse("hub_guild_faq_save", args=[guild.pk]).encode() in response.content
        assert reverse("hub_guild_links_save", args=[guild.pk]).encode() in response.content
        assert b"Save FAQ" in response.content
        assert b"Save Links" in response.content

    def it_renders_faq_delete_as_hidden_field_plus_danger_button_not_a_toggle(client: Client):
        _user_with_role("ct_faqdel", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        GuildFAQItemFactory(guild=guild)
        client.login(username="ct_faqdel", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 200
        # The real Delete button is present ...
        assert b"Delete this question" in response.content
        assert b"requestSubmit()" in response.content
        # ... and the old toggle label that form_field.html would have produced is gone.
        assert b"Delete this FAQ" not in response.content

    def it_does_not_carry_the_main_form_after_trick_on_the_split_forms(client: Client):
        # The split FAQ/Links delete buttons must NOT reference this.form.after — that hidden
        # input only lived in the main edit form and is gone from these standalone forms.
        _user_with_role("ct_after", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        GuildLinkFactory(guild=guild)
        GuildFAQItemFactory(guild=guild)
        client.login(username="ct_after", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert b"this.form.after" not in response.content

    def it_hides_the_page_wide_save_on_the_content_tab(client: Client):
        # The main form's Save only shows on the tabs it covers (Basic / Meetings / Images),
        # so it's hidden on the content tab (and on the other own-form tabs).
        _user_with_role("ct_save", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ct_save", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert b"section === 'basic' || section === 'meetings' || section === 'images'" in response.content

    def it_shows_empty_states_when_there_are_no_rows(client: Client):
        _user_with_role("ct_empty", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="ct_empty", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert b"No questions yet. Add your first." in response.content
        assert b"No links yet. Add your first." in response.content

    def it_renders_the_announcement_edit_modal_body_target(client: Client):
        # Guards blocker 1: the Edit button's hx-target must match modal.html's {{ modal_id }}-body.
        _user_with_role("ct_annmodal", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        announcement = GuildAnnouncementFactory(guild=guild)
        client.login(username="ct_annmodal", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert f'hx-target="#edit-ann-{announcement.pk}-body"'.encode() in response.content


@pytest.mark.django_db
def describe_guild_visibility_controls():
    """The 'make this page private' toggle + its effect on the Share & Print card."""

    def it_renders_the_make_private_toggle_on_the_basic_tab(client: Client):
        _user_with_role("vis_toggle", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="vis_toggle", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 200
        assert b"Make this guild page private?" in response.content

    def it_shows_the_share_and_print_actions_for_a_public_guild(client: Client):
        _user_with_role("vis_pub", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(is_public=True)
        client.login(username="vis_pub", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 200
        assert b"Download QR (SVG)" in response.content
        assert b"Open printable flyer" in response.content

    def it_hides_the_share_and_print_actions_for_a_private_guild(client: Client):
        _user_with_role("vis_priv", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(is_public=False)
        client.login(username="vis_priv", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 200
        # The share/QR/flyer actions are gone ...
        assert b"Download QR (SVG)" not in response.content
        assert b"Open printable flyer" not in response.content
        # ... replaced with an explanatory note.
        assert b"to share a link, QR code, and printable flyer" in response.content


@pytest.mark.django_db
def describe_guild_delete():
    def it_admin_can_soft_delete_the_guild(client: Client):
        _user_with_role("del_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="del_admin", password="pass")
        response = client.post(reverse("hub_guild_delete", args=[guild.pk]))
        assert response.status_code == 302
        assert response.url == reverse("home")
        # Hidden from the default manager, but the row (and its data) is preserved.
        assert not Guild.objects.filter(pk=guild.pk).exists()
        restored = Guild.all_objects.get(pk=guild.pk)
        assert restored.deleted_at is not None

    def it_guild_lead_cannot_delete_their_own_guild(client: Client):
        user = _user_with_role("del_lead", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="del_lead", password="pass")
        response = client.post(reverse("hub_guild_delete", args=[guild.pk]))
        assert response.status_code == 403
        assert Guild.objects.filter(pk=guild.pk).exists()

    def it_guild_officer_cannot_delete(client: Client):
        _user_with_role("del_officer", fog_role=Member.FogRole.GUILD_OFFICER)
        guild = GuildFactory()
        client.login(username="del_officer", password="pass")
        response = client.post(reverse("hub_guild_delete", args=[guild.pk]))
        assert response.status_code == 403
        assert Guild.objects.filter(pk=guild.pk).exists()

    def it_regular_member_cannot_delete(client: Client):
        _user_with_role("del_member", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="del_member", password="pass")
        response = client.post(reverse("hub_guild_delete", args=[guild.pk]))
        assert response.status_code == 403
        assert Guild.objects.filter(pk=guild.pk).exists()

    def it_anonymous_user_is_redirected_to_login(client: Client):
        guild = GuildFactory()
        response = client.post(reverse("hub_guild_delete", args=[guild.pk]))
        assert response.status_code == 302
        assert "/accounts/login" in response.url
        assert Guild.objects.filter(pk=guild.pk).exists()

    def it_rejects_get_requests(client: Client):
        _user_with_role("del_get", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="del_get", password="pass")
        response = client.get(reverse("hub_guild_delete", args=[guild.pk]))
        assert response.status_code == 405
        assert Guild.objects.filter(pk=guild.pk).exists()

    def it_shows_the_danger_zone_to_admins(client: Client):
        _user_with_role("dz_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="dz_admin", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert b"Danger Zone" in response.content
        assert reverse("hub_guild_delete", args=[guild.pk]).encode() in response.content

    def it_hides_the_danger_zone_from_a_guild_lead(client: Client):
        user = _user_with_role("dz_lead", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="dz_lead", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        # The lead can open the edit page, but the delete control is admin-only.
        assert response.status_code == 200
        assert b"Danger Zone" not in response.content
        assert reverse("hub_guild_delete", args=[guild.pk]).encode() not in response.content
