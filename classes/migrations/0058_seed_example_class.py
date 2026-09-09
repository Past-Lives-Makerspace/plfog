"""Point the Teach at Past Lives worked example at the Shaker side table class.

The example class is a hand-created production row with no seed command and no fixed
pk, so it is matched by slug and skipped entirely when that row is absent (a fresh
database, a test database, any site that never imported it). ``ClassSettings`` is a
singleton created on first ``load()``, so this only writes when a row already exists;
nothing here creates one.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

EXAMPLE_SLUG = "shaker-side-table-hand-cut-joinery"


def _point_at_the_example(apps: Any, _schema_editor: Any) -> None:
    ClassSettings = apps.get_model("classes", "ClassSettings")
    ClassOffering = apps.get_model("classes", "ClassOffering")
    example = ClassOffering.objects.filter(slug=EXAMPLE_SLUG).first()
    if example is None:
        return
    ClassSettings.objects.filter(example_class__isnull=True).update(example_class=example)


def _clear_the_example(apps: Any, _schema_editor: Any) -> None:
    ClassSettings = apps.get_model("classes", "ClassSettings")
    ClassSettings.objects.update(example_class=None)


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0057_classsettings_example_class"),
    ]

    operations = [
        migrations.RunPython(_point_at_the_example, _clear_the_example),
    ]
