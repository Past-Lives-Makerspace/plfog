"""Drop the vendor sheet pull's columns (#1090): the vendor team manages the list on Vendor Tables alone.

The five ``VendorFloor`` sheet fields and ``VendorTable.sheet_cells`` go in one step, Felix's call on #1090: while
this migrates the release still serving reads them, so the HQ's vendor pages may error until the new release is up.
The reverse puts the columns back empty.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("hq", "0016_vendor_discord_check"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="vendorfloor",
            name="sheet_pull_error",
        ),
        migrations.RemoveField(
            model_name="vendorfloor",
            name="sheet_pull_problems",
        ),
        migrations.RemoveField(
            model_name="vendorfloor",
            name="sheet_pull_summary",
        ),
        migrations.RemoveField(
            model_name="vendorfloor",
            name="sheet_pulled_at",
        ),
        migrations.RemoveField(
            model_name="vendorfloor",
            name="sheet_url",
        ),
        migrations.RemoveField(
            model_name="vendortable",
            name="sheet_cells",
        ),
    ]
