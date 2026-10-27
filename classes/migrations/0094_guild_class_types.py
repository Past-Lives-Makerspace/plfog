"""Every active guild gets a Class Type on the class composer.

Production had two active guilds with no Class Type (Printmaking, Prison Outreach), so
members could not file a class under them. From this release ``Guild.save`` calls
``Guild.ensure_class_type``; this applies the same rule to the guilds that already exist.
Model methods do not exist on historical models, so the rule is written out here: a guild
with a linked Class Type is left alone; otherwise an unlinked Class Type with the same name
or slug is linked to it, or a new one is created, named for the guild without a trailing
"Guild". A name another guild already owns is skipped.

Reverse leaves every Class Type in place: one created here may already have classes filed
under it, and an unused one is harmless.
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


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0093_split_multi_date_single_classes"),
        ("membership", "0245_guildneedsattention"),
    ]

    operations = [
        migrations.RunPython(create_guild_class_types, migrations.RunPython.noop),
    ]
