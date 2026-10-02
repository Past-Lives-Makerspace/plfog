"""Data-migration spec for core 0107: the stored #important-info links content says "classes".

Same ``MigrationExecutor`` pattern as my_tab_feature_migration_spec: the row is written
against the 0106 state, and each test restores the app to its head in a ``finally``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.models import DISCORD_INFO_LINKS_DEFAULT

_APP = "core"
_BEFORE = "0106_host_a_class_copy"
_AFTER = "0107_info_links_class_wording"

_migration = import_module(f"core.migrations.{_AFTER}")
OLD_LINE, NEW_LINE = _migration._OLD_LINE, _migration._NEW_LINE
OWN = "**Wiki**\nhttps://wiki.pastlives.space"


def _migrate(target: str | None) -> Any:
    """Migrate core to ``target`` (``None`` = head) and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    targets = [(_APP, target)] if target else executor.loader.graph.leaf_nodes(_APP)
    executor.migrate(targets)
    executor.loader.build_graph()
    return executor.loader.project_state(targets).apps


def _store(apps: Any, content: str) -> None:
    apps.get_model(_APP, "SiteConfiguration").objects.update_or_create(
        pk=1, defaults={"discord_info_links_content": content}
    )


def _stored(apps: Any) -> str:
    return apps.get_model(_APP, "SiteConfiguration").objects.get(pk=1).discord_info_links_content


@pytest.mark.django_db(transaction=True)
def describe_migration_0107_info_links_class_wording():
    def it_brings_the_old_starting_copy_to_the_current_default():
        try:
            _store(_migrate(_BEFORE), DISCORD_INFO_LINKS_DEFAULT.replace(NEW_LINE, OLD_LINE))

            assert _stored(_migrate(_AFTER)) == DISCORD_INFO_LINKS_DEFAULT
        finally:
            _migrate(None)

    def it_keeps_an_admins_other_edits_around_the_line():
        try:
            _store(_migrate(_BEFORE), f"{OLD_LINE}\n{OWN}")

            assert _stored(_migrate(_AFTER)) == f"{NEW_LINE}\n{OWN}"
        finally:
            _migrate(None)

    def it_leaves_an_admins_own_copy_alone_both_ways():
        try:
            _store(_migrate(_BEFORE), OWN)
            assert _stored(_migrate(_AFTER)) == OWN
            assert _stored(_migrate(_BEFORE)) == OWN
        finally:
            _migrate(None)

    def it_reverse_restores_the_old_line():
        try:
            _store(_migrate(_AFTER), DISCORD_INFO_LINKS_DEFAULT)

            assert _stored(_migrate(_BEFORE)) == DISCORD_INFO_LINKS_DEFAULT.replace(NEW_LINE, OLD_LINE)
        finally:
            _migrate(None)
