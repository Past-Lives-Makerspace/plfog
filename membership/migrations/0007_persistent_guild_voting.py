"""Replace session-based guild voting with persistent VotePreference and FundingSnapshot."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0006_merge_20260309_1721"),
    ]

    operations = [
        # Remove old constraints before dropping GuildVote
        migrations.RemoveConstraint(
            model_name="guildvote",
            name="unique_session_member_priority",
        ),
        migrations.RemoveConstraint(
            model_name="guildvote",
            name="unique_session_member_guild",
        ),
        # Drop GuildVote then VotingSession
        migrations.DeleteModel(name="GuildVote"),
        migrations.DeleteModel(name="VotingSession"),
        # Create VotePreference
        migrations.CreateModel(
            name="VotePreference",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                (
                    "member",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vote_preference",
                        to="membership.member",
                    ),
                ),
                (
                    "guild_1st",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="first_choice_votes",
                        to="membership.guild",
                    ),
                ),
                (
                    "guild_2nd",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="second_choice_votes",
                        to="membership.guild",
                    ),
                ),
                (
                    "guild_3rd",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="third_choice_votes",
                        to="membership.guild",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Vote Preference",
                "verbose_name_plural": "Vote Preferences",
            },
        ),
        # Create FundingSnapshot
        migrations.CreateModel(
            name="FundingSnapshot",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                ("cycle_label", models.CharField(max_length=100)),
                ("snapshot_at", models.DateTimeField(auto_now_add=True)),
                ("contributor_count", models.PositiveIntegerField()),
                ("funding_pool", models.DecimalField(decimal_places=2, max_digits=10)),
                ("results", models.JSONField(default=dict)),
            ],
            options={
                "verbose_name": "Funding Snapshot",
                "verbose_name_plural": "Funding Snapshots",
                "ordering": ["-snapshot_at"],
            },
        ),
    ]
