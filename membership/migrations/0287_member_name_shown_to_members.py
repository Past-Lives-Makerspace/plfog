"""Add Member.name_shown_to_members: what other members see beside a preferred name.

The column is added with plain ADD COLUMN SQL, which PostgreSQL and SQLite both run in place.
Django's own AddField makes SQLite rebuild the member table from the migration state, and that
state has already forgotten ``can_self_approve_discounts`` (0284) while its column still has to
stay for a release, so a rebuild would drop it. The state half records the same field.
"""

from django.db import migrations, models

FIELD = models.CharField(
    choices=[
        ("preferred", "My preferred name only"),
        ("initial", "My preferred name and my legal first name and last initial"),
        ("full", "My preferred name and my full legal name"),
    ],
    db_default="preferred",
    default="preferred",
    help_text=(
        "What other members see beside a preferred name: nothing (the default), the legal first name and "
        "last initial, or the full legal name. Staff always see both names; no effect without a preferred name."
    ),
    max_length=16,
)


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0286_rewrite_class_review_guides"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(model_name="member", name="name_shown_to_members", field=FIELD),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE membership_member "
                        "ADD COLUMN name_shown_to_members varchar(16) DEFAULT 'preferred' NOT NULL"
                    ),
                    reverse_sql="ALTER TABLE membership_member DROP COLUMN name_shown_to_members",
                ),
            ],
        ),
    ]
