"""BDD specs for the results-sent backfill data migration (0053).

Exercises the migration's forward/reverse functions directly against the live app
models (they are plain ORM updates), so pre-existing snapshots are stamped as
already-sent and the reverse genuinely nulls them.
"""

from __future__ import annotations

import importlib
from decimal import Decimal

import pytest
from django.apps import apps

from membership.models import FundingSnapshot

pytestmark = pytest.mark.django_db

_migration = importlib.import_module("membership.migrations.0053_backfill_funding_results_sent")


def describe_backfill_funding_results_sent():
    def it_stamps_existing_snapshots_as_already_sent():
        snap = FundingSnapshot.objects.create(
            cycle_label="May 2026", contributor_count=2, funding_pool=Decimal("1000.00")
        )
        assert snap.results_sent_at is None
        assert snap.results_send_count == 0

        _migration.set_results_sent(apps, None)

        snap.refresh_from_db()
        assert snap.results_sent_at == snap.snapshot_at
        assert snap.results_send_count == 1

    def it_reverse_nulls_them():
        snap = FundingSnapshot.objects.create(
            cycle_label="May 2026", contributor_count=2, funding_pool=Decimal("1000.00")
        )
        _migration.set_results_sent(apps, None)

        _migration.unset_results_sent(apps, None)

        snap.refresh_from_db()
        assert snap.results_sent_at is None
        assert snap.results_send_count == 0
