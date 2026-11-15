"""Forget SiteConfiguration.instructor_discount_codes_need_approval (#1185) in Django's state only.

The column stays for one release because the previous release, still serving while this deploy
migrates (STANDARDS.md section 10), reads it. It is NOT NULL with no database default, so the
database half gives it one (false): the new code inserts SiteConfiguration rows without naming
the column. The follow-up to #1185 drops the column with RunSQL once this release
is live.

The master setting's help text drops "request" and "approve" along with it.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0140_discord_member_role_help"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="siteconfiguration", name="instructor_discount_codes_need_approval"),
            ],
            database_operations=[
                migrations.AlterField(
                    model_name="siteconfiguration",
                    name="instructor_discount_codes_need_approval",
                    field=models.BooleanField(
                        default=True,
                        db_default=False,
                        verbose_name="Instructors request discount codes and an admin approves them",
                        help_text="Retired by #1185; the column is dropped in its follow-up.",
                    ),
                ),
            ],
        ),
        migrations.AlterField(
            model_name="siteconfiguration",
            name="instructor_discount_codes_enabled",
            field=models.BooleanField(
                default=False,
                verbose_name="Allow instructors to manage their own discount codes",
                help_text=(
                    "When off, instructors can no longer create, edit or delete their own discount codes "
                    "from the Teaching portal; those pages redirect. Instructors still see their personal "
                    "code and the codes that work on their classes either way. Admins can always create and "
                    "approve discount codes from Classes admin. Default off: only admins create discount codes."
                ),
            ),
        ),
    ]
