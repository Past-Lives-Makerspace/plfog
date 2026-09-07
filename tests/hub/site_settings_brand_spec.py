"""Site Settings -> Brand tab (PLAT-1).

The Brand tab is the first tab in the strip. Its seven fields (org_name, org_short_name,
org_legal_name, org_logo, org_primary_color, org_support_email, org_website_url) ride the
same ``#site-settings-form`` as every other tab, saved by the shared settings POST.
``org_name`` is the only required brand field; every other one degrades gracefully via a
fallback in the ``brand()`` context processor or the built in mark.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration
from hub.forms import SiteSettingsForm
from membership.models import Member

pytestmark = pytest.mark.django_db

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _superuser(client: Client) -> User:
    user = User.objects.create_superuser(username="brandadmin", email="brandadmin@x.com", password="p")
    client.login(username="brandadmin", password="p")
    return user


def _plain_member(client: Client) -> User:
    user = User.objects.create_user(username="brandmember", email="brandmember@x.com", password="p")
    member = user.member
    member.fog_role = Member.FogRole.MEMBER
    if not member.full_legal_name:
        member.full_legal_name = "Brand Member"
    member.save()
    client.login(username="brandmember", password="p")
    return user


def _settings_post(**overrides: str) -> dict[str, str]:
    """A complete, valid Site-Settings POST (all tabs share one form in the DOM)."""
    data = {
        "org_name": "Past Lives Makerspace",
        "registration_mode": SiteConfiguration.RegistrationMode.INVITE_ONLY,
        "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
        "signage_default_slide_seconds": "12",
        "signage_event_days_ahead": "30",
        "submitted_tab": "brand",
        "feeds-TOTAL_FORMS": "0",
        "feeds-INITIAL_FORMS": "0",
        "feeds-MIN_NUM_FORMS": "0",
        "feeds-MAX_NUM_FORMS": "1000",
    }
    data.update(overrides)
    return data


def describe_brand_fields_on_the_form():
    def it_declares_all_seven_brand_fields():
        assert "org_name" in SiteSettingsForm.Meta.fields
        assert "org_short_name" in SiteSettingsForm.Meta.fields
        assert "org_legal_name" in SiteSettingsForm.Meta.fields
        assert "org_logo" in SiteSettingsForm.Meta.fields
        assert "org_primary_color" in SiteSettingsForm.Meta.fields
        assert "org_support_email" in SiteSettingsForm.Meta.fields
        assert "org_website_url" in SiteSettingsForm.Meta.fields

    def it_renders_the_color_field_through_the_color_picker(client: Client):
        _superuser(client)
        content = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert "pl-color-picker" in content

    def it_requires_the_organization_name():
        config = SiteConfiguration.load()
        form = SiteSettingsForm(_settings_post(org_name=""), instance=config)
        assert not form.is_valid()
        assert "org_name" in form.errors

    def it_allows_every_other_brand_field_to_be_blank():
        config = SiteConfiguration.load()
        form = SiteSettingsForm(
            _settings_post(
                org_short_name="",
                org_legal_name="",
                org_primary_color="",
                org_support_email="",
                org_website_url="",
            ),
            instance=config,
        )
        assert form.is_valid(), form.errors


def describe_brand_tab_render():
    def it_shows_the_brand_tab_button(client: Client):
        _superuser(client)
        content = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert "@click=\"tab = 'brand'\"" in content

    def it_renders_each_brand_input_exactly_once(client: Client):
        _superuser(client)
        content = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert content.count('name="org_name"') == 1
        assert content.count('name="org_short_name"') == 1
        assert content.count('name="org_legal_name"') == 1
        assert content.count('name="org_logo"') == 1
        assert content.count('name="org_primary_color"') == 1
        assert content.count('name="org_support_email"') == 1
        assert content.count('name="org_website_url"') == 1

    def it_makes_the_settings_form_multipart(client: Client):
        _superuser(client)
        content = client.get(reverse("hub_admin_site_settings")).content.decode()
        start = content.index('<form method="post" id="site-settings-form"')
        form_tag_end = content.index(">", start)
        assert 'enctype="multipart/form-data"' in content[start:form_tag_end]

    def it_renders_the_logo_upload_zone(client: Client):
        _superuser(client)
        content = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert "cls-image-upload-zone" in content

    def it_keeps_the_save_button_inside_the_settings_form(client: Client):
        # Guards §7.4: the logo's delete confirm modal must be a SIBLING of
        # #site-settings-form, not nested inside it (a teleported <form> inside would
        # break the "no nested <form>" invariant the settings form depends on).
        _superuser(client)
        config = SiteConfiguration.load()
        config.org_logo = SimpleUploadedFile("logo.png", _PNG, content_type="image/png")
        config.save()

        html = client.get(reverse("hub_admin_site_settings")).content.decode()
        start = html.index('<form method="post" id="site-settings-form"')
        main_form = html[start : html.index("</form>", start)]
        assert "<form" not in main_form[1:], "no nested <form> inside the settings form"
        assert "Save settings" in main_form, "Save button must be inside the settings form"


def describe_brand_save():
    def it_round_trips_every_brand_field_onto_the_singleton(client: Client):
        _superuser(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(
                org_name="Fletcher Test Space",
                org_short_name="Fletcher",
                org_legal_name="Fletcher Test Space LLC",
                org_primary_color="#123456",
                org_support_email="help@fletcher.test",
                org_website_url="https://fletcher.test",
            ),
        )
        assert response.status_code == 302
        config = SiteConfiguration.load()
        assert config.org_name == "Fletcher Test Space"
        assert config.org_short_name == "Fletcher"
        assert config.org_legal_name == "Fletcher Test Space LLC"
        assert config.org_primary_color == "#123456"
        assert config.org_support_email == "help@fletcher.test"
        assert config.org_website_url == "https://fletcher.test"

    def it_redirects_back_to_the_brand_tab(client: Client):
        _superuser(client)
        response = client.post(reverse("hub_admin_site_settings"), _settings_post())
        assert response.status_code == 302
        assert "tab=brand" in response["Location"]

    def it_rejects_a_malformed_primary_color(client: Client):
        _superuser(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(org_primary_color="not-a-color"),
        )
        assert response.status_code == 200  # invalid → re-render with errors, not a redirect

    def it_saves_an_uploaded_logo(client: Client):
        _superuser(client)
        upload = SimpleUploadedFile("logo.png", _PNG, content_type="image/png")
        response = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(org_logo=upload),
        )
        assert response.status_code == 302
        config = SiteConfiguration.load()
        assert bool(config.org_logo) is True


def describe_brand_logo_delete():
    def it_clears_the_logo_and_redirects_to_the_brand_tab(client: Client):
        _superuser(client)
        config = SiteConfiguration.load()
        config.org_logo = SimpleUploadedFile("logo.png", _PNG, content_type="image/png")
        config.save()

        response = client.post(reverse("hub_admin_brand_logo_delete"))
        assert response.status_code == 302
        assert "tab=brand" in response["Location"]
        assert bool(SiteConfiguration.load().org_logo) is False

    def it_is_a_no_op_when_no_logo_is_set(client: Client):
        _superuser(client)
        response = client.post(reverse("hub_admin_brand_logo_delete"))
        assert response.status_code == 302
        assert bool(SiteConfiguration.load().org_logo) is False

    def it_rejects_a_get(client: Client):
        _superuser(client)
        response = client.get(reverse("hub_admin_brand_logo_delete"))
        assert response.status_code == 405

    def it_forbids_a_plain_member(client: Client):
        _plain_member(client)
        response = client.post(reverse("hub_admin_brand_logo_delete"))
        assert response.status_code == 403
