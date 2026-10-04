"""Hide the app store review account from every member facing list (#614).

Apple and Google reviewers sign in as appreview@pastlives.app, so the account stays an
ACTIVE member that can log in; it just never shows up next to real people. Forward sets the
ops override (``hide_from_directory``) and clears the member's own opt in. Reverse puts back
the values production held before this migration (listed, no override). Where the account
does not exist both directions change nothing.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

APP_REVIEW_EMAIL = "appreview@pastlives.app"


def _review_members(apps: Any) -> Any:
    member_model = apps.get_model("membership", "Member")
    return member_model.objects.filter(user__email__iexact=APP_REVIEW_EMAIL)


def hide_app_review_account(apps: Any, schema_editor: Any) -> None:
    _review_members(apps).update(hide_from_directory=True, show_in_directory=False)


def show_app_review_account(apps: Any, schema_editor: Any) -> None:
    _review_members(apps).update(hide_from_directory=False, show_in_directory=True)


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0203_guild_show_reservations_tab"),
    ]

    operations = [
        migrations.RunPython(hide_app_review_account, show_app_review_account),
    ]
