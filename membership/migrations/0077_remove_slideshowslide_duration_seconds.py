from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0076_slideshowzone_slideshowslide"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="slideshowslide",
            name="duration_seconds",
        ),
    ]
