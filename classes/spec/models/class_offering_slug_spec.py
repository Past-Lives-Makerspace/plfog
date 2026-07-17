"""BDD specs for ClassOffering.finalize_recurring_slug — date-stamped slugs (ADR 0002)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory


def _attach_session(offering, starts_at):
    ClassSessionFactory(class_offering=offering, starts_at=starts_at, ends_at=starts_at + timedelta(hours=2))


def describe_finalize_recurring_slug():
    def it_stamps_the_slug_with_the_first_session_date(db):
        offering = ClassOfferingFactory(title="Intro to Forging", slug="provisional-1")
        starts_at = timezone.make_aware(datetime(2026, 8, 15, 18, 0))
        _attach_session(offering, starts_at)
        offering.finalize_recurring_slug()
        offering.refresh_from_db()
        assert offering.slug == "intro-to-forging-2026-08-15"

    def it_uses_the_earliest_session_when_several_are_attached(db):
        offering = ClassOfferingFactory(title="Series", slug="provisional-2")
        first = timezone.make_aware(datetime(2026, 9, 3, 10, 0))
        _attach_session(offering, first + timedelta(days=14))
        _attach_session(offering, first)
        offering.finalize_recurring_slug()
        offering.refresh_from_db()
        assert offering.slug == "series-2026-09-03"

    def it_uses_the_local_date_when_the_utc_date_differs(db):
        # 03:00 UTC on the 16th is still the 15th in Portland (UTC-7/-8), so the
        # slug must read the LOCAL calendar day, not the stored UTC day.
        offering = ClassOfferingFactory(title="Night Forge", slug="provisional-3")
        starts_at = datetime(2026, 7, 16, 3, 0, tzinfo=UTC)
        _attach_session(offering, starts_at)
        assert starts_at.date().isoformat() == "2026-07-16"
        assert timezone.localtime(starts_at).date().isoformat() == "2026-07-15"
        offering.finalize_recurring_slug()
        offering.refresh_from_db()
        assert offering.slug == "night-forge-2026-07-15"

    def it_falls_back_to_a_numeric_tiebreak_on_a_same_day_collision(db):
        starts_at = timezone.make_aware(datetime(2026, 8, 15, 18, 0))
        first = ClassOfferingFactory(title="Weld", slug="weld-a")
        _attach_session(first, starts_at)
        first.finalize_recurring_slug()
        second = ClassOfferingFactory(title="Weld", slug="weld-b")
        _attach_session(second, starts_at)
        second.finalize_recurring_slug()
        third = ClassOfferingFactory(title="Weld", slug="weld-c")
        _attach_session(third, starts_at)
        third.finalize_recurring_slug()
        assert first.slug == "weld-2026-08-15"
        assert second.slug == "weld-2026-08-15-2"
        assert third.slug == "weld-2026-08-15-3"

    def it_falls_back_to_the_creation_date_when_there_are_no_sessions(db):
        offering = ClassOfferingFactory(title="Someday", slug="provisional-4")
        offering.finalize_recurring_slug()
        offering.refresh_from_db()
        assert offering.slug == f"someday-{timezone.localdate():%Y-%m-%d}"

    def it_uses_class_as_the_base_when_the_title_has_no_slug_chars(db):
        offering = ClassOfferingFactory(title="!!! ???", slug="provisional-5")
        starts_at = timezone.make_aware(datetime(2026, 8, 15, 18, 0))
        _attach_session(offering, starts_at)
        offering.finalize_recurring_slug()
        offering.refresh_from_db()
        assert offering.slug == "class-2026-08-15"


def describe_clone_slugs_are_untouched_by_date_stamping():
    def it_keeps_the_copy_suffix_when_duplicating(db):
        offering = ClassOfferingFactory(title="Pottery", slug="pottery")
        assert offering.duplicate().slug == "pottery-copy"

    def it_keeps_the_run_suffix_when_spinning_off_a_new_run(db):
        offering = ClassOfferingFactory(title="Pottery", slug="pottery")
        assert offering.duplicate_as_new_run().slug == "pottery-run"
