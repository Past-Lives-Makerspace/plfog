"""Put every app back at head after each classes migration spec, whatever the spec did.

Each spec restores with ``_head.migrate_all_to_head`` in its ``finally``; this is the net under it
for a spec that forgets or fails before its ``try``. The root ``conftest._restore_latest_migrations``
does the same once per file, too late for the next spec in this one. Nothing is migrated when
nothing is behind, so a spec that restored properly pays for one plan.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pytest_django.plugin import DjangoDbBlocker

from classes.spec.migrations._head import migrate_all_to_head


@pytest.fixture(autouse=True)
def _restore_every_app_after_each_spec(django_db_blocker: DjangoDbBlocker) -> Iterator[None]:
    yield
    with django_db_blocker.unblock():
        migrate_all_to_head()
