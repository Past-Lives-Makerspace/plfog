import json
from unittest.mock import MagicMock, patch

from classes.factories import InstructorFactory
from classes.models import Category, ClassOffering, ClassSession
from core.models import SiteConfiguration


LEGACY_API_BASE = "https://classes.pastlives.space"


def _make_mock_resp(data: dict) -> MagicMock:
    mock = MagicMock()
    mock.read.return_value = json.dumps(data).encode()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    mock.headers = {}
    return mock


def _class_item(
    node_id: str = "uuid-1",
    title: str = "Intro to Welding",
    class_type: str = "workshop",
    price: str = "100.00",
    capacity: int = 8,
    status: bool = True,
    dates: list | None = None,
    image_url: str = "https://classes.pastlives.space/sites/default/files/img.jpg",
    path_alias: str = "/class/intro-to-welding",
) -> dict:
    return {
        "id": node_id,
        "attributes": {
            "title": title,
            "status": status,
            "field_class_type": class_type,
            "field_price": price,
            "field_max_students": capacity,
            "field_dates": dates or [{"value": "2026-08-01T10:00:00+00:00", "end_value": "2026-08-01T12:00:00+00:00"}],
            "body": {"processed": "<p>Great class</p>"},
            "path": {"alias": path_alias},
            "metatag": [{"attributes": {"property": "og:image", "content": image_url}}],
        },
        "relationships": {},
    }


def _page(items: list, has_next: bool = False) -> dict:
    return {
        "data": items,
        "links": {"next": {"href": f"{LEGACY_API_BASE}/next"}} if has_next else {},
    }


def describe_sync_legacy_cms():
    def it_creates_a_class_offering_from_the_api(db):
        from classes.import_service import sync_legacy_cms

        resp = _make_mock_resp(_page([_class_item()]))
        with patch("urllib.request.urlopen", return_value=resp):
            count = sync_legacy_cms()

        assert count == 1
        assert ClassOffering.objects.filter(legacy_cms_id="uuid-1").exists()

    def it_auto_creates_the_category_from_field_class_type(db):
        from classes.import_service import sync_legacy_cms

        resp = _make_mock_resp(_page([_class_item(class_type="workshop")]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        assert Category.objects.filter(name="Workshop").exists()

    def it_does_not_overwrite_category_on_re_sync(db):
        from classes.import_service import sync_legacy_cms

        resp = _make_mock_resp(_page([_class_item(class_type="workshop")]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        guild_category = Category.objects.create(name="Woodworking", slug="woodworking")
        offering.category = guild_category
        offering.save(update_fields=["category"])

        with patch("urllib.request.urlopen", return_value=_make_mock_resp(_page([_class_item(class_type="workshop")]))):
            sync_legacy_cms()

        offering.refresh_from_db()
        assert offering.category == guild_category

    def it_maps_open_studio_to_a_readable_name(db):
        from classes.import_service import sync_legacy_cms

        resp = _make_mock_resp(_page([_class_item(class_type="open_studio")]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        assert Category.objects.filter(name="Open Studio").exists()

    def it_converts_price_to_cents(db):
        from classes.import_service import sync_legacy_cms

        resp = _make_mock_resp(_page([_class_item(price="200.00")]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert offering.price_cents == 20000

    def it_stores_the_legacy_image_url(db):
        from classes.import_service import sync_legacy_cms

        resp = _make_mock_resp(_page([_class_item()]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert offering.legacy_image_url == "https://classes.pastlives.space/sites/default/files/img.jpg"

    def it_does_not_write_a_legacy_image_url_when_a_real_image_exists(db):
        from classes.import_service import sync_legacy_cms

        category = Category.objects.create(name="Workshop", slug="workshop")
        # A previous sync left a legacy URL behind, and the picture has since been
        # migrated into our own storage.
        offering = ClassOffering.objects.create(
            legacy_cms_id="uuid-1",
            title="Old",
            slug="old",
            category=category,
            price_cents=0,
            status=ClassOffering.Status.PUBLISHED,
            image="classes/images/existing.jpg",
            legacy_image_url="https://classes.pastlives.space/sites/default/files/old.jpg",
        )

        # Sync offers a NEW image URL — the local image must win outright.
        resp = _make_mock_resp(
            _page([_class_item(image_url="https://classes.pastlives.space/sites/default/files/new.jpg")])
        )
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering.refresh_from_db()
        # Not the feed's URL, and not the stale one either — a class we own the picture
        # for keeps no handle on the legacy server at all.
        assert offering.legacy_image_url == ""
        assert offering.image.name == "classes/images/existing.jpg"

    def it_creates_class_sessions_from_field_dates(db):
        from classes.import_service import sync_legacy_cms

        dates = [
            {"value": "2026-08-01T10:00:00+00:00", "end_value": "2026-08-01T12:00:00+00:00"},
            {"value": "2026-08-08T10:00:00+00:00", "end_value": "2026-08-08T12:00:00+00:00"},
        ]
        resp = _make_mock_resp(_page([_class_item(dates=dates)]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert ClassSession.objects.filter(class_offering=offering).count() == 2

    def it_replaces_sessions_on_re_sync(db):
        from classes.import_service import sync_legacy_cms

        single_date = [{"value": "2026-08-01T10:00:00+00:00", "end_value": "2026-08-01T12:00:00+00:00"}]
        two_dates = [
            {"value": "2026-08-01T10:00:00+00:00", "end_value": "2026-08-01T12:00:00+00:00"},
            {"value": "2026-08-08T10:00:00+00:00", "end_value": "2026-08-08T12:00:00+00:00"},
        ]

        resp1 = _make_mock_resp(_page([_class_item(dates=two_dates)]))
        resp2 = _make_mock_resp(_page([_class_item(dates=single_date)]))

        with patch("urllib.request.urlopen", return_value=resp1):
            sync_legacy_cms()
        with patch("urllib.request.urlopen", return_value=resp2):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert ClassSession.objects.filter(class_offering=offering).count() == 1

    def it_marks_multi_date_nodes_as_a_series(db):
        from classes.import_service import sync_legacy_cms

        dates = [
            {"value": "2026-08-01T10:00:00+00:00", "end_value": "2026-08-01T12:00:00+00:00"},
            {"value": "2026-08-08T10:00:00+00:00", "end_value": "2026-08-08T12:00:00+00:00"},
        ]
        resp = _make_mock_resp(_page([_class_item(dates=dates)]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert offering.scheduling_type == ClassOffering.SchedulingType.SERIES_PACKAGE

    def it_marks_single_date_nodes_as_a_single_session(db):
        from classes.import_service import sync_legacy_cms

        resp = _make_mock_resp(_page([_class_item()]))  # default fixture carries one date
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert offering.scheduling_type == ClassOffering.SchedulingType.SINGLE_SESSION

    def it_corrects_scheduling_type_on_re_sync(db):
        from classes.import_service import sync_legacy_cms

        two_dates = [
            {"value": "2026-08-01T10:00:00+00:00", "end_value": "2026-08-01T12:00:00+00:00"},
            {"value": "2026-08-08T10:00:00+00:00", "end_value": "2026-08-08T12:00:00+00:00"},
        ]
        single_date = [{"value": "2026-08-01T10:00:00+00:00", "end_value": "2026-08-01T12:00:00+00:00"}]

        with patch("urllib.request.urlopen", return_value=_make_mock_resp(_page([_class_item(dates=two_dates)]))):
            sync_legacy_cms()
        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert offering.scheduling_type == ClassOffering.SchedulingType.SERIES_PACKAGE

        with patch("urllib.request.urlopen", return_value=_make_mock_resp(_page([_class_item(dates=single_date)]))):
            sync_legacy_cms()
        offering.refresh_from_db()
        assert offering.scheduling_type == ClassOffering.SchedulingType.SINGLE_SESSION

    def it_handles_pagination(db):
        from classes.import_service import sync_legacy_cms

        page1 = _page([_class_item("uuid-1")], has_next=True)
        page2 = _page([_class_item("uuid-2")])

        resp1 = _make_mock_resp(page1)
        resp2 = _make_mock_resp(page2)

        with patch("urllib.request.urlopen", side_effect=[resp1, resp2]):
            count = sync_legacy_cms()

        assert count == 2
        assert ClassOffering.objects.filter(legacy_cms_id__in=["uuid-1", "uuid-2"]).count() == 2

    def it_sets_slug_from_path_alias_on_first_import(db):
        from classes.import_service import sync_legacy_cms

        resp = _make_mock_resp(_page([_class_item(path_alias="/class/intro-to-welding")]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert offering.slug == "intro-to-welding"

    def it_does_not_overwrite_slug_on_re_sync(db):
        from classes.import_service import sync_legacy_cms

        resp = _make_mock_resp(_page([_class_item()]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        offering.slug = "custom-admin-slug"
        offering.save(update_fields=["slug"])

        with patch("urllib.request.urlopen", return_value=_make_mock_resp(_page([_class_item()]))):
            sync_legacy_cms()

        offering.refresh_from_db()
        assert offering.slug == "custom-admin-slug"

    def it_archives_offerings_removed_from_the_api(db):
        from classes.import_service import sync_legacy_cms

        resp1 = _make_mock_resp(_page([_class_item("uuid-1"), _class_item("uuid-2", path_alias="/class/c2")]))
        with patch("urllib.request.urlopen", return_value=resp1):
            sync_legacy_cms()

        resp2 = _make_mock_resp(_page([_class_item("uuid-1")]))
        with patch("urllib.request.urlopen", return_value=resp2):
            sync_legacy_cms()

        removed = ClassOffering.objects.get(legacy_cms_id="uuid-2")
        assert removed.status == ClassOffering.Status.ARCHIVED

    def it_updates_legacy_cms_last_synced_at(db):
        from classes.import_service import sync_legacy_cms

        resp = _make_mock_resp(_page([_class_item()]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        config = SiteConfiguration.load()
        assert config.legacy_cms_last_synced_at is not None

    def it_links_instructor_when_title_matches_display_name(db):
        from classes.import_service import sync_legacy_cms

        instructor = InstructorFactory(full_legal_name="Billy")

        resp = _make_mock_resp(_page([_class_item(title="Blacksmithing 101 with Billy")]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert offering.instructor == instructor

    def it_does_not_link_instructor_when_name_not_found(db):
        from classes.import_service import sync_legacy_cms

        resp = _make_mock_resp(_page([_class_item(title="Blacksmithing 101 with Ash")]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert offering.instructor is None

    def it_does_not_overwrite_instructor_once_set(db):
        from classes.import_service import sync_legacy_cms

        InstructorFactory(full_legal_name="Billy")
        ash = InstructorFactory(full_legal_name="Ash")

        resp = _make_mock_resp(_page([_class_item(title="Forging with Billy")]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        offering.instructor = ash
        offering.save(update_fields=["instructor"])

        with patch(
            "urllib.request.urlopen", return_value=_make_mock_resp(_page([_class_item(title="Forging with Billy")]))
        ):
            sync_legacy_cms()

        offering.refresh_from_db()
        assert offering.instructor == ash

    def it_does_not_link_when_no_with_pattern_in_title(db):
        from classes.import_service import sync_legacy_cms

        InstructorFactory(full_legal_name="Billy")

        resp = _make_mock_resp(_page([_class_item(title="Intro to Welding")]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert offering.instructor is None

    def it_appends_legacy_suffix_when_slug_collides(db):
        from classes.import_service import sync_legacy_cms

        category = Category.objects.create(name="Workshop", slug="workshop")
        # Pre-create an offering with the slug that would be generated from path_alias
        ClassOffering.objects.create(
            title="Other",
            slug="intro-to-welding",
            category=category,
            price_cents=0,
            status=ClassOffering.Status.PUBLISHED,
        )

        resp = _make_mock_resp(_page([_class_item(path_alias="/class/intro-to-welding")]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert offering.slug == "intro-to-welding-legacy"

    def it_skips_sessions_with_missing_value_key(db):
        from classes.import_service import sync_legacy_cms

        dates = [{"end_value": "2026-08-01T12:00:00+00:00"}]  # no "value" key
        resp = _make_mock_resp(_page([_class_item(dates=dates)]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert ClassSession.objects.filter(class_offering=offering).count() == 0

    def it_skips_sessions_where_parse_datetime_returns_none(db):
        from classes.import_service import sync_legacy_cms

        dates = [{"value": "not-a-date", "end_value": "also-not-a-date"}]
        resp = _make_mock_resp(_page([_class_item(dates=dates)]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert ClassSession.objects.filter(class_offering=offering).count() == 0

    def it_groups_same_class_posted_on_different_dates(db):
        from classes.import_service import sync_legacy_cms

        items = [
            _class_item("uuid-1", title="Blacksmithing 101 with Glen 6/5/26", path_alias="/class/bs-1"),
            _class_item("uuid-2", title="Blacksmithing 101 with Glen 6/12/26", path_alias="/class/bs-2"),
        ]
        resp = _make_mock_resp(_page(items))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        keys = set(ClassOffering.objects.values_list("grouping_key", flat=True))
        assert len(keys) == 1
        assert "" not in keys

    def it_skips_items_with_no_node_id(db):
        from classes.import_service import sync_legacy_cms

        item_no_id = _class_item()
        item_no_id["id"] = ""
        resp = _make_mock_resp(_page([item_no_id]))
        with patch("urllib.request.urlopen", return_value=resp):
            count = sync_legacy_cms()

        assert count == 0
        assert ClassOffering.objects.filter(legacy_cms_id__gt="").count() == 0


def describe_sync_legacy_cms_image_ownership():
    """Once a picture is in our own storage, no future sync may take it back."""

    def _migrated_offering(image_name: str = "classes/images/deadbeef.jpg") -> ClassOffering:
        category = Category.objects.create(name="Workshop", slug="workshop")
        return ClassOffering.objects.create(
            legacy_cms_id="uuid-1",
            title="Old",
            slug="old",
            category=category,
            price_cents=0,
            status=ClassOffering.Status.PUBLISHED,
            image=image_name,
            legacy_image_url="",
        )

    def it_does_not_re_set_legacy_image_url_on_a_migrated_offering(db):
        from classes.import_service import sync_legacy_cms

        offering = _migrated_offering()

        resp = _make_mock_resp(
            _page([_class_item(image_url="https://classes.pastlives.space/sites/default/files/back.jpg")])
        )
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering.refresh_from_db()
        assert offering.legacy_image_url == ""

    def it_does_not_overwrite_a_migrated_image(db):
        from classes.import_service import sync_legacy_cms

        offering = _migrated_offering()

        resp = _make_mock_resp(
            _page([_class_item(image_url="https://classes.pastlives.space/sites/default/files/back.jpg")])
        )
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering.refresh_from_db()
        assert offering.image.name == "classes/images/deadbeef.jpg"

    def it_clears_a_stale_legacy_image_url_when_a_local_image_exists(db):
        from classes.import_service import sync_legacy_cms

        offering = _migrated_offering()
        ClassOffering.objects.filter(pk=offering.pk).update(
            legacy_image_url="https://classes.pastlives.space/sites/default/files/old.jpg"
        )

        resp = _make_mock_resp(_page([_class_item()]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering.refresh_from_db()
        assert offering.legacy_image_url == ""

    def it_still_picks_up_the_image_url_of_a_brand_new_class(db):
        from classes.import_service import sync_legacy_cms

        resp = _make_mock_resp(
            _page([_class_item(image_url="https://classes.pastlives.space/sites/default/files/new.jpg")])
        )
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        assert offering.legacy_image_url == "https://classes.pastlives.space/sites/default/files/new.jpg"


def describe_sync_legacy_cms_archive_guard():
    def it_does_not_archive_the_catalog_when_the_feed_returns_no_classes(db):
        from classes.import_service import sync_legacy_cms

        first = _make_mock_resp(_page([_class_item("uuid-1"), _class_item("uuid-2", path_alias="/class/c2")]))
        with patch("urllib.request.urlopen", return_value=first):
            sync_legacy_cms()

        # A live-but-broken Drupal: HTTP 200, valid JSON, zero classes.
        with patch("urllib.request.urlopen", return_value=_make_mock_resp(_page([]))):
            sync_legacy_cms()

        statuses = set(ClassOffering.objects.values_list("status", flat=True))
        assert statuses == {ClassOffering.Status.PUBLISHED}
