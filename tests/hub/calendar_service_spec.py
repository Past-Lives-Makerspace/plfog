"""BDD specs for hub.calendar_service — sync_local_class_events and sync_all_sources."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from core.models import SiteConfiguration
from membership.models import CalendarEvent

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# sync_local_class_events — the sole source of class events on the calendar
# ---------------------------------------------------------------------------


def _published_offering(**kwargs: object) -> ClassOffering:
    return ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, **kwargs)


def _future_session(offering: ClassOffering, days: int = 5) -> object:
    start = timezone.now() + timedelta(days=days)
    return ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))


def describe_sync_local_class_events():
    def it_creates_a_calendar_event_per_upcoming_published_session():
        from hub.calendar_service import sync_local_class_events

        offering = _published_offering(title="Welding 101")
        session = _future_session(offering)

        count = sync_local_class_events()

        assert count == 1
        event = CalendarEvent.objects.get(source="classes", uid=f"local-class-{session.pk}")
        assert event.title == "Welding 101"
        assert event.start_dt == session.starts_at

    def it_links_to_the_local_class_page_not_the_legacy_site():
        from hub.calendar_service import sync_local_class_events

        offering = _published_offering(slug="welding-101")
        _future_session(offering)

        sync_local_class_events()

        event = CalendarEvent.objects.get(source="classes")
        assert event.url == "/classes/welding-101/"
        assert "pastlives.space" not in event.url

    def it_skips_draft_private_and_past_sessions():
        from hub.calendar_service import sync_local_class_events

        _future_session(ClassOfferingFactory(status=ClassOffering.Status.DRAFT))
        _future_session(_published_offering(is_private=True))
        past_offering = _published_offering()
        past = timezone.now() - timedelta(days=2)
        ClassSessionFactory(class_offering=past_offering, starts_at=past, ends_at=past + timedelta(hours=1))

        count = sync_local_class_events()

        assert count == 0
        assert CalendarEvent.objects.filter(source="classes").count() == 0

    def it_purges_leftover_legacy_drupal_calendar_events():
        from hub.calendar_service import sync_local_class_events

        CalendarEvent.objects.create(
            guild=None,
            uid="classes-node-123-0",
            source="classes",
            title="Legacy Welding",
            url="https://classes.pastlives.space/classes/welding-101",
            start_dt=timezone.now() + timedelta(days=3),
            end_dt=timezone.now() + timedelta(days=3, hours=2),
            fetched_at=timezone.now(),
        )

        sync_local_class_events()

        assert not CalendarEvent.objects.filter(uid="classes-node-123-0").exists()

    def it_purges_stale_local_events_when_a_session_is_removed():
        from hub.calendar_service import sync_local_class_events

        offering = _published_offering()
        session = _future_session(offering)
        sync_local_class_events()
        assert CalendarEvent.objects.filter(uid=f"local-class-{session.pk}").exists()

        session.delete()
        sync_local_class_events()

        assert not CalendarEvent.objects.filter(uid=f"local-class-{session.pk}").exists()

    def it_updates_an_existing_event_on_resync():
        from hub.calendar_service import sync_local_class_events

        offering = _published_offering(title="Original")
        session = _future_session(offering)
        sync_local_class_events()

        offering.title = "Updated"
        offering.save()
        sync_local_class_events()

        event = CalendarEvent.objects.get(uid=f"local-class-{session.pk}")
        assert event.title == "Updated"
        assert CalendarEvent.objects.filter(source="classes").count() == 1

    def it_sets_classes_last_synced_at():
        from hub.calendar_service import sync_local_class_events

        config = SiteConfiguration.load()
        config.classes_last_synced_at = None
        config.save()

        sync_local_class_events()

        config.refresh_from_db()
        assert config.classes_last_synced_at is not None


# ---------------------------------------------------------------------------
# sync_all_sources — legacy-CMS catalog importer branch
# ---------------------------------------------------------------------------


def describe_sync_all_sources_legacy_cms():
    def it_calls_sync_legacy_cms_when_enabled():
        from hub.calendar_service import sync_all_sources

        config = SiteConfiguration.load()
        config.legacy_cms_sync_enabled = True
        config.save()

        with patch("classes.import_service.sync_legacy_cms") as mock_sync:
            errors = sync_all_sources()

        mock_sync.assert_called_once()
        assert errors == []

    def it_skips_sync_legacy_cms_when_disabled():
        from hub.calendar_service import sync_all_sources

        config = SiteConfiguration.load()
        config.legacy_cms_sync_enabled = False
        config.save()

        with patch("classes.import_service.sync_legacy_cms") as mock_sync:
            sync_all_sources()

        mock_sync.assert_not_called()

    def it_captures_exceptions_from_legacy_cms_sync():
        from hub.calendar_service import sync_all_sources

        config = SiteConfiguration.load()
        config.legacy_cms_sync_enabled = True
        config.save()

        with patch("classes.import_service.sync_legacy_cms", side_effect=RuntimeError("drupal down")):
            errors = sync_all_sources()

        assert any("legacy CMS" in e and "drupal down" in e for e in errors)
