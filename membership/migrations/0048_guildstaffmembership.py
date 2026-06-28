import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0047_guild_orienters"),
    ]

    operations = [
        migrations.CreateModel(
            name="GuildStaffMembership",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("co_lead", "Co-Guild Lead"),
                            ("secretary", "Secretary"),
                            ("treasurer", "Treasurer"),
                            ("orienter", "Orienter"),
                        ],
                        help_text="The staff role held — every role carries full guild-lead permissions.",
                        max_length=20,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, help_text="When this role was assigned."),
                ),
                (
                    "guild",
                    models.ForeignKey(
                        help_text="The guild this staff role applies to.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="staff_memberships",
                        to="membership.guild",
                    ),
                ),
                (
                    "member",
                    models.ForeignKey(
                        help_text="The member holding the staff role.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="guild_staff_roles",
                        to="membership.member",
                    ),
                ),
            ],
            options={
                "ordering": ["role", "member__full_legal_name"],
            },
        ),
        migrations.AddConstraint(
            model_name="guildstaffmembership",
            constraint=models.UniqueConstraint(
                fields=("guild", "member", "role"),
                name="uq_guildstaff_guild_member_role",
            ),
        ),
    ]
