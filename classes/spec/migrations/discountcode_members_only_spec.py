"""Migration spec for classes 0100: discount codes get Members only, switched on for MEMBER10 (#1072).

Uses Django's ``MigrationExecutor`` (the pattern of backfill_personal_discount_codes_spec). Codes
are built at 0099, before the column exists, through that state's historical model; stepping to
0100 adds the column and runs the data step, and stepping back runs the reverse. Each test
restores the schema to head in a ``finally`` so the rest of the suite sees the current DB.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

from classes.models import DiscountCode

_APP = "classes"
_BEFORE = "0099_backfill_personal_discount_codes"
_AFTER = "0100_discountcode_members_only"
_HEAD = MigrationLoader(None).graph.leaf_nodes(_APP)[0][1]


def _migrate(target: str):
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _columns() -> set[str]:
    with connection.cursor() as cursor:
        return {
            column.name for column in connection.introspection.get_table_description(cursor, "classes_discountcode")
        }


def _make(apps, code: str) -> None:
    apps.get_model(_APP, "DiscountCode").objects.create(code=code, discount_pct=10, is_active=True, is_approved=True)


@pytest.mark.django_db(transaction=True)
def describe_migration_0100_discountcode_members_only():
    def it_switches_on_member10_and_leaves_every_other_code_off():
        try:
            apps = _migrate(_BEFORE)
            assert "members_only" not in _columns()
            _make(apps, "MEMBER10")
            _make(apps, "SAVE20")
            _make(apps, "MEMBER15")

            _migrate(_AFTER)
            assert dict(DiscountCode.objects.values_list("code", "members_only")) == {
                "MEMBER10": True,
                "SAVE20": False,
                "MEMBER15": False,
            }
        finally:
            _migrate(_HEAD)

    def it_does_nothing_when_there_is_no_member10():
        try:
            apps = _migrate(_BEFORE)
            _make(apps, "SAVE20")
            _migrate(_AFTER)
            assert list(DiscountCode.objects.values_list("code", "members_only")) == [("SAVE20", False)]
        finally:
            _migrate(_HEAD)

    def it_reverses_by_switching_member10_off_and_dropping_the_column():
        try:
            apps = _migrate(_BEFORE)
            _make(apps, "MEMBER10")
            _migrate(_AFTER)
            assert DiscountCode.objects.get(code="MEMBER10").members_only is True

            _migrate(_BEFORE)
            assert "members_only" not in _columns()
        finally:
            _migrate(_HEAD)

    def it_switches_member10_off_in_the_reverse_step_itself():
        try:
            _migrate(_AFTER)
            DiscountCode.objects.create(code="MEMBER10", discount_pct=10, members_only=True)
            from importlib import import_module

            from django.apps import apps as live_apps

            import_module(f"classes.migrations.{_AFTER}").backwards(live_apps, None)
            assert DiscountCode.objects.get(code="MEMBER10").members_only is False
        finally:
            _migrate(_HEAD)
