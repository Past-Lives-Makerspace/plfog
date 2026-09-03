# Data migration: pronouns became free text in 0153. The old choice list included a
# "prefer not to share" sentinel; with free text, "not sharing" is expressed by leaving
# the field blank (plus the existing directory_visibility["pronouns"] toggle), so this
# maps every stored sentinel to "".
from __future__ import annotations

from django.db import migrations

_SENTINEL = "prefer not to share"


def blank_prefer_not_to_share(apps, schema_editor) -> None:
    """Blank every pronouns value that stored the old 'prefer not to share' sentinel."""
    Member = apps.get_model("membership", "Member")
    Member.objects.filter(pronouns=_SENTINEL).update(pronouns="")


def leave_blanked_pronouns(apps, schema_editor) -> None:
    """Reverse: deliberately leave the blanked pronouns as-is.

    The forward pass is a one-way policy cleanup: the 'prefer not to share' sentinel
    only made sense as a choice-list option, and after 0153 the field is free text.
    Restoring the literal string 'prefer not to share' into members' free-text pronouns
    on rollback would be wrong (it would display as if it were their pronouns), and the
    set of rows that were blanked is indistinguishable from members who were already
    blank. Rolling back 0153 re-imposes the choice list at the schema level; blank
    remains a valid value there, so no data restoration is needed for a clean rollback.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0153_alter_member_pronouns"),
    ]

    operations = [
        migrations.RunPython(blank_prefer_not_to_share, leave_blanked_pronouns),
    ]
