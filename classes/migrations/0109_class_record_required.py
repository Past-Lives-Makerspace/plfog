"""Every run belongs to a class (#1260, part 6): ``ClassOffering.class_record`` becomes NOT NULL.

Production had no run without a class on 2026-11-20 (every save files its run under one). No
default is given, so a run with no class makes the database refuse the change and the migration
stops; nothing is ever filed under a made-up class.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0108_class_record_states"),
    ]

    operations = [
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
