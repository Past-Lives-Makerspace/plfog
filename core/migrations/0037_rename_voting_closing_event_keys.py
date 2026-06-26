"""Rename the repurposed voting-closing event key on admin-edited copy + opt-outs.

``voting.closing_48h`` becomes ``voting.closing_soon`` (the "48h" was wrong once the
reminder lead is configurable). The key is stored on ``NotificationTemplate``
(admin-edited copy) and ``NotificationPreference`` (per-user email opt-outs), so both
are renamed here to preserve any override and any member's opt-out. Reverse renames
back. ``EventDelivery`` rows for the old key are an inert ledger (old periods) and are
left as-is — new sends use the new key.
"""

from django.db import migrations

OLD_KEY = "voting.closing_48h"
NEW_KEY = "voting.closing_soon"


def _rename(apps, old, new):
    NotificationTemplate = apps.get_model("core", "NotificationTemplate")
    NotificationPreference = apps.get_model("core", "NotificationPreference")
    NotificationTemplate.objects.filter(event_key=old).update(event_key=new)
    NotificationPreference.objects.filter(event_key=old).update(event_key=new)


def rename_forward(apps, schema_editor):
    _rename(apps, OLD_KEY, NEW_KEY)


def rename_backward(apps, schema_editor):
    _rename(apps, NEW_KEY, OLD_KEY)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_invite_last_sent_at_alter_siteactivity_kind"),
    ]

    operations = [
        migrations.RunPython(rename_forward, rename_backward),
    ]
