"""Fold the standalone ``Guild.orienters`` M2M into ``GuildStaffMembership``.

Orienters become a staff role (``role="orienter"``) so they're managed alongside
co-leads, secretaries, and treasurers on the guild's Staff tab. Every staff role now
carries full guild-lead permissions, so an orienter gains the lead's edit authority.
"""

from django.db import migrations


def copy_orienters_to_staff(apps, schema_editor):
    """Create an ``orienter`` staff row for each existing ``Guild.orienters`` link."""
    Guild = apps.get_model("membership", "Guild")
    GuildStaffMembership = apps.get_model("membership", "GuildStaffMembership")
    for guild in Guild.objects.all():
        for member in guild.orienters.all():
            GuildStaffMembership.objects.get_or_create(guild=guild, member=member, role="orienter")


def restore_orienters_from_staff(apps, schema_editor):
    """Reverse: re-populate the ``Guild.orienters`` M2M from the orienter staff rows."""
    GuildStaffMembership = apps.get_model("membership", "GuildStaffMembership")
    for staff in GuildStaffMembership.objects.filter(role="orienter").select_related("guild", "member"):
        staff.guild.orienters.add(staff.member)


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0048_guildstaffmembership"),
    ]

    operations = [
        migrations.RunPython(copy_orienters_to_staff, restore_orienters_from_staff),
        migrations.RemoveField(
            model_name="guild",
            name="orienters",
        ),
    ]
