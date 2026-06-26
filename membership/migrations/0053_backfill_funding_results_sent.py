"""Backfill ``results_sent_at`` / ``results_send_count`` on pre-existing snapshots.

Every snapshot that already existed was created under the old auto-send behavior, so
its results were already emailed. Without this, they'd all read ``results_sent_at IS
NULL`` and wrongly surface in the new "review & send" banner. Forward stamps each with
``results_sent_at = snapshot_at`` and ``results_send_count = 1``; reverse nulls them
(a real reverse, not a no-op).
"""

from django.db import migrations
from django.db.models import F


def set_results_sent(apps, schema_editor):
    FundingSnapshot = apps.get_model("membership", "FundingSnapshot")
    FundingSnapshot.objects.filter(results_sent_at__isnull=True).update(
        results_sent_at=F("snapshot_at"),
        results_send_count=1,
    )


def unset_results_sent(apps, schema_editor):
    FundingSnapshot = apps.get_model("membership", "FundingSnapshot")
    FundingSnapshot.objects.update(results_sent_at=None, results_send_count=0)


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0052_fundingsnapshot_results_flags"),
    ]

    operations = [
        migrations.RunPython(set_results_sent, unset_results_sent),
    ]
