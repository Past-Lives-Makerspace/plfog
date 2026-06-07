"""BDD specs for hub.calendar_service — _fetch_json, sync_classes_calendar, refresh_stale_sources."""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from core.models import SiteConfiguration
from membership.models import CalendarEvent
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_urlopen_json(payload: dict) -> object:
    """Return a side_effect for urllib.request.urlopen that returns JSON bytes."""

    def _fake(req, **kwargs):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(payload).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        return mock_response

    return _fake


def _make_fake_urlopen(ical_bytes: bytes) -> object:
    def _fake(url, **kwargs):
        mock_response = MagicMock()
        mock_response.read.return_value = ical_bytes
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        return mock_response

    return _fake


# ---------------------------------------------------------------------------
# _fetch_json
# ---------------------------------------------------------------------------


def describe__fetch_json():
    def it_returns_parsed_json_from_url():
        from hub.calendar_service import _fetch_json

        payload = {"data": [{"id": "abc", "attributes": {"title": "My Class"}}]}
        with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_make_urlopen_json(payload)):
            result = _fetch_json("https://example.com/api/classes")
        assert result == payload

    def it_sends_accept_header():
        from hub.calendar_service import _fetch_json

        captured_requests = []

        def _fake(req, **kwargs):
            captured_requests.append(req)
            mock_response = MagicMock()
            mock_response.read.return_value = b'{"data": []}'
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            return mock_response

        with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_fake):
            _fetch_json("https://example.com/api")
        assert len(captured_requests) == 1
        assert captured_requests[0].get_header("Accept") == "application/vnd.api+json"


# ---------------------------------------------------------------------------
# sync_classes_calendar
# ---------------------------------------------------------------------------

# Minimal JSON:API response for one class with two sessions
_CLASSES_API_RESPONSE = {
    "data": [
        {
            "id": "node-123",
            "attributes": {
                "title": "Intro to Welding",
                "field_dates": [
                    {
                        "value": "2026-06-01T10:00:00",
                        "end_value": "2026-06-01T12:00:00",
                    },
                    {
                        "value": "2026-06-08T10:00:00",
                        "end_value": "2026-06-08T12:00:00",
                    },
                ],
                "path": {"alias": "/classes/welding-101"},
                "body": {"value": "<p>Learn to weld.</p>", "summary": ""},
            },
        }
    ],
    "links": {},
}

# Response with two pages (first page links to second)
_CLASSES_PAGE1 = {
    "data": [
        {
            "id": "node-page1",
            "attributes": {
                "title": "Page 1 Class",
                "field_dates": [{"value": "2026-07-01T10:00:00", "end_value": "2026-07-01T11:00:00"}],
                "path": {"alias": "/classes/page1"},
                "body": {},
            },
        }
    ],
    "links": {"next": {"href": "https://classes.pastlives.space/jsonapi/node/class?page=2"}},
}

_CLASSES_PAGE2 = {
    "data": [
        {
            "id": "node-page2",
            "attributes": {
                "title": "Page 2 Class",
                "field_dates": [{"value": "2026-07-02T10:00:00", "end_value": "2026-07-02T11:00:00"}],
                "path": {},
                "body": {"summary": "Short description"},
            },
        }
    ],
    "links": {},
}

# Response with a class that has no field_dates (should be skipped)
_CLASSES_NO_DATES = {
    "data": [
        {
            "id": "node-nodates",
            "attributes": {
                "title": "No Dates Class",
                "field_dates": [],
                "path": {},
                "body": {},
            },
        }
    ],
    "links": {},
}

# Response with a session that has no start_str (should be skipped)
_CLASSES_NO_START = {
    "data": [
        {
            "id": "node-nostart",
            "attributes": {
                "title": "No Start Class",
                "field_dates": [{"value": None, "end_value": None}],
                "path": {},
                "body": {},
            },
        }
    ],
    "links": {},
}


def describe_sync_classes_calendar():
    def it_returns_zero_when_sync_classes_disabled():
        from hub.calendar_service import sync_classes_calendar

        config = SiteConfiguration.load()
        config.sync_classes_enabled = False
        config.save()
        count = sync_classes_calendar()
        assert count == 0

    def it_creates_calendar_events_for_each_session():
        from hub.calendar_service import sync_classes_calendar

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.save()

        with patch("hub.calendar_service._fetch_json", return_value=_CLASSES_API_RESPONSE):
            count = sync_classes_calendar()

        assert count == 2
        events = CalendarEvent.objects.filter(source="classes")
        assert events.count() == 2
        titles = set(events.values_list("title", flat=True))
        assert titles == {"Intro to Welding"}

    def it_sets_event_url_from_path_alias():
        from hub.calendar_service import sync_classes_calendar

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.save()

        with patch("hub.calendar_service._fetch_json", return_value=_CLASSES_API_RESPONSE):
            sync_classes_calendar()

        event = CalendarEvent.objects.filter(source="classes", uid="classes-node-123-0").first()
        assert event is not None
        assert event.url == "https://classes.pastlives.space/classes/welding-101"

    def it_strips_html_from_body_description():
        from hub.calendar_service import sync_classes_calendar

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.save()

        with patch("hub.calendar_service._fetch_json", return_value=_CLASSES_API_RESPONSE):
            sync_classes_calendar()

        event = CalendarEvent.objects.filter(source="classes", uid="classes-node-123-0").first()
        assert event is not None
        assert "<p>" not in event.description
        assert "Learn to weld." in event.description

    def it_skips_items_with_no_field_dates():
        from hub.calendar_service import sync_classes_calendar

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.save()

        with patch("hub.calendar_service._fetch_json", return_value=_CLASSES_NO_DATES):
            count = sync_classes_calendar()
        assert count == 0
        assert CalendarEvent.objects.filter(source="classes").count() == 0

    def it_skips_sessions_with_no_start_str():
        from hub.calendar_service import sync_classes_calendar

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.save()

        with patch("hub.calendar_service._fetch_json", return_value=_CLASSES_NO_START):
            count = sync_classes_calendar()
        assert count == 0

    def it_paginates_through_multiple_pages():
        from hub.calendar_service import sync_classes_calendar

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.save()

        pages = [_CLASSES_PAGE1, _CLASSES_PAGE2]
        call_count = 0

        def _fake_fetch(url: str) -> dict:
            nonlocal call_count
            result = pages[call_count]
            call_count += 1
            return result

        with patch("hub.calendar_service._fetch_json", side_effect=_fake_fetch):
            count = sync_classes_calendar()

        assert count == 2
        assert CalendarEvent.objects.filter(source="classes").count() == 2

    def it_removes_old_records_with_a_guild_before_upsert():
        from hub.calendar_service import sync_classes_calendar

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.save()

        # Create an old record with the same UID but a guild (pre-migration data)
        guild = GuildFactory()
        CalendarEvent.objects.create(
            guild=guild,
            uid="classes-node-123-0",
            source="classes",
            title="Old Version",
            start_dt=timezone.now(),
            end_dt=timezone.now() + timedelta(hours=1),
            fetched_at=timezone.now(),
        )

        with patch("hub.calendar_service._fetch_json", return_value=_CLASSES_API_RESPONSE):
            sync_classes_calendar()

        # The old record with a guild should be gone
        assert not CalendarEvent.objects.filter(uid="classes-node-123-0", guild__isnull=False).exists()
        # The new record with guild=None should exist
        assert CalendarEvent.objects.filter(uid="classes-node-123-0", guild__isnull=True).exists()

    def it_updates_classes_last_synced_at():
        from hub.calendar_service import sync_classes_calendar

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.classes_last_synced_at = None
        config.save()

        with patch("hub.calendar_service._fetch_json", return_value=_CLASSES_API_RESPONSE):
            sync_classes_calendar()

        config.refresh_from_db()
        assert config.classes_last_synced_at is not None

    def it_uses_empty_url_when_path_alias_is_missing():
        from hub.calendar_service import sync_classes_calendar

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.save()

        with patch("hub.calendar_service._fetch_json", return_value=_CLASSES_PAGE2):
            sync_classes_calendar()

        event = CalendarEvent.objects.filter(source="classes", uid="classes-node-page2-0").first()
        assert event is not None
        assert event.url == ""

    def it_uses_summary_as_fallback_description():
        from hub.calendar_service import sync_classes_calendar

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.save()

        # _CLASSES_PAGE2 has body: {"summary": "Short description"} and no "value"
        with patch("hub.calendar_service._fetch_json", return_value=_CLASSES_PAGE2):
            sync_classes_calendar()

        event = CalendarEvent.objects.filter(source="classes", uid="classes-node-page2-0").first()
        assert event is not None
        assert event.description == "Short description"

    def it_defaults_end_value_to_start_when_missing():
        from hub.calendar_service import sync_classes_calendar

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.save()

        response = {
            "data": [
                {
                    "id": "node-noend",
                    "attributes": {
                        "title": "No End Class",
                        "field_dates": [{"value": "2026-08-01T10:00:00"}],
                        "path": {},
                        "body": {},
                    },
                }
            ],
            "links": {},
        }

        with patch("hub.calendar_service._fetch_json", return_value=response):
            count = sync_classes_calendar()

        assert count == 1
        event = CalendarEvent.objects.get(source="classes", uid="classes-node-noend-0")
        assert event.start_dt == event.end_dt


# ---------------------------------------------------------------------------
# refresh_stale_sources — classes branch (lines 239-245)
# ---------------------------------------------------------------------------


def describe_refresh_stale_sources_classes():
    def it_syncs_classes_when_stale():
        from hub.calendar_service import refresh_stale_sources

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.classes_last_synced_at = timezone.now() - timedelta(seconds=1000)
        config.save()

        with patch("hub.calendar_service.sync_classes_calendar", return_value=3) as mock_sync:
            refresh_stale_sources(max_age_seconds=900)

        mock_sync.assert_called_once()

    def it_syncs_classes_when_never_synced():
        from hub.calendar_service import refresh_stale_sources

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.classes_last_synced_at = None
        config.save()

        with patch("hub.calendar_service.sync_classes_calendar", return_value=1) as mock_sync:
            refresh_stale_sources(max_age_seconds=900)

        mock_sync.assert_called_once()

    def it_skips_classes_when_recently_synced():
        from hub.calendar_service import refresh_stale_sources

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.classes_last_synced_at = timezone.now()
        config.save()

        with patch("hub.calendar_service.sync_classes_calendar") as mock_sync:
            refresh_stale_sources(max_age_seconds=900)

        mock_sync.assert_not_called()

    def it_swallows_exceptions_from_classes_sync():
        from hub.calendar_service import refresh_stale_sources

        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.classes_last_synced_at = None
        config.save()

        with patch("hub.calendar_service.sync_classes_calendar", side_effect=RuntimeError("timeout")):
            # Should not raise — exception is swallowed
            refresh_stale_sources(max_age_seconds=900)

    def it_skips_classes_when_sync_disabled():
        from hub.calendar_service import refresh_stale_sources

        config = SiteConfiguration.load()
        config.sync_classes_enabled = False
        config.classes_last_synced_at = None
        config.save()

        with patch("hub.calendar_service.sync_classes_calendar") as mock_sync:
            refresh_stale_sources(max_age_seconds=900)

        mock_sync.assert_not_called()


# ---------------------------------------------------------------------------
# refresh_stale_sources — legacy_cms branch
# ---------------------------------------------------------------------------


def describe_refresh_stale_sources_legacy_cms():
    def it_calls_sync_legacy_cms_when_enabled_and_stale():
        from hub.calendar_service import refresh_stale_sources

        config = SiteConfiguration.load()
        config.legacy_cms_sync_enabled = True
        config.legacy_cms_last_synced_at = None
        config.save()

        with patch("classes.import_service.sync_legacy_cms") as mock_sync:
            refresh_stale_sources(max_age_seconds=900)

        mock_sync.assert_called_once()

    def it_skips_sync_legacy_cms_when_disabled():
        from hub.calendar_service import refresh_stale_sources

        config = SiteConfiguration.load()
        config.legacy_cms_sync_enabled = False
        config.legacy_cms_last_synced_at = None
        config.save()

        with patch("classes.import_service.sync_legacy_cms") as mock_sync:
            refresh_stale_sources(max_age_seconds=900)

        mock_sync.assert_not_called()
