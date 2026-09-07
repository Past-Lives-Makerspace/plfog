from classes.factories import ClassOfferingFactory


def describe_ClassOffering():
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
