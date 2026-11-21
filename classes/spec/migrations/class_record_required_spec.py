"""Migration 0109 (#1260, part 6): it makes a run's class required, and refuses to guess one."""

from __future__ import annotations

from importlib import import_module
from unittest.mock import MagicMock

import pytest

_migration = import_module("classes.migrations.0109_class_record_required")


def _apps_with(unfiled: int) -> MagicMock:
    apps = MagicMock()
    apps.get_model.return_value.objects.filter.return_value.count.return_value = unfiled
    return apps


def describe_migration_0109_class_record_required():
    def it_stops_with_the_count_while_a_run_has_no_class():
        with pytest.raises(RuntimeError, match="2 class run"):
            _migration.refuse_runs_with_no_class(_apps_with(2), None)

    def it_goes_ahead_when_every_run_has_a_class():
        apps = _apps_with(0)

        _migration.refuse_runs_with_no_class(apps, None)

        apps.get_model.assert_called_once_with("classes", "ClassOffering")
