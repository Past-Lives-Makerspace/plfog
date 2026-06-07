"""Add temp integer bridge columns for the Instructor→Member FK remapping."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0029_merge_20260606_1846"),
        ("membership", "0032_member_instructor_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="classoffering",
            name="temp_instructor_pk",
            field=models.IntegerField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="classoffering",
            name="temp_created_by_pk",
            field=models.IntegerField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="instructormessage",
            name="temp_instructor_pk",
            field=models.IntegerField(null=True, blank=True),
        ),
    ]
