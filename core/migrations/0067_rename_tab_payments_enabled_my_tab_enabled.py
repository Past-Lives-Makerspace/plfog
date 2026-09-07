# Rename (not drop+add) so the stored prod value survives: the flag now scopes
# member-facing My Tab surfaces only; the admin Payments/Reports pages are
# permission-gated and no longer feature-toggled. RenameField is auto-reversible.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0066_calendar_channel_rename_help_text"),
    ]

    operations = [
        migrations.RenameField(
            model_name="siteconfiguration",
            old_name="tab_payments_enabled",
            new_name="my_tab_enabled",
        ),
        migrations.AlterField(
            model_name="siteconfiguration",
            name="my_tab_enabled",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "When off, hides the member My Tab pages, the balance pill, and the Buyables tab "
                    "on guild pages. Members visiting the Tab pages are redirected. The admin Payments and "
                    "Reports pages are always available to billing admins."
                ),
                verbose_name="Enable My Tab",
            ),
        ),
    ]
