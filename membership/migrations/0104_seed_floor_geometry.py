"""Seed the drawn floor map from the committed geometry.

The map is static data: ``membership/floor_geometry.py`` holds the traced rectangles and
the app draws the map from the :class:`Floorplan` / :class:`MapHotspot` rows they become.
Migrations create those tables but not their rows, so a fresh database (production
included) renders an empty "upload a plan" state until something loads the geometry. The
``seed_floor_geometry`` management command does that by hand; this migration does it on
deploy, so the map is present in every environment without a manual step.

It runs **once** (migrations are tracked), so it seeds the initial layout and then never
touches it again — an admin who drags or edits a marker in the placement editor keeps their
change through future deploys. Each row is created with ``get_or_create`` keyed on
``(floorplan, sort_order)``, so it is also a no-op if the command was already run by hand.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from django.db import migrations

#: Seeded rows sit in their own sort_order band so they never collide with a marker an
#: admin dropped by hand (those land at 0 via the model default). Mirrors the command.
SEED_SORT_BASE = 1000
#: Kinds bound to a Space; the rest are drawn as facility/info markers.
_REQUESTABLE = ("studio", "cubby")


def seed_floor_geometry(apps: Any, schema_editor: Any) -> None:
    # Skip under pytest: a data migration commits outside the per-test transaction, so
    # seeded floors/markers would persist into every test and break the many count and
    # legend assertions. Tests exercise the geometry through factories and the command's
    # own spec instead. (Same guard as 0039_seed_guild_content.)
    if "pytest" in sys.argv[0] or "PYTEST_CURRENT_TEST" in os.environ:
        return

    from membership.floor_geometry import FLOORS

    Floorplan = apps.get_model("membership", "Floorplan")
    MapHotspot = apps.get_model("membership", "MapHotspot")
    Space = apps.get_model("membership", "Space")

    spaces = {space.space_id: space for space in Space.objects.all()}

    for seed_floor in FLOORS:
        floor, _ = Floorplan.objects.get_or_create(
            name=seed_floor.name,
            defaults={
                "sort_order": seed_floor.sort_order,
                "aspect_ratio": seed_floor.aspect_ratio,
                "caption": seed_floor.caption,
                "is_published": True,
            },
        )
        for offset, room in enumerate(seed_floor.rooms):
            space = spaces.get(room.space_code) if room.space_code else None
            kind = room.kind
            if space is None and kind in _REQUESTABLE:
                # A leasable room whose Space has not synced yet is still drawn, as a
                # labelled facility, so the floor stays complete.
                kind = "facility"
            x, y, w, h = seed_floor.percent_box(room)
            MapHotspot.objects.get_or_create(
                floorplan=floor,
                sort_order=SEED_SORT_BASE + offset,
                defaults={
                    "shape": "region",
                    "kind": kind,
                    "space": space,
                    "label": "" if space is not None else room.label,
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                },
            )


def unseed_floor_geometry(apps: Any, schema_editor: Any) -> None:
    """Remove what the forward pass added: the seeded markers, and any floor left empty."""
    from membership.floor_geometry import FLOORS

    Floorplan = apps.get_model("membership", "Floorplan")
    MapHotspot = apps.get_model("membership", "MapHotspot")

    names = [seed_floor.name for seed_floor in FLOORS]
    MapHotspot.objects.filter(floorplan__name__in=names, sort_order__gte=SEED_SORT_BASE).delete()
    for floor in Floorplan.objects.filter(name__in=names):
        if not MapHotspot.objects.filter(floorplan=floor).exists():
            floor.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0103_alter_maphotspot_kind"),
    ]

    operations = [
        migrations.RunPython(seed_floor_geometry, unseed_floor_geometry),
    ]
