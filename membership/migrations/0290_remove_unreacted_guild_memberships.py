"""Remove the guild memberships a member left by taking their emoji off (#1319).

Until #1319 an un-react kept the membership and set ``no_discord_role``. Now an un-react removes
it, and nothing reads the mark any more, so a marked row would sit on the roster with its role
given back by the daily sync. Production had no marked rows when this was written; this covers
any marked after #1313 deployed and before this did. Data only, no schema change. The reverse
is a noop (Felix approved it on the PR, 2026-11-23, STANDARDS.md): the rows are gone, and a removed membership
is exactly the state an un-react now produces, so there is nothing to put back.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def remove_marked(apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    """Delete every membership marked ``no_discord_role``."""
    GuildMembership = apps.get_model("membership", "GuildMembership")
    GuildMembership.objects.filter(no_discord_role=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0289_guildmembership_no_discord_role"),
    ]

    operations = [
        migrations.RunPython(remove_marked, migrations.RunPython.noop),
    ]
