"""Data-migration spec for classes 0107: one class per grouping key, every run attached (#1260).

Runs are built with the factories at head, the schema steps back to 0106 (whose reverse detaches
every run and deletes every class), and the migration's function runs against that state's
historical models. Each test restores the schema to head in a ``finally``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.models import ClassOffering, ClassRecord
from classes.spec.migrations._head import migrate_all_to_head

_APP = "classes"
_BEFORE = "0106_class_record"
_AFTER = "0107_backfill_class_records"

_migration = import_module(f"classes.migrations.{_AFTER}")

pytestmark = pytest.mark.django_db(transaction=True)


def _migrate(target: str) -> Any:
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def describe_migration_0107_backfill_class_records():
    def it_makes_one_class_per_grouping_key_and_attaches_every_run():
        wood, glass = CategoryFactory(slug="wood"), CategoryFactory(slug="glass")
        framing = [
            ClassOfferingFactory(title="Picture Framing - 1/2/26", slug="picture-framing", category=wood),
            ClassOfferingFactory(title="Picture Framing - 3/4/26", slug="picture-framing-run", category=wood),
        ]
        glass_framing = ClassOfferingFactory(title="Picture Framing", slug="framing-glass", category=glass)
        forge_owner = ClassOfferingFactory(title="Open Forge Sundays", slug="forge", category=wood)
        forge = ClassOfferingFactory(title="Forge", slug="forge-2026-12-01", category=wood)
        try:
            apps = _migrate(_BEFORE)
            assert not ClassRecord.objects.exists()
            assert not ClassOffering.objects.filter(class_record__isnull=False).exists()

            made = _migration.attach_runs(apps)
        finally:
            migrate_all_to_head()

        assert made == 4
        assert not ClassOffering.objects.filter(class_record__isnull=True).exists()
        record = ClassOffering.objects.get(pk=framing[0].pk).class_record
        assert set(record.runs.values_list("pk", flat=True)) == {run.pk for run in framing}
        # The oldest class keeps the bare address, here also its own run's old address.
        assert record.slug == "picture-framing"
        # The newest run names the class.
        assert record.title == "Picture Framing - 3/4/26"
        assert record.category == wood
        assert ClassOffering.objects.get(pk=glass_framing.pk).class_record.slug == "picture-framing-glass"
        assert ClassOffering.objects.get(pk=forge_owner.pk).class_record.slug == "open-forge-sundays"
        # "forge" is another class's run, so its links stay that class's.
        assert ClassOffering.objects.get(pk=forge.pk).class_record.slug == "forge-wood"

    def it_changes_nothing_else_on_a_run():
        run = ClassOfferingFactory(title="Make a Cup", slug="make-a-cup")
        before = ClassOffering.objects.filter(pk=run.pk).values("updated_at", "grouping_key", "slug", "title").get()
        try:
            apps = _migrate(_BEFORE)
            _migration.attach_runs(apps)
        finally:
            migrate_all_to_head()

        after = ClassOffering.objects.filter(pk=run.pk).values("updated_at", "grouping_key", "slug", "title").get()
        assert after == before

    def it_does_nothing_the_second_time():
        ClassOfferingFactory(title="Make a Cup", slug="make-a-cup")
        try:
            apps = _migrate(_BEFORE)
            assert _migration.attach_runs(apps) == 1
            assert _migration.attach_runs(apps) == 0
        finally:
            migrate_all_to_head()

        assert ClassRecord.objects.count() == 1

    def it_gives_an_untitled_run_a_class_of_its_own():
        run = ClassOfferingFactory(title="", slug="untitled")
        try:
            apps = _migrate(_BEFORE)
            _migration.attach_runs(apps)
        finally:
            migrate_all_to_head()

        assert ClassOffering.objects.get(pk=run.pk).class_record.slug == "class"

    def it_reverses_to_no_classes():
        ClassOfferingFactory(title="Make a Cup", slug="make-a-cup")
        try:
            _migrate(_AFTER)
            assert ClassRecord.objects.count() == 1
            _migrate(_BEFORE)
            assert not ClassRecord.objects.exists()
            assert not ClassOffering.objects.filter(class_record__isnull=False).exists()
        finally:
            migrate_all_to_head()
