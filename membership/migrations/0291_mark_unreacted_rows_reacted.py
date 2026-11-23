"""Treat a membership marked ``no_discord_role`` as one its member reacted for (#1319, #1320).

Until #1319 an un-react kept the membership and set ``no_discord_role``. Nothing reads the mark
any more, so such a row would keep its role. Marking it ``reacted`` hands it to the 15 minute
reaction sync, which removes it on the next complete read unless the member reacts again.
Production had no marked rows when this was written. The reverse clears ``reacted`` on those
same rows.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def mark_reacted(apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    """Set ``reacted`` on every membership marked ``no_discord_role``."""
    GuildMembership = apps.get_model("membership", "GuildMembership")
    GuildMembership.objects.filter(no_discord_role=True).update(reacted=True)


def unmark_reacted(apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    """Clear ``reacted`` on every membership marked ``no_discord_role``."""
    GuildMembership = apps.get_model("membership", "GuildMembership")
    GuildMembership.objects.filter(no_discord_role=True).update(reacted=False)


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0290_guildmembership_reacted"),
    ]

    operations = [
        migrations.RunPython(mark_reacted, unmark_reacted),
    ]
