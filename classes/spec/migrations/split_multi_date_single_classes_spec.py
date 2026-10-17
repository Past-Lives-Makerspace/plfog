"""Data-migration spec for classes 0093: published single classes with several dates are split (#823).

Uses Django's ``MigrationExecutor`` (the pattern of the other migration specs in this package).
Classes are built with the factories at head, the schema steps back to 0092, and the
migration's functions run against that state's historical models, so the clone under test is
the migration's own and never ``ClassOffering.save``. Each test restores the schema to head in
a ``finally`` so the rest of the suite sees the current DB.
"""

from __future__ import annotations

from datetime import timedelta
from importlib import import_module
from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from classes.factories import (
    ClassFaqFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    SeriesClassOfferingFactory,
)
from classes.models import ClassOffering, ClassSession, CmsActivity, Registration

_APP = "classes"
_BEFORE = "0092_classinstructor"
_AFTER = "0093_split_multi_date_single_classes"
_HEAD = MigrationLoader(None).graph.leaf_nodes(_APP)[0][1]

_migration = import_module(f"classes.migrations.{_AFTER}")

pytestmark = pytest.mark.django_db(transaction=True)


def _migrate(target: str) -> Any:
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _published(title: str, *days: int, **kwargs: Any) -> ClassOffering:
    offering = ClassOfferingFactory(title=title, status=ClassOffering.Status.PUBLISHED, **kwargs)
    for day in days:
        start = timezone.now() + timedelta(days=day)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _starts(pk: int) -> list[Any]:
    return list(
        ClassSession.objects.filter(class_offering_id=pk).order_by("starts_at").values_list("starts_at", flat=True)
    )


def describe_classes_to_split():
    def it_picks_published_fixed_single_classes_with_several_dates_and_no_seat_held():
        sushi = _published("Sushi Set", 10, 31, 45, 59)
        cancelled_only = _published("Cup", 10, 20)
        RegistrationFactory(class_offering=cancelled_only, status=Registration.Status.CANCELLED)
        held = _published("Held", 10, 20)
        RegistrationFactory(class_offering=held, status=Registration.Status.PENDING)
        _published("One Date", 10)
        draft = ClassOfferingFactory(title="Draft")
        for day in (10, 20):
            start = timezone.now() + timedelta(days=day)
            ClassSessionFactory(class_offering=draft, starts_at=start, ends_at=start + timedelta(hours=2))
        series = SeriesClassOfferingFactory(title="Series", status=ClassOffering.Status.PUBLISHED, session_count=3)
        flexible = _published("Flexible", 10, 20, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        try:
            apps = _migrate(_BEFORE)
            picked = _migration.classes_to_split(apps)
        finally:
            _migrate(_HEAD)
        assert picked == [sushi.pk, cancelled_only.pk]
        assert series.pk not in picked and flexible.pk not in picked


def describe_split_all():
    def it_moves_each_later_date_onto_a_published_announced_run():
        first, second = InstructorFactory(), InstructorFactory()
        sushi = _published("Sushi Set", 10, 31, 45, slug="sushi-set", instructors=[first, second])
        ClassFaqFactory(class_offering=sushi, question="What do I take home?")
        ClassOfferingFactory(title="Taken", slug="sushi-set-run")
        starts = _starts(sushi.pk)
        try:
            apps = _migrate(_BEFORE)
            made = _migration.split_all(apps)
        finally:
            _migrate(_HEAD)

        assert [source for source, _ in made] == [sushi.pk, sushi.pk]
        assert _starts(sushi.pk) == starts[:1]
        sushi.refresh_from_db()
        runs = [ClassOffering.objects.get(pk=run_pk) for _, run_pk in made]
        assert [_starts(run.pk) for run in runs] == [[starts[1]], [starts[2]]]
        assert [run.slug for run in runs] == ["sushi-set-run-2", "sushi-set-run-3"]
        for run in runs:
            assert run.status == ClassOffering.Status.PUBLISHED
            assert run.published_at == sushi.published_at
            assert run.title == sushi.title
            assert run.grouping_key == sushi.grouping_key
            assert run.channel_announced_at is not None
            assert run.instructor_list == [first, second]
            assert run.gallery_images.count() == 1
            assert run.faqs.get().question == "What do I take home?"
            activity = CmsActivity.objects.get(class_offering=run, kind=CmsActivity.Kind.CLASS_PUBLISHED)
            assert activity.payload == {"marker": _migration.MARKER, "split_from": sushi.pk}

    def it_copies_the_announcement_stamp_of_an_announced_class():
        announced = timezone.now() - timedelta(days=2)
        cup = _published("Cup", 10, 20, channel_announced_at=announced)
        try:
            apps = _migrate(_BEFORE)
            made = _migration.split_all(apps)
        finally:
            _migrate(_HEAD)
        assert ClassOffering.objects.get(pk=made[0][1]).channel_announced_at == announced
        cup.refresh_from_db()
        assert cup.channel_announced_at == announced


def describe_merge_all():
    def it_puts_the_dates_back_and_removes_the_runs_nobody_signed_up_for():
        cup = _published("Cup", 10, 20, 30)
        starts = _starts(cup.pk)
        try:
            apps = _migrate(_BEFORE)
            made = _migration.split_all(apps)
            booked = made[1][1]
            apps.get_model(_APP, "Registration").objects.create(
                class_offering_id=booked,
                first_name="Sam",
                last_name="Smith",
                email="sam@example.com",
                status="confirmed",
                self_serve_token="token-merge",
                order_number="PL-0001-CO",
            )
            removed = _migration.merge_all(apps)
            # Read before stepping back to head, which applies 0093 again and would re-split.
            assert removed == [made[0][1]]
            assert _starts(cup.pk) == [starts[0], starts[1]]
            assert _starts(booked) == [starts[2]]
        finally:
            _migrate(_HEAD)

    def it_skips_a_run_already_deleted():
        cup = _published("Cup", 10, 20)
        try:
            apps = _migrate(_BEFORE)
            made = _migration.split_all(apps)
            apps.get_model(_APP, "ClassOffering").objects.filter(pk=made[0][1]).delete()
            removed = _migration.merge_all(apps)
        finally:
            _migrate(_HEAD)
        assert removed == []
        assert len(_starts(cup.pk)) == 1

    def it_runs_as_the_migrations_reverse():
        cup = _published("Cup", 10, 20)
        try:
            _migrate(_BEFORE)
            _migrate(_AFTER)
            assert len(_starts(cup.pk)) == 1
            _migrate(_BEFORE)
            assert len(_starts(cup.pk)) == 2
            assert ClassOffering.objects.count() == 1
        finally:
            _migrate(_HEAD)


def describe_split_all_when_the_first_date_has_passed():
    def it_leaves_the_earliest_future_run_unannounced_to_carry_the_post():
        _published("Cup", -6, 8, 22)
        try:
            apps = _migrate(_BEFORE)
            made = _migration.split_all(apps)
        finally:
            _migrate(_HEAD)
        first_run, second_run = (ClassOffering.objects.get(pk=run_pk) for _, run_pk in made)
        assert first_run.channel_announced_at is None
        assert second_run.channel_announced_at is not None


def describe_merge_all_without_a_source():
    def it_leaves_the_run_where_it_is():
        cup = _published("Cup", 10, 20)
        try:
            apps = _migrate(_BEFORE)
            made = _migration.split_all(apps)
            run_pk = made[0][1]
            Offering = apps.get_model(_APP, "ClassOffering")
            Offering.objects.filter(pk=cup.pk).delete()
            removed = _migration.merge_all(apps)
            assert removed == []
            assert Offering.objects.filter(pk=run_pk).exists()
            assert len(_starts(run_pk)) == 1
        finally:
            _migrate(_HEAD)
