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
