"""Forget Member.can_self_approve_discounts (#1185) in Django's state only.

The column stays for one release because the previous release, still serving while this deploy
migrates (STANDARDS.md section 10), reads and writes it. It is NOT NULL with no database
default, so the database half gives it one (false): the new code inserts Member rows without
naming the column. The follow-up to #1185 drops the column with RunSQL once this
release is live.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0283_orientavail_every_n_weeks_last"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="member", name="can_self_approve_discounts"),
            ],
            database_operations=[
                migrations.AlterField(
                    model_name="member",
                    name="can_self_approve_discounts",
                    field=models.BooleanField(
                        default=False,
                        db_default=False,
                        help_text="Retired by #1185; the column is dropped in its follow-up.",
                    ),
                ),
            ],
        ),
    ]
