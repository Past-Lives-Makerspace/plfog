"""Move the stored #important-info links content from "classes and workshops" to "classes".

0106 changed the field's default but left the stored row, so the singleton kept the old line
and no longer matched the default. Only that phrase is swapped, so an admin's other edits to
the post stay; reversing puts it back the same way. The strings are frozen here so later
edits to the model default cannot change history.
"""

from django.db import migrations

_OLD_LINE = "Browse and sign up for upcoming classes and workshops:"
_NEW_LINE = "Browse and sign up for upcoming classes:"


def _swap(apps, old: str, new: str) -> None:
    site_configuration = apps.get_model("core", "SiteConfiguration")
    for config in site_configuration.objects.all():
        if old in config.discord_info_links_content:
            config.discord_info_links_content = config.discord_info_links_content.replace(old, new)
            config.save(update_fields=["discord_info_links_content"])


def _say_classes(apps, schema_editor) -> None:
    """Rewrite the Classes line inside the stored links content."""
    _swap(apps, _OLD_LINE, _NEW_LINE)


def _say_classes_and_workshops(apps, schema_editor) -> None:
    """Reverse: put the old Classes line back."""
    _swap(apps, _NEW_LINE, _OLD_LINE)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0106_host_a_class_copy"),
    ]

    operations = [
        migrations.RunPython(_say_classes, _say_classes_and_workshops),
    ]
