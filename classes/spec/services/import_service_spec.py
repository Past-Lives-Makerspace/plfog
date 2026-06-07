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
            "field_dates": dates or [
                {"value": "2026-08-01T10:00:00+00:00", "end_value": "2026-08-01T12:00:00+00:00"}
            ],
            "body": {"processed": "<p>Great class</p>"},
            "path": {"alias": path_alias},
            "metatag": [
                {"attributes": {"property": "og:image", "content": image_url}}
            ],
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

    def it_does_not_overwrite_legacy_image_url_when_real_image_exists(db):
        from classes.import_service import sync_legacy_cms

        category = Category.objects.create(name="Workshop", slug="workshop")
        # Pre-set legacy_image_url to simulate a previous sync (real image is also present)
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

        # Sync with a NEW image URL from the API — the guard should block the update
        resp = _make_mock_resp(_page([_class_item(image_url="https://classes.pastlives.space/sites/default/files/new.jpg")]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering.refresh_from_db()
        # Real image present — legacy_image_url must not be overwritten with the new API URL
        assert offering.legacy_image_url == "https://classes.pastlives.space/sites/default/files/old.jpg"

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

        instructor = InstructorFactory(display_name="Billy")

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

        InstructorFactory(display_name="Billy")
        ash = InstructorFactory(display_name="Ash")

        resp = _make_mock_resp(_page([_class_item(title="Forging with Billy")]))
        with patch("urllib.request.urlopen", return_value=resp):
            sync_legacy_cms()

        offering = ClassOffering.objects.get(legacy_cms_id="uuid-1")
        offering.instructor = ash
        offering.save(update_fields=["instructor"])

        with patch("urllib.request.urlopen", return_value=_make_mock_resp(_page([_class_item(title="Forging with Billy")]))):
            sync_legacy_cms()

        offering.refresh_from_db()
        assert offering.instructor == ash

    def it_does_not_link_when_no_with_pattern_in_title(db):
        from classes.import_service import sync_legacy_cms

        InstructorFactory(display_name="Billy")

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
