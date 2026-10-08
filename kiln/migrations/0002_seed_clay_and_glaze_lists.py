"""Seed the Ceramics Guild's clay and studio glaze lists (#691).

The guild's current choices, in the order its paper slip lists them. The crew edits them on
the Lists screen from then on. Reversing removes only these names, and only while no ticket
uses them.
"""

from __future__ import annotations

from django.db import migrations

CLAYS = [
    "G-Mix 6",
    "Trail Mix Dark Chocolate",
    "Kristy Lombard",
    "Trail Mix Toast",
    "White Salmon",
]

GLAZES = [
    "Perfect White",
    "Ritual Clear",
    "Georgie's Patent Black",
    "Georgie's Zinc-Free Clear",
    "Coyote Copper",
    "Penguin Floating Blue",
    "Plum Wine",
    "Donte's Laogai Green",
    "Old Forge Campfire",
    "RC-2 Oxidation Red",
]


def seed(apps, schema_editor):
    for model_name, names in (("ClayOption", CLAYS), ("GlazeOption", GLAZES)):
        model = apps.get_model("kiln", model_name)
        for order, name in enumerate(names):
            model.objects.get_or_create(name=name, archived_at=None, defaults={"sort_order": order})


def unseed(apps, schema_editor):
    for model_name, names in (("ClayOption", CLAYS), ("GlazeOption", GLAZES)):
        model = apps.get_model("kiln", model_name)
        model.objects.filter(name__in=names, tickets__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("kiln", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
