"""Show every member's skills on their directory card again (#674).

Until #674 the profile form wrote ``directory_visibility["skills"] = False`` on every save,
because Settings had no skills switch to post a value. No member chose hidden, so this sets the
key back to shown and leaves the other six visibility keys exactly as they are. Rows that were
explicitly hidden carry a ``skills_shown_by_0213`` marker so the reverse restores exactly them; the
marker is never read and the next profile save rebuilds the dict without it.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


MARKER = "skills_shown_by_0213"


def show_skills_on_directory_cards(apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    """Set skills shown for every member; mark the rows that were explicitly hidden so reverse can find them.

    A missing key already reads as shown (``Member.is_public``), so it gets True without the marker.
    """
    Member = apps.get_model("membership", "Member")
    for member in Member.objects.only("pk", "directory_visibility").iterator():
        visibility = dict(member.directory_visibility)
        if "skills" not in visibility:
            visibility["skills"] = True
        elif visibility["skills"] is True:
            continue
        else:
            visibility["skills"] = True
            visibility[MARKER] = True
        Member.objects.filter(pk=member.pk).update(directory_visibility=visibility)


def hide_skills_shown_by_0213(apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    """Put back the hidden skills the forward run flipped, and drop the marker; everyone else is untouched."""
    Member = apps.get_model("membership", "Member")
    for member in Member.objects.filter(directory_visibility__has_key=MARKER).only("pk", "directory_visibility"):
        visibility = dict(member.directory_visibility)
        del visibility[MARKER]
        visibility["skills"] = False
        Member.objects.filter(pk=member.pk).update(directory_visibility=visibility)


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0212_merge_0211"),
    ]

    operations = [
        migrations.RunPython(show_skills_on_directory_cards, hide_skills_shown_by_0213),
    ]
