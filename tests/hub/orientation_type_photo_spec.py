"""BDD specs for an orientation type's own photo (#502 part 2).

The field on both editors (guild settings and the equipment manage page), the guild
autosave path, the equipment formset, the delete endpoint and its gates, and the model's
card image fallback and orphan cleanup.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from membership.models import OrientationType
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
    tiny_png_bytes,
)

pytestmark = pytest.mark.django_db

AUTOSAVE = {"HTTP_X_AUTOSAVE": "1"}
GUILD_TOOLTIP = (
    "Wide photo, about 1600x900, JPG or PNG. Shown on this orientation's card. Leave empty to use the guild banner."
)
EQUIPMENT_TOOLTIP = (
    "Wide photo, about 1600x900, JPG or PNG. Shown on this orientation's card. Leave empty to use the equipment photo."
)


def _png(name: str = "card.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, tiny_png_bytes(), "image/png")


def _login(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    client.login(username=username, password="pass")
    return user


def _type_row(orientation_type: OrientationType | None, **fields: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": str(orientation_type.pk) if orientation_type else "",
        "name": orientation_type.name if orientation_type else "New Type",
        "duration_minutes": "60",
        "default_seats": "4",
        "sort_order": "0",
        "is_active": "on",
    }
    row.update(fields)
    return row


def _formset(rows: list[dict[str, object]], *, initial: int) -> dict[str, object]:
    data: dict[str, object] = {
        "otypes-TOTAL_FORMS": str(len(rows)),
        "otypes-INITIAL_FORMS": str(initial),
        "otypes-MIN_NUM_FORMS": "0",
        "otypes-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        for key, value in row.items():
            data[f"otypes-{index}-{key}"] = value
    return data


def describe_the_guild_editor():
    def it_renders_the_photo_field_with_its_tooltip_and_a_delete_control_on_a_saved_row(client: Client):
        user = _login(client, "ph_guild_field")
        guild = GuildFactory(guild_lead=user.member)
        with_photo = OrientationTypeFactory(guild=guild, name="Has Photo", photo=_png())
        without = OrientationTypeFactory(guild=guild, name="No Photo")
        content = client.get(reverse("hub_guild_orientations", args=[guild.pk])).content.decode()
        assert 'name="otypes-0-photo"' in content
        assert GUILD_TOOLTIP in content
        assert with_photo.photo.url in content
        assert f"'delete-otype-photo-{with_photo.pk}'" in content
        assert reverse("hub_orientation_type_photo_delete", args=[with_photo.pk]) in content
        assert f"delete-otype-photo-{without.pk}" not in content
        assert "The card goes back to the guild banner." in content

    def it_renders_the_field_in_the_add_row_template_without_a_delete(client: Client):
        user = _login(client, "ph_guild_template")
        guild = GuildFactory(guild_lead=user.member)
        content = client.get(reverse("hub_guild_orientations", args=[guild.pk])).content.decode()
        template = content[content.index('<template id="otype-empty-template"') :]
        template = template[: template.index("</template>")]
        assert 'name="otypes-__prefix__-photo"' in template
        assert 'id="image-upload-zone-id_otypes-__prefix__-photo"' in template
        assert "delete-otype-photo-" not in template

    def it_posts_the_types_form_as_multipart(client: Client):
        user = _login(client, "ph_guild_multipart")
        guild = GuildFactory(guild_lead=user.member)
        content = client.get(reverse("hub_guild_orientations", args=[guild.pk])).content.decode()
        action = reverse("hub_guild_orientation_types_save", args=[guild.pk])
        assert f'action="{action}" enctype="multipart/form-data"' in content

    def it_saves_a_photo_through_autosave_and_the_card_uses_it(client: Client):
        user = _login(client, "ph_guild_save")
        guild = GuildFactory(guild_lead=user.member)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        orientation_type = OrientationTypeFactory(guild=guild, name="Photo Type")
        data = _formset([_type_row(orientation_type, name="Photo Type", photo=_png("upload.png"))], initial=1)
        with patch("membership.orientations.generate_slots"):
            response = client.post(reverse("hub_guild_orientation_types_save", args=[guild.pk]), data, **AUTOSAVE)
        assert response.status_code == 200
        assert response.json()["rows"] == {"otypes": [orientation_type.pk]}
        orientation_type.refresh_from_db()
        assert orientation_type.photo.name.startswith("orientations/photos/")
        page = client.get(reverse("hub_orientations")).content.decode()
        assert orientation_type.photo.url in page


def describe_the_equipment_editor():
    def it_renders_the_photo_field_with_its_tooltip_and_delete_control(client: Client):
        user = _login(client, "ph_item_field")
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        orientation_type = OrientationTypeFactory(guild=None, equipment=equipment, name="Item Photo", photo=_png())
        content = client.get(reverse("hub_equipment_manage", args=[equipment.slug])).content.decode()
        assert 'name="otypes-0-photo"' in content
        assert EQUIPMENT_TOOLTIP in content
        assert f"'delete-otype-photo-{orientation_type.pk}'" in content
        assert "The card goes back to the equipment photo." in content
        template = content[content.index('<template id="equip-otype-empty-template"') :]
        assert 'name="otypes-__prefix__-photo"' in template[: template.index("</template>")]
        action = reverse("hub_equipment_orientation_types_save", args=[equipment.slug])
        assert f'action="{action}" enctype="multipart/form-data"' in content

    def it_saves_a_photo_with_the_formset(client: Client):
        user = _login(client, "ph_item_save")
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        orientation_type = OrientationTypeFactory(guild=None, equipment=equipment, name="Item Save")
        data = _formset([_type_row(orientation_type, photo=_png("item.png"))], initial=1)
        response = client.post(reverse("hub_equipment_orientation_types_save", args=[equipment.slug]), data)
        assert response.status_code == 302
        orientation_type.refresh_from_db()
        assert orientation_type.photo.name.startswith("orientations/photos/")


def describe_the_delete_endpoint():
    def it_clears_a_guild_types_photo_for_the_lead_and_returns_to_the_orientations_page(client: Client):
        user = _login(client, "ph_del_lead")
        guild = GuildFactory(guild_lead=user.member)
        orientation_type = OrientationTypeFactory(guild=guild, name="Delete Me", photo=_png())
        stored = orientation_type.photo.name
        response = client.post(reverse("hub_orientation_type_photo_delete", args=[orientation_type.pk]))
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_guild_orientations", args=[guild.pk])
        orientation_type.refresh_from_db()
        assert not orientation_type.photo
        assert not default_storage.exists(stored)

    def it_403s_a_stranger_on_a_guild_type(client: Client):
        _login(client, "ph_del_stranger")
        orientation_type = OrientationTypeFactory(guild=GuildFactory(), name="Not Yours", photo=_png())
        response = client.post(reverse("hub_orientation_type_photo_delete", args=[orientation_type.pk]))
        assert response.status_code == 403
        orientation_type.refresh_from_db()
        assert orientation_type.photo

    def it_clears_an_equipment_types_photo_for_a_manager_and_returns_to_the_tab(client: Client):
        user = _login(client, "ph_del_mgr")
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        orientation_type = OrientationTypeFactory(guild=None, equipment=equipment, name="Item Delete", photo=_png())
        response = client.post(reverse("hub_orientation_type_photo_delete", args=[orientation_type.pk]))
        assert response["Location"] == f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation"
        orientation_type.refresh_from_db()
        assert not orientation_type.photo

    def it_403s_a_stranger_on_an_equipment_type(client: Client):
        _login(client, "ph_del_item_stranger")
        orientation_type = OrientationTypeFactory(equipment_owned=True, name="Item Not Yours", photo=_png())
        assert client.post(reverse("hub_orientation_type_photo_delete", args=[orientation_type.pk])).status_code == 403

    def it_redirects_quietly_when_there_is_no_photo(client: Client):
        user = _login(client, "ph_del_none")
        guild = GuildFactory(guild_lead=user.member)
        orientation_type = OrientationTypeFactory(guild=guild, name="Bare")
        response = client.post(reverse("hub_orientation_type_photo_delete", args=[orientation_type.pk]))
        assert response.status_code == 302

    def it_refuses_a_get(client: Client):
        user = _login(client, "ph_del_get")
        orientation_type = OrientationTypeFactory(guild=GuildFactory(guild_lead=user.member), name="Get")
        assert client.get(reverse("hub_orientation_type_photo_delete", args=[orientation_type.pk])).status_code == 405


def describe_the_model():
    def it_deletes_the_old_file_when_the_photo_is_replaced():
        orientation_type = OrientationTypeFactory(name="Replace", photo=_png("first.png"))
        first = orientation_type.photo.name
        orientation_type.photo = _png("second.png")
        orientation_type.save()
        assert not default_storage.exists(first)
        assert default_storage.exists(orientation_type.photo.name)

    def it_centres_its_own_photo_and_borrows_the_owners_crop_otherwise():
        guild = GuildFactory(banner_image=_png("banner.png"), hero_crop_x=10, hero_crop_y=90)
        orientation_type = OrientationTypeFactory(guild=guild, name="Position")
        assert orientation_type.card_image_owner == guild
        assert orientation_type.card_image_position == "10% 90%"
        orientation_type.photo = _png("own.png")
        orientation_type.save()
        assert orientation_type.card_image_owner is None
        assert orientation_type.card_image_position == "50% 50%"

    def it_has_no_card_image_with_no_picture_anywhere():
        orientation_type = OrientationTypeFactory(equipment_owned=True, name="Nothing")
        assert orientation_type.card_image is None
        assert orientation_type.card_image_position == "50% 50%"

    def it_links_its_card_on_the_orientations_page():
        orientation_type = OrientationTypeFactory(name="Anchor")
        assert orientation_type.orientations_page_path() == f"/orientations/#orientation-type-{orientation_type.pk}"
