"""Carry the standalone My Tab boolean onto a FeatureSwitch row (#416).

My Tab joins the ``core.features`` registry as its one functional switch. The boolean's value
becomes the row's state: True is ON, False is HIDDEN (the off state that matches today's
sidebar, which shows no My Tab entry while the boolean is off). 0103 drops the column right
after, so this must not be squashed past that point, the same pairing as 0087 and 0088.

The reverse copies the state back onto the boolean (only ON is True; Coming soon is an off state)
and removes the row, so 0102+0103 is reversible.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

_KEY = "my_tab"
_FIELD = "my_tab_enabled"
# The boolean's own default, for a database with no singleton row yet (a fresh install).
_FIELD_DEFAULT = True


def _forward(apps: Any, schema_editor: Any) -> None:
    SiteConfiguration = apps.get_model("core", "SiteConfiguration")
    FeatureSwitch = apps.get_model("core", "FeatureSwitch")

    config = SiteConfiguration.objects.first()
    enabled = getattr(config, _FIELD) if config is not None else _FIELD_DEFAULT
    FeatureSwitch.objects.update_or_create(feature_key=_KEY, defaults={"state": "on" if enabled else "hidden"})


def _reverse(apps: Any, schema_editor: Any) -> None:
    SiteConfiguration = apps.get_model("core", "SiteConfiguration")
    FeatureSwitch = apps.get_model("core", "FeatureSwitch")

    config = SiteConfiguration.objects.first()
    if config is not None:
        row = FeatureSwitch.objects.filter(feature_key=_KEY).first()
        setattr(config, _FIELD, row.state == "on" if row is not None else _FIELD_DEFAULT)
        config.save(update_fields=[_FIELD])
    FeatureSwitch.objects.filter(feature_key=_KEY).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0101_retire_community_wording"),
    ]

    operations = [
        migrations.RunPython(_forward, _reverse),
    ]
