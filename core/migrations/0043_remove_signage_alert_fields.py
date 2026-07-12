from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0042_siteconfiguration_signage_alert_active_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="siteconfiguration",
            name="signage_alert_active",
        ),
        migrations.RemoveField(
            model_name="siteconfiguration",
            name="signage_alert_heading",
        ),
        migrations.RemoveField(
            model_name="siteconfiguration",
            name="signage_alert_message",
        ),
    ]
