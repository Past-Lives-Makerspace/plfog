"""Data-migration spec for classes 0076: the Host a Class page's starting words say "class".

Same ``MigrationExecutor`` pattern as teach_page_no_free_option_spec: rows are built
against the 0075 state, and each test restores the schema to head in a ``finally``.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

_APP = "classes"
_BEFORE = "0075_class_type_and_host_a_class_copy"
_AFTER = "0076_host_a_class_starting_copy"
_HEAD = MigrationLoader(None).graph.leaf_nodes(_APP)[0][1]

COPY_FIELDS = import_module(f"classes.migrations.{_AFTER}").COPY_FIELDS

CUSTOM = {
    "teach_page_lead": "An admin's own workshop lead.",
    "teach_page_features": "One Card: An admin's own list.",
    "teach_page_faq": "<h3>Own?</h3><p>An admin's own answer.</p>",
}


def _migrate(target: str):
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _make_settings(apps, **kwargs):
    ClassSettings = apps.get_model(_APP, "ClassSettings")
    ClassSettings.objects.update_or_create(
        pk=1,
        defaults={"liability_waiver_text": "LIABILITY", "model_release_waiver_text": "MODEL RELEASE", **kwargs},
    )


def _settings(apps):
    return apps.get_model(_APP, "ClassSettings").objects.get(pk=1)


@pytest.mark.django_db(transaction=True)
def describe_migration_0076_host_a_class_starting_copy():
    def it_moves_every_old_default_to_the_class_wording():
        try:
            apps = _migrate(_BEFORE)
            _make_settings(apps, **{name: old for name, (old, _new) in COPY_FIELDS.items()})

            row = _settings(_migrate(_AFTER))
            for name, (_old, new) in COPY_FIELDS.items():
                assert getattr(row, name) == new
                assert "workshop" not in getattr(row, name).lower()
        finally:
            _migrate(_HEAD)

    def it_leaves_an_admins_own_copy_alone_both_ways():
        try:
            apps = _migrate(_BEFORE)
            _make_settings(apps, **CUSTOM)
            row = _settings(_migrate(_AFTER))
            for name, value in CUSTOM.items():
                assert getattr(row, name) == value
            row = _settings(_migrate(_BEFORE))
            for name, value in CUSTOM.items():
                assert getattr(row, name) == value
        finally:
            _migrate(_HEAD)

    def it_reverse_restores_the_old_default():
        try:
            apps = _migrate(_AFTER)
            _make_settings(apps, **{name: new for name, (_old, new) in COPY_FIELDS.items()})

            row = _settings(_migrate(_BEFORE))
            for name, (old, _new) in COPY_FIELDS.items():
                assert getattr(row, name) == old
        finally:
            _migrate(_HEAD)

    def it_matches_the_current_model_defaults():
        from classes.models import ClassSettings

        for name, (_old, new) in COPY_FIELDS.items():
            assert ClassSettings._meta.get_field(name).default == new
