"""Data-migration spec for classes 0063: the Host a Workshop defaults lose the free option.

Uses Django's ``MigrationExecutor`` (the pattern of convert_teach_page_markdown_spec) so
rows are built against the 0062 state, before the three defaults change. Each test
restores the schema to head in a ``finally`` so the rest of the suite sees the current DB.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

_APP = "classes"
_BEFORE = "0062_convert_teach_page_markdown"
_AFTER = "0063_teach_page_no_free_option"
_HEAD = "0063_teach_page_no_free_option"

_migration = import_module(f"classes.migrations.{_AFTER}")
COPY_FIELDS = _migration.COPY_FIELDS

# An admin's own copy in every field: never touched, in either direction.
CUSTOM = {
    "teach_page_features": "One Card: An admin's own list.",
    "teach_page_faq": "<h3>Is It Free?</h3><p>An admin's own answer.</p>",
    "teach_page_split_note": "An admin's own note.",
}


def _migrate(target: str):
    """Migrate the classes app to ``target`` and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _make_settings(apps, **kwargs):
    """Put ``kwargs`` on the settings row. Migration 0058 seeds pk=1, so update it when it is there."""
    ClassSettings = apps.get_model(_APP, "ClassSettings")
    row, _created = ClassSettings.objects.update_or_create(
        pk=1,
        defaults={"liability_waiver_text": "LIABILITY", "model_release_waiver_text": "MODEL RELEASE", **kwargs},
    )
    return row


def _settings(apps):
    return apps.get_model(_APP, "ClassSettings").objects.get(pk=1)


@pytest.mark.django_db(transaction=True)
def describe_migration_0063_teach_page_no_free_option():
    def it_moves_every_old_default_to_the_new_default():
        try:
            apps = _migrate(_BEFORE)
            _make_settings(apps, **{name: old for name, (old, _new) in COPY_FIELDS.items()})

            apps = _migrate(_AFTER)
            row = _settings(apps)
            for name, (_old, new) in COPY_FIELDS.items():
                assert getattr(row, name) == new
            assert "free" not in row.teach_page_features.lower()
            assert "run it free" not in row.teach_page_faq
            assert row.teach_page_split_note == "Every class splits the same way."
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
            assert "Run it free and there is nothing to split." in row.teach_page_split_note
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
