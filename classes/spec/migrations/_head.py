"""Back to head for the classes migration specs: every app, not only ``classes``.

Stepping ``classes`` back unapplies every later migration of another app that depends on it
(membership 0259 onward depends on classes 0097). Migrating ``classes`` alone back to its own leaf
brings back only what that leaf needs, so a spec that read a Member right after its ``finally``
read a table missing its newest columns (#1077's ``staff_onboarding_step``).
"""

from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor


def migrate_all_to_head() -> None:
    """Apply whatever is behind, in every app; nothing when everything is already at head."""
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    if executor.migration_plan(leaves):
        executor.migrate(leaves)
