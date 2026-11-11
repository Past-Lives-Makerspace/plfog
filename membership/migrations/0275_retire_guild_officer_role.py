"""Retire the Guild Officer role (ADR 0004).

``guild_officer`` was a hand-set, cross-guild grant that edited every guild and class and
carried Django ``is_staff``. It had drifted onto nearly every guild lead and Co-Lead, so a lead
could change other guilds' settings. A guild officer is now whoever leads or staffs a guild,
derived from those rows, and the only cross-guild tier is admin.

The data step first records each holder in ``RetiredGuildOfficer`` with whether their login
had ``is_staff``, then moves them to ``member`` and takes that ``is_staff`` away, as
``Member.sync_user_permissions`` now does for a member. A superuser's flags are left alone here;
their next sign-in syncs them from the role, as it always has. Their guild authority is
unchanged: it comes from their lead and staff rows. The reverse reads the record and puts the
role and ``is_staff`` back.
"""

from __future__ import annotations

from typing import Any

import django.db.models.deletion
from django.db import migrations, models

RETIRED_ROLE = "guild_officer"


def retire_guild_officers(apps: Any, schema_editor: Any) -> None:
    """Record every Guild Officer, make them a member and drop the ``is_staff`` the role gave their login."""
    member_model = apps.get_model("membership", "Member")
    record_model = apps.get_model("membership", "RetiredGuildOfficer")
    user_model = apps.get_model("auth", "User")

    officers = member_model.objects.filter(fog_role=RETIRED_ROLE).select_related("user")
    record_model.objects.bulk_create(
        record_model(member=officer, had_is_staff=bool(officer.user and officer.user.is_staff)) for officer in officers
    )
    user_ids = list(officers.filter(user__isnull=False).values_list("user_id", flat=True))
    user_model.objects.filter(pk__in=user_ids, is_superuser=False).update(is_staff=False)
    officers.update(fog_role="member")


def restore_guild_officers(apps: Any, schema_editor: Any) -> None:
    """Put the role back on every recorded member, and ``is_staff`` on the logins that had it."""
    member_model = apps.get_model("membership", "Member")
    record_model = apps.get_model("membership", "RetiredGuildOfficer")
    user_model = apps.get_model("auth", "User")

    records = record_model.objects.all()
    # Only those still plain members: someone made an admin since keeps that.
    member_model.objects.filter(pk__in=records.values("member_id"), fog_role="member").update(fog_role=RETIRED_ROLE)
    staff_user_ids = member_model.objects.filter(
        pk__in=records.filter(had_is_staff=True).values("member_id"), user__isnull=False
    ).values("user_id")
    user_model.objects.filter(pk__in=staff_user_ids).update(is_staff=True)
    records.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("membership", "0274_guild_discord_lead_role_id"),
    ]

    operations = [
        migrations.CreateModel(
            name="RetiredGuildOfficer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "had_is_staff",
                    models.BooleanField(
                        default=False,
                        help_text="Whether their login had is_staff then; the reverse gives it back only to those who did.",
                    ),
                ),
                ("retired_at", models.DateTimeField(auto_now_add=True, help_text="When migration 0275 recorded them.")),
                (
                    "member",
                    models.OneToOneField(
                        help_text="The member who held the Guild Officer role when migration 0275 retired it.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="membership.member",
                    ),
                ),
            ],
        ),
        migrations.RunPython(retire_guild_officers, restore_guild_officers),
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
                help_text="Ops-only override: never list this member in the member directory, even when their role (admin, guild lead, instructor) would normally force-list them. Used for the Help Center's example/demo accounts. Set via shell/admin, not member-editable.",
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
