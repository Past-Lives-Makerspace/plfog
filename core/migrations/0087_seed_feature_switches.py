"""Carry the three standalone feature booleans onto FeatureSwitch rows, and seed the rest ON.

This is the migration that makes the deploy a no-op. ``wiki_enabled``,
``equipment_page_enabled`` and ``host_a_workshop_enabled`` are the only three of the seven
features that had a switch at all; their current values become ON or HIDDEN, and the four that
never had one are seeded ON. 0088 drops the columns immediately after, so this must not be
squashed past that point.

The reverse copies the states back onto the booleans and removes the rows, so the pair
0087+0088 is genuinely reversible — which is the only thing standing between a bad deploy and
three silently defaulted switches.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

# feature key → the SiteConfiguration boolean it folds in, and that boolean's own default for a
# database that has no singleton row yet (a fresh install; wiki has always shipped off).
_FOLDED: dict[str, tuple[str, bool]] = {
    "wiki": ("wiki_enabled", False),
    "equipment": ("equipment_page_enabled", True),
    "teach": ("host_a_workshop_enabled", True),
}
# The four that never had a switch. Seeded ON, so nothing about them changes on deploy.
_NEW: tuple[str, ...] = ("meetings", "directory", "spaces", "voting")


def _forward(apps: Any, schema_editor: Any) -> None:
    SiteConfiguration = apps.get_model("core", "SiteConfiguration")
    FeatureSwitch = apps.get_model("core", "FeatureSwitch")

    config = SiteConfiguration.objects.first()
    for key, (field_name, field_default) in _FOLDED.items():
        enabled = getattr(config, field_name) if config is not None else field_default
        FeatureSwitch.objects.update_or_create(feature_key=key, defaults={"state": "on" if enabled else "hidden"})
    for key in _NEW:
        FeatureSwitch.objects.update_or_create(feature_key=key, defaults={"state": "on"})


def _reverse(apps: Any, schema_editor: Any) -> None:
    SiteConfiguration = apps.get_model("core", "SiteConfiguration")
    FeatureSwitch = apps.get_model("core", "FeatureSwitch")

    config = SiteConfiguration.objects.first()
    if config is not None:
        for key, (field_name, field_default) in _FOLDED.items():
            row = FeatureSwitch.objects.filter(feature_key=key).first()
            # Coming soon has no boolean equivalent; it is an off state, so it goes back as off.
            setattr(config, field_name, row.state == "on" if row is not None else field_default)
        config.save(update_fields=[field for field, _ in _FOLDED.values()])
    FeatureSwitch.objects.filter(feature_key__in=[*_FOLDED, *_NEW]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0086_featureswitch"),
    ]

    operations = [
        migrations.RunPython(_forward, _reverse),
    ]
