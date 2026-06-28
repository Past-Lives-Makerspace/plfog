"""Add Guild.slug and backfill it from each guild's name (runs automatically on deploy)."""

from __future__ import annotations

from django.db import migrations, models
from django.utils.text import slugify


def backfill_slugs(apps, schema_editor):
    """Give every existing guild a unique slug derived from its name."""
    Guild = apps.get_model("membership", "Guild")
    for guild in Guild.objects.all().order_by("pk"):
        base = slugify(guild.name) or "guild"
        slug = base
        n = 2
        # Already-assigned guilds carry a non-empty slug; unassigned ones are "" and
        # never collide, so checking the DB as we go keeps each slug unique.
        while Guild.objects.exclude(pk=guild.pk).filter(slug=slug).exists():
            slug = f"{base}-{n}"
            n += 1
        guild.slug = slug
        guild.save(update_fields=["slug"])


def clear_slugs(apps, schema_editor):
    """Reverse: blank every slug (the column is dropped by the AddField reversal)."""
    Guild = apps.get_model("membership", "Guild")
    Guild.objects.update(slug="")


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0060_member_discord_linked_at_member_discord_user_id"),
    ]

    operations = [
        # 1. Add the column non-unique AND non-indexed so existing rows can share the empty
        #    default; the unique index (+ its Postgres _like sibling) is built once in step 3.
        migrations.AddField(
            model_name="guild",
            name="slug",
            field=models.SlugField(blank=True, db_index=False, default="", max_length=120),
        ),
        # 2. Populate a unique slug per guild.
        migrations.RunPython(backfill_slugs, clear_slugs),
        # 3. Enforce uniqueness now that every row has a distinct value.
        migrations.AlterField(
            model_name="guild",
            name="slug",
            field=models.SlugField(
                blank=True,
                help_text="URL slug for the guild's public page, auto-generated from the name (stable across renames).",
                max_length=120,
                unique=True,
            ),
        ),
    ]
