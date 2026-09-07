"""BDD specs for the SiteConfiguration brand block (PLAT-1).

Covers the seven brand fields' Past Lives defaults, ``org_primary_color``'s hex
validation, and the logo's replace/delete storage behavior via ``delete_orphan_on_replace``
wired into ``SiteConfiguration.save()``.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def describe_SiteConfiguration_brand_defaults():
    def it_defaults_to_the_past_lives_identity():
        config = SiteConfiguration.load()
        assert config.org_name == "Past Lives Makerspace"
        assert config.org_short_name == "Past Lives"
        assert config.org_legal_name == "Past Lives Makerspace LLC"
        assert config.org_primary_color == "#092E4C"
        assert config.org_support_email == "info@pastlives.space"
        assert config.org_website_url == "https://pastlives.space"

    def it_starts_with_no_uploaded_logo():
        config = SiteConfiguration.load()
        assert bool(config.org_logo) is False

    def it_survives_load_on_a_fresh_database():
        # A data migration (0003) already seeds pk=1 on a fresh database, so ``load()``
        # here exercises the get_or_create's "get" branch — it must still return the
        # singleton with the brand defaults intact, not blow up or create a second row.
        config = SiteConfiguration.load()
        assert config.pk == 1
        assert SiteConfiguration.objects.count() == 1
        assert config.org_name == "Past Lives Makerspace"


def describe_org_primary_color_validation():
    def it_accepts_a_six_digit_hex():
        config = SiteConfiguration.load()
        config.org_primary_color = "#ABCDEF"
        config.full_clean()
        config.org_primary_color = "#abcdef"
        config.full_clean()

    def it_accepts_a_blank_value():
        config = SiteConfiguration.load()
        config.org_primary_color = ""
        config.full_clean()

    def it_rejects_a_three_digit_shorthand():
        config = SiteConfiguration.load()
        config.org_primary_color = "#FFF"
        with pytest.raises(ValidationError):
            config.full_clean()

    def it_rejects_a_value_without_a_hash():
        config = SiteConfiguration.load()
        config.org_primary_color = "092E4C"
        with pytest.raises(ValidationError):
            config.full_clean()

    def it_rejects_a_non_hex_character():
        config = SiteConfiguration.load()
        config.org_primary_color = "#GGGGGG"
        with pytest.raises(ValidationError):
            config.full_clean()


def describe_org_logo_replacement():
    def it_deletes_the_previous_file_when_a_new_one_is_saved():
        config = SiteConfiguration.load()
        config.org_logo = SimpleUploadedFile("first.png", _PNG, content_type="image/png")
        config.save()
        old_name = config.org_logo.name
        assert default_storage.exists(old_name)

        config.org_logo = SimpleUploadedFile("second.png", _PNG, content_type="image/png")
        config.save()

        assert not default_storage.exists(old_name)
        assert default_storage.exists(config.org_logo.name)

    def it_leaves_storage_alone_when_the_logo_is_unchanged():
        config = SiteConfiguration.load()
        config.org_logo = SimpleUploadedFile("stable.png", _PNG, content_type="image/png")
        config.save()
        stored_name = config.org_logo.name

        config.org_name = "Updated Name"
        config.save()

        assert config.org_logo.name == stored_name
        assert default_storage.exists(stored_name)
