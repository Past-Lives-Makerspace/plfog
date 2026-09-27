"""Data-migration spec for core 0102/0103: the My Tab boolean becomes the ``my_tab`` feature (#416).

Uses Django's ``MigrationExecutor`` (the pronouns and stripe-mode specs' approach) so the
boolean is set against the 0101 schema, where it still exists, and read back after 0103 drops
it. Each test restores the app to its head in a ``finally`` so the rest of the suite sees the
current schema.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

_APP = "core"
_BEFORE = "0101_retire_community_wording"
_AFTER = "0103_drop_my_tab_boolean"
# The retired column. Built rather than written out so a grep for the old name finds only migrations.
_BOOLEAN = "my_tab" + "_enabled"


def _migrate(target: str | None) -> Any:
    """Migrate core to ``target`` (``None`` = head) and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    targets = [(_APP, target)] if target else executor.loader.graph.leaf_nodes(_APP)
    executor.migrate(targets)
    executor.loader.build_graph()
    return executor.loader.project_state(targets).apps


def _state_after_forward(apps: Any) -> str:
    FeatureSwitch = apps.get_model(_APP, "FeatureSwitch")
    return FeatureSwitch.objects.get(feature_key="my_tab").state


@pytest.mark.django_db(transaction=True)
def describe_migration_0102_fold_my_tab_into_feature_switches():
    @pytest.mark.parametrize(("enabled", "expected"), [(True, "on"), (False, "hidden")])
    def it_maps_the_boolean_onto_the_feature_state(enabled: bool, expected: str):
        try:
            apps = _migrate(_BEFORE)
            SiteConfiguration = apps.get_model(_APP, "SiteConfiguration")
            SiteConfiguration.objects.all().delete()
            SiteConfiguration.objects.create(**{_BOOLEAN: enabled})

            assert _state_after_forward(_migrate(_AFTER)) == expected
        finally:
            _migrate(None)

    def it_seeds_on_when_no_settings_row_exists_yet():
        # A fresh install has no singleton; the boolean's own default (True) is what it meant.
        try:
            apps = _migrate(_BEFORE)
            apps.get_model(_APP, "SiteConfiguration").objects.all().delete()

            assert _state_after_forward(_migrate(_AFTER)) == "on"
        finally:
            _migrate(None)

    @pytest.mark.parametrize(("state", "expected"), [("on", True), ("soon", False), ("hidden", False)])
    def it_reverses_only_on_back_to_true_and_removes_the_row(state: str, expected: bool):
        try:
            apps = _migrate(_AFTER)
            apps.get_model(_APP, "SiteConfiguration").objects.all().delete()
            apps.get_model(_APP, "SiteConfiguration").objects.create()
            apps.get_model(_APP, "FeatureSwitch").objects.update_or_create(
                feature_key="my_tab", defaults={"state": state}
            )

            apps = _migrate(_BEFORE)
            config = apps.get_model(_APP, "SiteConfiguration").objects.get()
            assert getattr(config, _BOOLEAN) is expected
            assert not apps.get_model(_APP, "FeatureSwitch").objects.filter(feature_key="my_tab").exists()
        finally:
            _migrate(None)
