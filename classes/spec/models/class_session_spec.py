"""BDD specs for ClassSession."""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.db.utils import IntegrityError
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering, ClassSession


def describe_ClassSession():
    def it_orders_by_starts_at(db):
        offering = ClassOfferingFactory()
        later = timezone.now() + timedelta(days=2)
        earlier = timezone.now() + timedelta(days=1)
        ClassSessionFactory(class_offering=offering, starts_at=later, ends_at=later + timedelta(hours=1))
        ClassSessionFactory(class_offering=offering, starts_at=earlier, ends_at=earlier + timedelta(hours=1))
        all_sessions = list(ClassSession.objects.all())
        assert all_sessions[0].starts_at == earlier

    def it_rejects_ends_before_starts(db):
        offering = ClassOfferingFactory()
        now = timezone.now()
        with pytest.raises(IntegrityError):
            ClassSession.objects.create(class_offering=offering, starts_at=now, ends_at=now - timedelta(minutes=1))

    def it_stringifies_with_class_and_date(db):
        offering = ClassOfferingFactory(title="Pottery")
        session = ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now().replace(year=2026, month=5, day=10),
        )
        assert "Pottery" in str(session)
        assert "2026-05-10" in str(session)


def _published_offering(**kwargs):
    """A published offering; pass overrides like ``is_private`` / ``scheduling_model``."""
    return ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, **kwargs)


def _future_session(offering, days=7):
    """Attach a session ``days`` out (default a week) so it counts as upcoming."""
    start = timezone.now() + timedelta(days=days)
    return ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))


def describe_ClassSessionQuerySet():
    def describe_upcoming_public_count():
        def it_counts_a_single_future_session(db):
            _future_session(_published_offering())
            assert ClassSession.objects.upcoming_public_count() == 1

        def it_counts_each_session_of_a_series(db):
            offering = _published_offering()
            for week in range(3):
                _future_session(offering, days=7 * (week + 1))
            assert ClassSession.objects.upcoming_public_count() == 3

        def it_excludes_past_sessions(db):
            offering = _published_offering()
            _future_session(offering)
            past = timezone.now() - timedelta(days=7)
            ClassSessionFactory(class_offering=offering, starts_at=past, ends_at=past + timedelta(hours=2))
            assert ClassSession.objects.upcoming_public_count() == 1

        def it_excludes_draft_pending_and_archived_offerings(db):
            for status in (
                ClassOffering.Status.DRAFT,
                ClassOffering.Status.PENDING,
                ClassOffering.Status.ARCHIVED,
            ):
                _future_session(ClassOfferingFactory(status=status))
            assert ClassSession.objects.upcoming_public_count() == 0

        def it_excludes_private_offerings(db):
            _future_session(_published_offering(is_private=True))
            assert ClassSession.objects.upcoming_public_count() == 0

        def it_excludes_flexible_classes(db):
            _published_offering(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
            assert ClassSession.objects.upcoming_public_count() == 0

        def it_counts_a_session_starting_exactly_now(db):
            offering = _published_offering()
            now = timezone.now()
            ClassSessionFactory(class_offering=offering, starts_at=now, ends_at=now + timedelta(hours=2))
            # Pin ``timezone.now`` *inside the queryset* to the same instant the
            # session is stamped, so the ``>=`` boundary is exercised deterministically
            # (a real wall-clock would tick past ``now`` before the query runs and the
            # test would flake on whether the boundary is included).
            with mock.patch("classes.models.timezone.now", return_value=now):
                assert ClassSession.objects.upcoming_public_count() == 1

        def it_returns_zero_for_an_empty_catalog(db):
            assert ClassSession.objects.upcoming_public_count() == 0
