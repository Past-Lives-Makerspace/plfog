import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0021_discount_code_approval"),
        ("membership", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="instructormessage",
            name="instructor",
            field=models.ForeignKey(
                blank=True,
                help_text="The instructor who sent this, or NULL if sent by an admin.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sent_messages",
                to="classes.instructor",
            ),
        ),
        migrations.AddField(
            model_name="instructormessage",
            name="sent_by",
            field=models.ForeignKey(
                blank=True,
                help_text="The user who actually sent the message (admin or instructor).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sent_class_messages",
                to="membership.member",
            ),
        ),
    ]
