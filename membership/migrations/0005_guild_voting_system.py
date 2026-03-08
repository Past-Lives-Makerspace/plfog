"""Add guild voting system: VotingSession model, Guild.is_active, GuildVote session FK."""

import django.db.models.deletion
from django.db import migrations, models


def backfill_session(apps, schema_editor):
    """Create a default VotingSession and assign it to any existing GuildVotes."""
    GuildVote = apps.get_model("membership", "GuildVote")
    if not GuildVote.objects.exists():
        return
    VotingSession = apps.get_model("membership", "VotingSession")
    session = VotingSession.objects.create(
        name="Legacy",
        open_date="2024-01-01",
        close_date="2024-01-01",
        status="closed",
    )
    GuildVote.objects.filter(session__isnull=True).update(session=session)


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0004_space_sublet_guild"),
    ]

    operations = [
        # 1. Add is_active to Guild
        migrations.AddField(
            model_name="guild",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        # 2. Create VotingSession
        migrations.CreateModel(
            name="VotingSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("open_date", models.DateField()),
                ("close_date", models.DateField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("open", "Open"),
                            ("closed", "Closed"),
                            ("calculated", "Calculated"),
                        ],
                        default="draft",
                        max_length=20,
                    ),
                ),
                ("eligible_member_count", models.PositiveIntegerField(default=0)),
                ("votes_cast", models.PositiveIntegerField(default=0)),
                ("results_summary", models.JSONField(blank=True, default=dict)),
                ("airtable_record_id", models.CharField(blank=True, max_length=50)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Voting Session",
                "verbose_name_plural": "Voting Sessions",
                "ordering": ["-open_date"],
            },
        ),
        # 3. Drop old GuildVote constraints
        migrations.RemoveConstraint(
            model_name="guildvote",
            name="unique_member_priority",
        ),
        migrations.RemoveConstraint(
            model_name="guildvote",
            name="unique_member_guild",
        ),
        # 4. Add session FK (nullable initially)
        migrations.AddField(
            model_name="guildvote",
            name="session",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="votes",
                to="membership.votingsession",
            ),
        ),
        # 5. Add created_at
        migrations.AddField(
            model_name="guildvote",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        # 6. Change guild related_name
        migrations.AlterField(
            model_name="guildvote",
            name="guild",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="guild_votes_received",
                to="membership.guild",
            ),
        ),
        # 7. Backfill session for existing votes
        migrations.RunPython(backfill_session, migrations.RunPython.noop),
        # 8. Make session non-nullable
        migrations.AlterField(
            model_name="guildvote",
            name="session",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="votes",
                to="membership.votingsession",
            ),
        ),
        # 9. Update ordering
        migrations.AlterModelOptions(
            name="guildvote",
            options={
                "ordering": ["session", "member", "priority"],
                "verbose_name": "Guild Vote",
                "verbose_name_plural": "Guild Votes",
            },
        ),
        # 10. Add new constraints
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
