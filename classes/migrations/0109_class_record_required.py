"""Every run belongs to a class (#1260, part 6): ``ClassOffering.class_record`` becomes NOT NULL.

Production had no run without a class on 2026-11-20 (every save files its run under one). The
check below refuses to guess: a run with no class stops the migration with its count, so the
column is never forced onto a made-up class.
"""

from typing import Any

import django.db.models.deletion
from django.db import migrations, models


def refuse_runs_with_no_class(apps: Any, schema_editor: Any) -> None:
    offering_model = apps.get_model("classes", "ClassOffering")
    unfiled = offering_model.objects.filter(class_record__isnull=True).count()
    if unfiled:
        raise RuntimeError(
            f"{unfiled} class run(s) have no class. Save each one (or run refresh_class_states on the "
            "release before this) to file it under a class, then migrate again."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0108_class_record_states"),
    ]

    operations = [
        migrations.RunPython(refuse_runs_with_no_class, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="classoffering",
            name="class_record",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "The class this run belongs to (#1260). Set on first save from a run of the same "
                    "class, or a new class when there is none."
                ),
                on_delete=django.db.models.deletion.PROTECT,
                related_name="runs",
                to="classes.classrecord",
            ),
        ),
    ]
