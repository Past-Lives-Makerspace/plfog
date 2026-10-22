"""Retire the Holiday Bizarre feature switch.

The sidebar entry no longer reads a switch: it shows to admins whenever an HQ exists and to anyone
else once a card on the open HQ is opened to them (``hub.context_processors``, HQ card sharing). A
row left behind would sit in Site Settings as a switch that does nothing, so it is deleted. The
reverse writes it back Hidden, the state core migration 0118 seeded.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

FEATURE_KEY = "holiday_bizarre"


def _forward(apps: Any, schema_editor: Any) -> None:
    FeatureSwitch = apps.get_model("core", "FeatureSwitch")
    FeatureSwitch.objects.filter(feature_key=FEATURE_KEY).delete()


def _reverse(apps: Any, schema_editor: Any) -> None:
    FeatureSwitch = apps.get_model("core", "FeatureSwitch")
    FeatureSwitch.objects.get_or_create(feature_key=FEATURE_KEY, defaults={"state": "hidden"})


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0118_seed_holiday_bizarre_switch"),
    ]

    operations = [
        migrations.RunPython(_forward, _reverse),
    ]
