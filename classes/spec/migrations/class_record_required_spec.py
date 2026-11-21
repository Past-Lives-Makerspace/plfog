"""Migration 0109 (#1260, part 6): a run's class becomes required, and a run with none stops it."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering
from classes.spec.migrations._head import migrate_all_to_head

pytestmark = pytest.mark.django_db(transaction=True)

_BEFORE = [("classes", "0108_class_record_states")]
_AFTER = [("classes", "0109_class_record_required")]


def _migrate(target: list[tuple[str, str]]) -> None:
    MigrationExecutor(connection).migrate(target)


def describe_migration_0109_class_record_required():
    def it_refuses_to_run_while_a_run_has_no_class():
        run = ClassOfferingFactory(title="Unfiled", slug="unfiled")
        try:
            _migrate(_BEFORE)
            ClassOffering.objects.filter(pk=run.pk).update(class_record=None)

            with pytest.raises(IntegrityError):
                _migrate(_AFTER)
        finally:
            ClassOffering.objects.filter(pk=run.pk).delete()
            migrate_all_to_head()

    def it_makes_the_class_required_when_every_run_has_one():
        run = ClassOfferingFactory(title="Filed", slug="filed")
        try:
            _migrate(_BEFORE)
            _migrate(_AFTER)
        finally:
            migrate_all_to_head()

        with pytest.raises(IntegrityError):
            ClassOffering.objects.filter(pk=run.pk).update(class_record=None)
