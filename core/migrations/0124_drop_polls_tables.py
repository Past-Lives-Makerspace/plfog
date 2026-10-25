"""Drop the tables of the polls feature removed in #890 (#913).

The polls app is gone from the code; its tables stayed one release so the release before #890
kept working while it deployed. A JSON backup of every row was taken before this ran. The
``polls`` migration history and content types go too, so nothing points at the removed app.

One way on purpose (Felix, 2026-10-23, #917): with no reverse, a rollback stops with
IrreversibleError rather than reporting success. Recovery is the JSON backup and git history.
"""

from typing import Any

from django.db import migrations


def forget_polls_content_types(apps: Any, schema_editor: Any) -> None:
    """Delete the polls content types; their permissions cascade."""
    apps.get_model("contenttypes", "ContentType").objects.filter(app_label="polls").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0123_scheduledjobstate_cadence"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunSQL("DROP TABLE IF EXISTS polls_pollvote"),
        migrations.RunSQL("DROP TABLE IF EXISTS polls_pollchoice"),
        migrations.RunSQL("DROP TABLE IF EXISTS polls_poll"),
        migrations.RunSQL("DELETE FROM django_migrations WHERE app = 'polls'"),
        migrations.RunPython(forget_polls_content_types),
    ]
