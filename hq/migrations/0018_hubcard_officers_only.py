"""The HQ opens to admins and guild officers alone (#1307): a card has no audience, Can open list or Team any more.

Drops the ``audience`` column and both people tables: ``hq_hubcard_can_open`` and the Team's
``hq_hubcard_named_members`` (pinned by 0013, so Django drops it by that name). The release still
serving during the deploy reads these on HQ pages and may answer a 500 there for under a minute (the
#1091 precedent). The reverse brings the column back as Admins only and both tables empty.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("hq", "0017_remove_vendor_sheet_pull"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="hubcard",
            name="audience",
        ),
        migrations.RemoveField(
            model_name="hubcard",
            name="can_open",
        ),
        migrations.RemoveField(
            model_name="hubcard",
            name="team",
        ),
    ]
