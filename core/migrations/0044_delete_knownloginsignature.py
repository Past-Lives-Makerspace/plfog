from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0043_remove_signage_alert_fields"),
    ]

    operations = [
        migrations.DeleteModel(name="KnownLoginSignature"),
    ]
