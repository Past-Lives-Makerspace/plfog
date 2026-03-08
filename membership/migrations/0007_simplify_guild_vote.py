"""Remove Airtable-specific fields from GuildVote; make member/session non-nullable."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0006_guild_voting_system"),
    ]

    operations = [
        # 1. Drop old constraints (they reference member_airtable_id)
        migrations.RemoveConstraint(
            model_name="guildvote",
            name="unique_session_member_priority",
        ),
        migrations.RemoveConstraint(
            model_name="guildvote",
            name="unique_session_member_guild",
        ),
        # 2. Remove Airtable-specific fields
        migrations.RemoveField(
            model_name="guildvote",
            name="member_airtable_id",
        ),
        migrations.RemoveField(
            model_name="guildvote",
            name="member_name",
        ),
        migrations.RemoveField(
            model_name="guildvote",
            name="airtable_record_id",
        ),
        # 3. Make member non-nullable (no existing rows in prod)
        migrations.AlterField(
            model_name="guildvote",
            name="member",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="guild_votes",
                to="membership.member",
            ),
        ),
        # 4. Make session non-nullable
        migrations.AlterField(
            model_name="guildvote",
            name="session",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="votes",
                to="membership.votingsession",
            ),
        ),
        # 5. Update ordering
        migrations.AlterModelOptions(
            name="guildvote",
            options={
                "ordering": ["session", "member", "priority"],
                "verbose_name": "Guild Vote",
                "verbose_name_plural": "Guild Votes",
            },
        ),
        # 6. Add new constraints using member FK
        migrations.AddConstraint(
            model_name="guildvote",
            constraint=models.UniqueConstraint(
                fields=("session", "member", "priority"),
                name="unique_session_member_priority",
            ),
        ),
        migrations.AddConstraint(
            model_name="guildvote",
            constraint=models.UniqueConstraint(
                fields=("session", "member", "guild"),
                name="unique_session_member_guild",
            ),
        ),
    ]
