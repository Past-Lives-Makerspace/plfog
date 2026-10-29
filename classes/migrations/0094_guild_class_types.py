"""Every active guild gets a Class Type on the class composer.

Production had two active guilds with no Class Type (Printmaking, Prison Outreach), so
members could not file a class under them. From this release ``Guild.save`` calls
``Guild.ensure_class_type``; this applies the same rule to the guilds that already exist.
Model methods do not exist on historical models, so the rule is written out here: a guild
with a linked Class Type is left alone; otherwise an unlinked Class Type with the same name
or slug is linked to it, or a new one is created, named for the guild without a trailing
"Guild". A name another guild already owns is skipped.

Reverse deletes the bare Class Types this rule makes: linked to a guild, named for it, with no
hero image, no icon and no classes filed under them. Running forward again recreates exactly
those, so nothing is lost. A Class Type with a picture, an icon or a class stays, and one it
linked rather than created stays linked: that link is harmless.
"""

from __future__ import annotations

import re
from typing import Any

from django.db import migrations
from django.db.models import Q
from django.utils.text import slugify


def class_type_name(guild_name: str) -> str:
    return re.sub(r"\s+guild\s*$", "", guild_name, flags=re.IGNORECASE).strip() or guild_name


def create_guild_class_types(apps: Any, schema_editor: Any) -> None:
    Guild = apps.get_model("membership", "Guild")
    Category = apps.get_model("classes", "Category")
    linked = set(Category.objects.filter(guild__isnull=False).values_list("guild_id", flat=True))
    for guild in Guild.objects.filter(is_active=True, deleted_at__isnull=True).order_by("pk"):
        if guild.pk in linked:
            continue
        name = class_type_name(guild.name)
        slug = slugify(name) or guild.slug
        match = Category.objects.filter(Q(name__iexact=name) | Q(slug=slug)).first()
        if match is None:
            Category.objects.create(name=name, slug=slug, guild=guild)
        elif match.guild_id is None:
            match.guild = guild
            match.save(update_fields=["guild"])


def delete_guild_class_types(apps: Any, schema_editor: Any) -> None:
    Category = apps.get_model("classes", "Category")
    bare = Category.objects.filter(guild__isnull=False, classes__isnull=True, hero_image="", icon_svg="")
    # Only the names: a whole Guild row would name columns a later membership migration may already have dropped.
    for pk, name, guild_name in bare.values_list("pk", "name", "guild__name"):
        if name == class_type_name(guild_name):
            Category.objects.filter(pk=pk).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0093_split_multi_date_single_classes"),
        ("membership", "0245_guildneedsattention"),
    ]

    operations = [
        migrations.RunPython(create_guild_class_types, delete_guild_class_types),
    ]
