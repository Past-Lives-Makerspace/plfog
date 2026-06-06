from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0022_instructor_message_nullable_instructor_sent_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="classoffering",
            name="video_url",
            field=models.URLField(
                blank=True,
                max_length=500,
                help_text="Optional YouTube link (watch, youtu.be, embed, or shorts URL). Embeds on the public class page.",
            ),
        ),
    ]
