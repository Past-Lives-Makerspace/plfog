"""Seed the Holiday Bizarre feature switch Hidden.

``core.features.is_on`` reads a key with no row as ON, which is what makes every other new
feature a no-op on deploy. This one is different: its sidebar entry opens the Holiday Bizarre
HQ, and until an admin makes the first HQ that entry would point every member at a 404. So the
row is written Hidden here, and an admin flips it to Coming soon or On from Site Settings once
the HQ exists.

Only a missing row is written: a database that already carries a state (a re-run, or an admin
who flipped it before this migration applied) keeps it. The reverse deletes the row, so the key
reads ON again exactly as an unseeded feature does.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

FEATURE_KEY = "holiday_bizarre"


def _forward(apps: Any, schema_editor: Any) -> None:
    FeatureSwitch = apps.get_model("core", "FeatureSwitch")
    FeatureSwitch.objects.get_or_create(feature_key=FEATURE_KEY, defaults={"state": "hidden"})


def _reverse(apps: Any, schema_editor: Any) -> None:
    FeatureSwitch = apps.get_model("core", "FeatureSwitch")
    FeatureSwitch.objects.filter(feature_key=FEATURE_KEY).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0117_siteconfiguration_door_code"),
    ]

    operations = [
        migrations.RunPython(_forward, _reverse),
    ]
