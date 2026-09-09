from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from classes.factories import CategoryFactory, ClassOfferingFactory

LEGACY_URL = "https://classes.pastlives.space/sites/default/files/glen.jpg"


def _image_file(name: str = "cat.png") -> SimpleUploadedFile:
    # Minimal PNG signature, enough for ImageField without a validating PIL pass.
    return SimpleUploadedFile(name, BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).getvalue(), content_type="image/png")


def describe_ClassOffering():
    def describe_hero_image_url():
        def it_is_the_uploaded_file(db):
            offering = ClassOfferingFactory()
            assert offering.hero_image_url == offering.image.url

        def it_is_the_proxy_for_a_photo_imported_from_the_legacy_site(db):
            offering = ClassOfferingFactory(image="", legacy_image_url=LEGACY_URL)
            assert offering.hero_image_url == (
                f"{reverse('classes:legacy_image')}"
                "?url=https%3A%2F%2Fclasses.pastlives.space%2Fsites%2Fdefault%2Ffiles%2Fglen.jpg"
            )

        def it_prefers_the_upload_when_both_exist(db):
            offering = ClassOfferingFactory(legacy_image_url=LEGACY_URL)
            assert offering.hero_image_url == offering.image.url

        def it_is_empty_with_no_photo_of_its_own_even_when_the_category_has_one(db):
            category = CategoryFactory(hero_image=_image_file())
            offering = ClassOfferingFactory(image="", category=category)
            assert category.hero_image
            assert offering.hero_image_url == ""

    def describe_has_hero_photo():
        def it_is_true_for_an_upload(db):
            assert ClassOfferingFactory().has_hero_photo is True

        def it_is_true_for_an_imported_photo(db):
            assert ClassOfferingFactory(image="", legacy_image_url=LEGACY_URL).has_hero_photo is True

        def it_is_false_with_neither(db):
            assert ClassOfferingFactory(image="").has_hero_photo is False

    def describe_legacy_fields():
        def it_has_legacy_cms_id_defaulting_to_empty(db):
            offering = ClassOfferingFactory()
            assert offering.legacy_cms_id == ""

        def it_has_legacy_image_url_defaulting_to_empty(db):
            offering = ClassOfferingFactory()
            assert offering.legacy_image_url == ""

        def it_allows_instructor_to_be_null(db):
            offering = ClassOfferingFactory(instructor=None)
            offering.refresh_from_db()
            assert offering.instructor is None

        def it_persists_legacy_cms_id(db):
            offering = ClassOfferingFactory(legacy_cms_id="node-abc-123")
            offering.refresh_from_db()
            assert offering.legacy_cms_id == "node-abc-123"

    def describe_legacy_public_url():
        def it_points_at_the_drupal_class_page(db):
            offering = ClassOfferingFactory(legacy_cms_id="node-abc-123", slug="sewing-pattern")
            assert offering.legacy_public_url == "https://classes.pastlives.space/class/sewing-pattern"

        def it_is_empty_for_a_locally_authored_offering(db):
            offering = ClassOfferingFactory(slug="sewing-pattern")
            assert offering.legacy_public_url == ""

        def it_strips_the_collision_suffix_to_recover_the_drupal_alias(db):
            offering = ClassOfferingFactory(legacy_cms_id="node-abc-123", slug="sewing-pattern-legacy")
            assert offering.legacy_public_url == "https://classes.pastlives.space/class/sewing-pattern"
