"""Add ``overridden_by_admin`` to the reviewer decisions a ClassApproval row can carry.

The lane an admin shuts by publishing over it is now marked overridden rather than
approved, so nothing renders a yes the guild lead never gave.

Choices only: ``AlterField`` on a ``choices`` change emits no DDL, the old code serving
during the deploy cannot see it, and it reverses itself. **No data migration belongs in
this release.** Render runs ``manage.py migrate`` inside ``buildCommand`` and only starts
gunicorn once the build succeeds (``render.yaml``), so the OLD code is still answering
requests while migrations run. A data migration writing ``overridden_by_admin`` would hand
that old ``_pipeline_detail`` a value its verb lookup cannot parse — ``Decision(row.decision)``
raises ``ValueError`` before the dict is even indexed — for the length of a build. The one
historical row is corrected afterwards by the ``backfill_override_decisions`` management
command, run as a one-off job once the new code is serving.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0064_video_provider_help_text"),
    ]

    operations = [
        migrations.AlterField(
            model_name="classapproval",
            name="decision",
            field=models.CharField(
                blank=True,
                choices=[
                    ("approved", "Approved"),
                    ("changes_requested", "Changes Requested"),
                    ("denied", "Denied"),
                    ("overridden_by_admin", "Overridden by Admin"),
                ],
                default="",
                help_text="Reviewer's verdict; empty means still pending.",
                max_length=20,
            ),
        ),
    ]
