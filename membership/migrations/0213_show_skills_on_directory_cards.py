"""Show every member's skills on their directory card again (#674).

Until #674 the profile form wrote ``directory_visibility["skills"] = False`` on every save,
because Settings had no skills switch to post a value. No member chose hidden, so this sets the
key back to shown and leaves the other six visibility keys exactly as they are.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def show_skills_on_directory_cards(apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    Member = apps.get_model("membership", "Member")
    for member in Member.objects.only("pk", "directory_visibility").iterator():
        visibility = dict(member.directory_visibility)
        if visibility.get("skills") is True:
            continue
        visibility["skills"] = True
        Member.objects.filter(pk=member.pk).update(directory_visibility=visibility)


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0212_merge_0211"),
    ]

    operations = [
        migrations.RunPython(show_skills_on_directory_cards, migrations.RunPython.noop),
    ]
