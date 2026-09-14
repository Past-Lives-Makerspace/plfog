"""Name every provider the video field takes in the help text members read (#368 item 8).

``video_url`` now accepts Instagram and Facebook links alongside YouTube, so the help
text under the field stops promising YouTube only. Help text only: no column changes,
and ``AlterField`` reverses itself back to the old wording.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0063_teach_page_no_free_option"),
    ]

    operations = [
        migrations.AlterField(
            model_name="classoffering",
            name="video_url",
            field=models.URLField(
                blank=True,
                help_text="Optional video link from YouTube, Instagram, or Facebook. A YouTube link plays right on the public class page; an Instagram or Facebook link shows a card that opens the video on their site.",
                max_length=500,
            ),
        ),
    ]
