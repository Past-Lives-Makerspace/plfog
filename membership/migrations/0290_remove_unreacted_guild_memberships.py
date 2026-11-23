"""Remove the guild memberships a member left by taking their emoji off (#1319).

Until #1319 an un-react kept the membership and set ``no_discord_role``. Now an un-react removes
it, and nothing reads the mark any more, so a marked row would sit on the roster with its role
given back by the daily sync. Production had no marked rows when this was written; this covers
any marked after #1313 deployed and before this did. Data only, no schema change. The reverse
does nothing: a removed membership is the state an un-react now produces.
"""

from django.db import migrations


def remove_marked(apps, schema_editor):
    GuildMembership = apps.get_model("membership", "GuildMembership")
    GuildMembership.objects.filter(no_discord_role=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0289_guildmembership_no_discord_role"),
    ]

    operations = [
        migrations.RunPython(remove_marked, migrations.RunPython.noop),
    ]
