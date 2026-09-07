from __future__ import annotations

from django.db import migrations

# Facility marker label -> a fragment that uniquely matches the owning guild's name.
# Only markers with an obvious guild home are linked; the rest (bathrooms, stairs,
# loading bay, etc.) stay unlinked. The space-detail modal already renders a "Go to
# <guild>" link whenever a marker has a guild, so this is purely data.
FACILITY_GUILD = {
    "Wood Shop": "Woodworking",
    "Metal Shop": "Metalworkers",
    "Stage": "Events",
    "Ceramics": "Ceramics",
    "Gallery": "Visual Arts",
    "Leather": "Leatherwork",
    "Garden Guild": "Gardeners",
}


def _guild_for(Guild, fragment):
    return Guild.objects.filter(name__icontains=fragment, deleted_at__isnull=True).first()


def link_facility_guilds(apps, schema_editor):
    MapHotspot = apps.get_model("membership", "MapHotspot")
    Guild = apps.get_model("membership", "Guild")
    for label, fragment in FACILITY_GUILD.items():
        guild = _guild_for(Guild, fragment)
        if guild is None:
            continue
        # Only fill markers that have no guild yet — never clobber a hand-set link.
        MapHotspot.objects.filter(label=label, kind="facility", guild__isnull=True).update(guild=guild)


def unlink_facility_guilds(apps, schema_editor):
    # A stateless data migration can't know which rows it actually set, so on rollback this
    # also clears a link that was hand-set to the same guild before the migration ran. That
    # edge only bites on a manual reverse and is acceptable for this facility mapping.
    MapHotspot = apps.get_model("membership", "MapHotspot")
    Guild = apps.get_model("membership", "Guild")
    for label, fragment in FACILITY_GUILD.items():
        guild = _guild_for(Guild, fragment)
        if guild is None:
            continue
        MapHotspot.objects.filter(label=label, kind="facility", guild=guild).update(guild=None)


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0147_alter_votepreference_guild_2nd_and_more"),
    ]

    operations = [
        migrations.RunPython(link_facility_guilds, unlink_facility_guilds),
    ]
