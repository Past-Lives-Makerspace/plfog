"""Delete the now-unused ScheduledNotificationMarker model (design §2.5, Phase 7).

The marker was the legacy per-job dedupe for time-based notification jobs. Phase 5/6
folded every scheduled job onto the :class:`core.models.EventDelivery` ledger (the
voting reminder via the generalized scheduler, the lease-expiry reminder via emit's
``lease:<pk>:expiring`` period), so nothing reads or writes the marker any more.

``DeleteModel`` auto-reverses to ``CreateModel`` (re-creating the empty table), so this
migration is reversible — the historical rows are not restored (they were pure dedupe
state, not domain data), but the schema round-trips cleanly.
"""

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0034_notificationpreference_per_channel"),
    ]

    operations = [
        migrations.DeleteModel(name="ScheduledNotificationMarker"),
    ]
