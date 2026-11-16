"""Stamp each guild lead gate with the guild's lead when it opened (#1183).

A guild lead's approval publishes the class, so an emailed review link decides signed out only
while the person it was sent to still leads the guild. New gates are stamped in
``ClassApproval.save``; this backfills the undecided ones already open, from their guild's
current lead, so a link sent before the deploy keeps working. Decided rows are history and
stay unstamped. A gate the previous release opens while this deploy migrates is unstamped too,
and its link then works signed in only. The reverse clears the stamp, before the column goes.
"""

import django.db.models.deletion
from django.db import migrations, models


def stamp_open_gates(apps, schema_editor) -> None:
    ClassApproval = apps.get_model("classes", "ClassApproval")
    gates = ClassApproval.objects.filter(role="guild_lead", decision="", opened_for_guild__isnull=False)
    for gate in gates.select_related("opened_for_guild"):
        ClassApproval.objects.filter(pk=gate.pk).update(opened_for_lead_id=gate.opened_for_guild.guild_lead_id)


def clear_stamps(apps, schema_editor) -> None:
    apps.get_model("classes", "ClassApproval").objects.update(opened_for_lead=None)


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0104_remove_classsettings_instructor_approval_required_state"),
        ("membership", "0286_rewrite_class_review_guides"),
    ]

    operations = [
        migrations.AddField(
            model_name="classapproval",
            name="opened_for_lead",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "The guild's lead when this gate was opened, stamped when the row is created. The emailed "
                    "link works signed out only while that person is still the guild's lead, so a link held by "
                    "a replaced lead, or one whose account was deleted, decides nothing. Null on admin rows and "
                    "on rows predating the field."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="membership.member",
            ),
        ),
        migrations.RunPython(stamp_open_gates, clear_stamps),
    ]
