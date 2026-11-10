"""Retire the Guild Officer role (ADR 0004).

``guild_officer`` was a hand-set, cross-guild grant that edited every guild and class and
carried Django ``is_staff``. It had drifted onto nearly every guild lead and Co-Lead, so a lead
could change other guilds' settings. A guild officer is now whoever leads or staffs a guild,
derived from those rows, and the only cross-guild tier is admin.

The data step moves every holder to ``member`` and takes away the ``is_staff`` the role gave
their login, as ``Member.sync_user_permissions`` now does for a member, leaving a superuser's
flags alone. Their guild authority is unchanged: it comes from their lead and staff rows. The
reverse is a no-op, because who held the role is not recorded anywhere to put back.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations, models

RETIRED_ROLE = "guild_officer"


def retire_guild_officers(apps: Any, schema_editor: Any) -> None:
    """Make every Guild Officer a member and drop the ``is_staff`` the role gave their login."""
    member_model = apps.get_model("membership", "Member")
    user_model = apps.get_model("auth", "User")

    officers = member_model.objects.filter(fog_role=RETIRED_ROLE)
    user_ids = list(officers.filter(user__isnull=False).values_list("user_id", flat=True))
    user_model.objects.filter(pk__in=user_ids, is_superuser=False).update(is_staff=False)
    officers.update(fog_role="member")


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("membership", "0274_guild_discord_lead_role_id"),
    ]

    operations = [
        migrations.RunPython(retire_guild_officers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="member",
            name="fog_role",
            field=models.CharField(
                choices=[("member", "Member"), ("admin", "Admin")],
                default="member",
                help_text="FOG access level: admin (full access) or member. Guild authority comes from leading or staffing a guild.",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="member",
            name="hide_from_directory",
            field=models.BooleanField(
                default=False,
                help_text="Ops-only override: never list this member in the member directory, even when their role (admin, guild lead or staff, instructor) would normally force-list them. Used for the Help Center's example/demo accounts. Set via shell/admin, not member-editable.",
            ),
        ),
        migrations.AlterField(
            model_name="announcementdraft",
            name="audience",
            field=models.CharField(
                choices=[
                    ("site", "Everyone (site-wide)"),
                    ("guild", "A specific guild"),
                    ("class", "A class you teach"),
                    ("leads", "Guild leads"),
                    ("instructor", "All Instructors"),
                    ("students", "My Past Students"),
                ],
                default="site",
                help_text="Who hears it: everyone site-wide, one guild's joined members, a class's registrants, every guild lead and staffer, every active instructor, or everyone who took a class the sender teaches.",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="wikipage",
            name="admins_only_edit",
            field=models.BooleanField(
                db_default=False,
                default=False,
                help_text="Only admins may edit or moderate this page; everyone else reads it. Unlike Official, a guild's lead and staff cannot archive it either. Set on the Guild Staff Rules and Tools page.",
            ),
        ),
    ]
