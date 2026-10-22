"""HQ card sharing: the named members become the Team, and a card gains a Can open list.

The named members are renamed to ``team`` in place rather than copied: the field first pins its
table to the name it already has (``hq_hubcard_named_members``), so the rename moves no row and
renames no table. Every person named on a card today is on its Team after this migration, and the
release still serving while it applies keeps reading the same table. ``can_open`` is a new, empty
list. The reverse undoes each step, so the Team goes back to being the named members.
"""

from django.db import migrations, models

NAMED_TABLE = "hq_hubcard_named_members"


class Migration(migrations.Migration):
    dependencies = [
        ("hq", "0012_ledger_tally"),
        ("membership", "0238_eventrsvp_guests"),
    ]

    operations = [
        # Pin the table to the name it already has: no change in the database.
        migrations.AlterField(
            model_name="hubcard",
            name="named_members",
            field=models.ManyToManyField(
                blank=True,
                db_table=NAMED_TABLE,
                help_text="People who may open this card whatever its audience, and who organise what is behind it.",
                related_name="hq_cards",
                to="membership.member",
            ),
        ),
        migrations.RenameField(model_name="hubcard", old_name="named_members", new_name="team"),
        migrations.AlterField(
            model_name="hubcard",
            name="team",
            field=models.ManyToManyField(
                blank=True,
                db_table=NAMED_TABLE,
                help_text="People who may open this card and do its organiser work: add and edit rows, read "
                "reports, download the CSVs. Settings and uploads stay with admins.",
                related_name="hq_team_cards",
                to="membership.member",
            ),
        ),
        migrations.AddField(
            model_name="hubcard",
            name="can_open",
            field=models.ManyToManyField(
                blank=True,
                help_text="People who may open this card and use it as a member would, whatever its audience.",
                related_name="hq_open_cards",
                to="membership.member",
            ),
        ),
        migrations.AlterField(
            model_name="hubcard",
            name="audience",
            field=models.CharField(
                choices=[("admins", "Admins only"), ("members", "Members"), ("public", "Everyone, signed in or not")],
                default="admins",
                help_text="Who may see and open the card. Admins, the Can open people and the Team always can.",
                max_length=20,
            ),
        ),
    ]
