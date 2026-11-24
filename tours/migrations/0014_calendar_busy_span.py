"""Google busy times as rows (#1300, part 2): ``CalendarBusySpan``, filled from each connection's JSON cache.

From here every busy read writes both; the copy below fills the rows for connections read before
it, so no busy time goes missing until the next read. The reverse deletes the copies.
"""

from datetime import datetime

import django.db.models.deletion
from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def copy_busy(apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    CalendarBusySpan = apps.get_model("tours", "CalendarBusySpan")
    TourCalendarConnection = apps.get_model("tours", "TourCalendarConnection")
    CalendarBusySpan.objects.bulk_create(
        CalendarBusySpan(
            connection=connection,
            starts_at=datetime.fromisoformat(start),
            ends_at=datetime.fromisoformat(end),
        )
        for connection in TourCalendarConnection.objects.all()
        for start, end in connection.busy_spans
    )


def delete_copies(apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    apps.get_model("tours", "CalendarBusySpan").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tours", "0013_tourhours_hours"),
    ]

    operations = [
        migrations.CreateModel(
            name="CalendarBusySpan",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "starts_at",
                    models.DateTimeField(help_text="When the busy time starts."),
                ),
                ("ends_at", models.DateTimeField(help_text="When the busy time ends.")),
                (
                    "connection",
                    models.ForeignKey(
                        help_text="The Google Calendar connection that shows this busy time.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="busy_rows",
                        to="tours.tourcalendarconnection",
                    ),
                ),
            ],
            options={
                "ordering": ["starts_at"],
                "indexes": [
                    models.Index(
                        fields=["connection", "starts_at"],
                        name="ix_busyspan_conn_start",
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("ends_at__gt", models.F("starts_at"))),
                        name="ck_busyspan_ends_after_start",
                    )
                ],
            },
        ),
        migrations.RunPython(copy_busy, delete_copies),
    ]
