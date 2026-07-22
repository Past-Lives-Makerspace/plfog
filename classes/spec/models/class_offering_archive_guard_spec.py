"""Specs for ClassOfferingQuerySet.archive_missing_from_legacy_feed.

The legacy feed drives a mass-archive across nearly the whole catalog, so the guard
around it is the safety net for the one failure mode that would empty the class
listing in a single statement: a feed that answers HTTP 200 with nothing in it.
"""

from __future__ import annotations

import pytest

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db


def _legacy(n: int) -> ClassOffering:
    return ClassOfferingFactory(
        legacy_cms_id=f"uuid-{n}",
        status=ClassOffering.Status.PUBLISHED,
        image="",
    )


def describe_archive_missing_from_legacy_feed():
    def it_archives_the_handful_the_feed_dropped():
        kept = [_legacy(i) for i in range(9)]
        dropped = _legacy(99)

        archived = ClassOffering.objects.archive_missing_from_legacy_feed([o.legacy_cms_id for o in kept])

        dropped.refresh_from_db()
        assert archived == 1
        assert dropped.status == ClassOffering.Status.ARCHIVED
        assert all(ClassOffering.objects.get(pk=o.pk).status == ClassOffering.Status.PUBLISHED for o in kept)

    def describe_when_the_feed_came_back_empty():
        def it_archives_nothing():
            offerings = [_legacy(i) for i in range(3)]

            archived = ClassOffering.objects.archive_missing_from_legacy_feed([])

            assert archived == 0
            for offering in offerings:
                offering.refresh_from_db()
                assert offering.status == ClassOffering.Status.PUBLISHED

        def it_logs_loudly(caplog):
            _legacy(1)

            with caplog.at_level("ERROR", logger="classes.models"):
                ClassOffering.objects.archive_missing_from_legacy_feed([])

            assert "ARCHIVE GUARD TRIPPED" in caplog.text
            assert "NOTHING WAS ARCHIVED" in caplog.text

    def describe_when_the_feed_would_archive_more_than_half():
        def it_archives_nothing():
            offerings = [_legacy(i) for i in range(4)]

            # Feed lists 1 of 4 — archiving 3 (75%) is over the 50% ceiling.
            archived = ClassOffering.objects.archive_missing_from_legacy_feed([offerings[0].legacy_cms_id])

            assert archived == 0
            for offering in offerings:
                offering.refresh_from_db()
                assert offering.status == ClassOffering.Status.PUBLISHED

        def it_still_archives_at_exactly_half():
            offerings = [_legacy(i) for i in range(4)]
            seen = [o.legacy_cms_id for o in offerings[:2]]

            archived = ClassOffering.objects.archive_missing_from_legacy_feed(seen)

            assert archived == 2

    def describe_when_nothing_needs_archiving():
        def it_returns_zero_without_writing():
            offerings = [_legacy(i) for i in range(3)]

            archived = ClassOffering.objects.archive_missing_from_legacy_feed([o.legacy_cms_id for o in offerings])

            assert archived == 0

        def it_returns_zero_when_no_offering_came_from_the_legacy_cms():
            ClassOfferingFactory(legacy_cms_id="", image="")

            assert ClassOffering.objects.archive_missing_from_legacy_feed([]) == 0

    def it_ignores_offerings_already_archived_when_measuring_the_blast_radius():
        # A backlog of previously-archived legacy rows must not make the guard trip
        # forever on a healthy feed.
        for i in range(20):
            ClassOfferingFactory(
                legacy_cms_id=f"old-{i}",
                status=ClassOffering.Status.ARCHIVED,
                image="",
            )
        live = [_legacy(i) for i in range(4)]

        archived = ClassOffering.objects.archive_missing_from_legacy_feed([o.legacy_cms_id for o in live[:3]])

        assert archived == 1
