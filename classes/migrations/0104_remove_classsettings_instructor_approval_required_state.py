"""Forget ClassSettings.instructor_approval_required (#1183) in Django's state only.

Nothing read the switch: review was never optional, and since #1183 a class has one review, the
guild lead's or, with no guild lead, an admin's. The column stays for one release because the
previous release, still serving while this deploy migrates (STANDARDS.md section 10), saves it
from the Class Settings form. It is NOT NULL with no database default, so the database half gives
it one (true, what production holds): the new code inserts ClassSettings rows without naming the
column. The follow-up to #1183 drops the column with RunSQL once this release is live.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0103_remove_discountcoderequest_state"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="classsettings", name="instructor_approval_required"),
            ],
            database_operations=[
                migrations.AlterField(
                    model_name="classsettings",
                    name="instructor_approval_required",
                    field=models.BooleanField(
                        default=True,
                        db_default=True,
                        help_text="Retired by #1183; the column is dropped in its follow-up.",
                    ),
                ),
            ],
        ),
    ]
