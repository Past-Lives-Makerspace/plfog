"""Data-migration spec for classes 0072: the Host a Workshop defaults stop naming the member discount.

Uses Django's ``MigrationExecutor`` (the pattern of teach_page_no_free_option_spec) so rows
are built against the 0071 state, before the two defaults change and the discount columns
go. Each test restores the schema to head in a ``finally`` so the rest of the suite sees
the current DB.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

_APP = "classes"
_BEFORE = "0071_classoffering_hero_cropped"
_AFTER = "0072_remove_member_discount"
# The real head off the graph: a pinned name goes stale the day a later migration drops a column.
_HEAD = MigrationLoader(None).graph.leaf_nodes(_APP)[0][1]

_migration = import_module(f"classes.migrations.{_AFTER}")
COPY_FIELDS = _migration.COPY_FIELDS

# An admin's own copy in both fields: never touched, in either direction.
CUSTOM = {
    "teach_page_features": "One Card: An admin's own list.",
    "teach_page_faq": "<h3>Is It Free?</h3><p>An admin's own answer.</p>",
}


def _migrate(target: str):
    """Migrate the classes app to ``target`` and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _make_settings(apps, **kwargs):
    """Put ``kwargs`` on the settings row. Migration 0008 seeds pk=1, so update it when it is there."""
    ClassSettings = apps.get_model(_APP, "ClassSettings")
    row, _created = ClassSettings.objects.update_or_create(
        pk=1,
        defaults={"liability_waiver_text": "LIABILITY", "model_release_waiver_text": "MODEL RELEASE", **kwargs},
    )
    return row


def _settings(apps):
    return apps.get_model(_APP, "ClassSettings").objects.get(pk=1)


def _columns(table: str) -> set[str]:
    with connection.cursor() as cursor:
        return {column.name for column in connection.introspection.get_table_description(cursor, table)}


@pytest.mark.django_db(transaction=True)
def describe_migration_0072_remove_member_discount():
    def it_drops_both_discount_columns_and_the_reverse_puts_them_back_at_ten():
        try:
            _migrate(_BEFORE)
            assert "member_discount_pct" in _columns("classes_classoffering")
            assert "default_member_discount_pct" in _columns("classes_classsettings")

            _migrate(_AFTER)
            assert "member_discount_pct" not in _columns("classes_classoffering")
            assert "default_member_discount_pct" not in _columns("classes_classsettings")

            apps = _migrate(_BEFORE)
            _make_settings(apps)
            assert _settings(apps).default_member_discount_pct == 10
        finally:
            _migrate(_HEAD)

    def it_moves_every_old_default_to_the_new_default():
        try:
            apps = _migrate(_BEFORE)
            _make_settings(apps, **{name: old for name, (old, _new) in COPY_FIELDS.items()})

            apps = _migrate(_AFTER)
            row = _settings(apps)
            for name, (_old, new) in COPY_FIELDS.items():
                assert getattr(row, name) == new
            assert "member discount" not in row.teach_page_features
            assert "member discount" not in row.teach_page_faq
        finally:
            _migrate(_HEAD)

    def it_leaves_an_admins_own_copy_alone_going_forward():
        try:
            apps = _migrate(_BEFORE)
            _make_settings(apps, **CUSTOM)

            apps = _migrate(_AFTER)
            row = _settings(apps)
            for name, value in CUSTOM.items():
                assert getattr(row, name) == value
        finally:
            _migrate(_HEAD)

    def it_reverse_restores_the_old_default():
        try:
            apps = _migrate(_AFTER)
            _make_settings(apps, **{name: new for name, (_old, new) in COPY_FIELDS.items()})

            apps = _migrate(_BEFORE)
            row = _settings(apps)
            for name, (old, _new) in COPY_FIELDS.items():
                assert getattr(row, name) == old
            assert "Set a price with a member discount" in row.teach_page_features
        finally:
            _migrate(_HEAD)

    def it_reverse_leaves_an_admins_own_copy_alone():
        try:
            apps = _migrate(_AFTER)
            _make_settings(apps, **CUSTOM)

            apps = _migrate(_BEFORE)
            row = _settings(apps)
            for name, value in CUSTOM.items():
                assert getattr(row, name) == value
        finally:
            _migrate(_HEAD)

    def it_does_nothing_with_no_settings_row():
        try:
            apps = _migrate(_BEFORE)
            apps.get_model(_APP, "ClassSettings").objects.all().delete()
            apps = _migrate(_AFTER)
            assert apps.get_model(_APP, "ClassSettings").objects.count() == 0
        finally:
            _migrate(_HEAD)
