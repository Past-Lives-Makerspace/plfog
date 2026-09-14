"""Name every provider the video field takes in the help text members read (#368 item 8).

``video_url`` now accepts Instagram and Facebook links alongside YouTube, so the help
text under the field stops promising YouTube only. Help text only: no column changes,
and ``AlterField`` reverses itself back to the old wording.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0172_orientation_external_signup_url"),
    ]

    operations = [
        migrations.AlterField(
            model_name="guildfaqitem",
            name="video_url",
            field=models.URLField(
                blank=True,
                default="",
                help_text="Optional video link shown with this answer, from YouTube, Instagram, or Facebook. A YouTube link plays here; an Instagram or Facebook link shows a card that opens the video on their site.",
            ),
        ),
        migrations.AlterField(
            model_name="orgfaqitem",
            name="video_url",
            field=models.URLField(
                blank=True,
                default="",
                help_text="Optional video link shown with this answer, from YouTube, Instagram, or Facebook. A YouTube link plays here; an Instagram or Facebook link shows a card that opens the video on their site.",
            ),
        ),
    ]
