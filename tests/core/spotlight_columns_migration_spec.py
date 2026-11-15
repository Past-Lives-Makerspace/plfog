"""BDD specs for unapplying 0124, which takes the Spotlight Text and Meeting fields out of the model (#935).

On PostgreSQL the columns stay until #938 drops them. On SQLite 0124 drops them with the fields, because
every later rebuild of the table there builds it from the model state and drops them anyway. Before it
did, unapplying 0124 on SQLite rebuilt the table from a state that has them again, selected
"spotlight_meeting_event_id" from a table without it, and SQLite read the quoted name as a string, so
the foreign key check failed every migration spec that rewinds core past 0124 (#1200).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.models import SiteConfiguration

BEFORE = ("core", "0123_scheduledjobstate_cadence")
AFTER = ("core", "0124_remove_spotlight_text_and_meeting")

pytestmark = pytest.mark.django_db(transaction=True)


def _migrate(target: tuple[str, str]) -> Any:
    executor = MigrationExecutor(connection)
    executor.migrate([target])
    executor.loader.build_graph()
    return executor.loader.project_state([target]).apps


@pytest.fixture
def old_apps() -> Iterator[Any]:
    """The app registry at 0123, rewound from the latest core with a site configuration row in place;
    core goes back to its latest migration after."""
    SiteConfiguration.load()
    latest = MigrationExecutor(connection).loader.graph.leaf_nodes("core")[0]
    try:
        yield _migrate(BEFORE)
    finally:
        _migrate(latest)


def describe_0124_reverse():
    def it_rewinds_past_a_later_rebuild_and_leaves_the_meeting_empty(old_apps: Any):
        config = old_apps.get_model("core", "SiteConfiguration").objects.get()

        assert config.spotlight_meeting_event_id is None

    def it_goes_forward_again_with_the_row_intact(old_apps: Any):
        new_apps = _migrate(AFTER)

        assert new_apps.get_model("core", "SiteConfiguration").objects.count() == 1
