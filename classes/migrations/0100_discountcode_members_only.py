"""Discount codes can be Members only, and MEMBER10 is (#1072).

Adds ``DiscountCode.members_only`` (off, with a database default so the release before this one
can still insert codes while this migrates) and switches it on for the code MEMBER10, the code
that replaced the automatic member discount (#557). No other code is touched, and nothing
happens when there is no MEMBER10. The reverse switches MEMBER10 back off before the column
goes, so stepping back and forward again leaves the same rows.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations, models

MEMBER_CODE = "MEMBER10"


def _set_member_code(apps: Any, members_only: bool) -> None:
    DiscountCode = apps.get_model("classes", "DiscountCode")
    DiscountCode.objects.filter(code=MEMBER_CODE).update(members_only=members_only)


def forwards(apps: Any, schema_editor: Any) -> None:
    _set_member_code(apps, True)


def backwards(apps: Any, schema_editor: Any) -> None:
    _set_member_code(apps, False)


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0099_backfill_personal_discount_codes"),
    ]

    operations = [
        migrations.AddField(
            model_name="discountcode",
            name="members_only",
            field=models.BooleanField(
                db_default=False,
                default=False,
                help_text="Members only (#1072): checkout takes this code only from a signed in member whose status is Active. Anyone else is told to sign in and pays full price, and it never auto applies to them.",
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
