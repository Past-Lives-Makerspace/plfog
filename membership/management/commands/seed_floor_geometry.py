"""Draw the real Past Lives floors onto the interactive space map.

The map is rendered by the app from :class:`~membership.models.MapHotspot` rectangles, so
this command *is* how the building gets onto it: it reads the traced geometry in
``membership/floor_geometry.py`` and writes one :class:`~membership.models.Floorplan` per
floor plus one hotspot per room, binding each leasable room to its Airtable-sourced
:class:`~membership.models.Space` by ``space_id``.

Idempotent and safe to re-run. Seeded rooms live in their own ``sort_order`` band starting
at :data:`SEED_SORT_BASE`, and each room keeps the same slot run after run — so correcting a
rectangle in the geometry module and re-running is the intended workflow, and a marker an
admin placed by hand (which lands at ``sort_order`` 0) can never be overwritten by a seed.
Nothing is ever deleted, and a floor's published flag and underlay image are left alone
after the first run.

A room whose code has no matching ``Space`` (a bathroom, a shop, or a code that has since
left Airtable) is still drawn — as a labelled facility marker — and reported at the end.

Usage::

    manage.py seed_floor_geometry [--dry-run]
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

#: Seeded rooms occupy their own ``sort_order`` band so they never collide with a marker an
#: admin dropped by hand (those land at 0 via the model default).
SEED_SORT_BASE = 1000


class Command(BaseCommand):
    help = (
        "Draw the real Past Lives floors onto the space map from the traced geometry in "
        "membership/floor_geometry.py. Idempotent; use --dry-run to preview."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing any rows.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.floor_geometry import FLOORS
        from membership.models import Floorplan, MapHotspot, Space

        dry_run = bool(options["dry_run"])
        spaces = {space.space_id: space for space in Space.objects.all()}
        unmatched: list[str] = []
        totals = {"floors": 0, "created": 0, "updated": 0, "bound": 0}

        for seed_floor in FLOORS:
            floor = self._sync_floor(seed_floor, Floorplan, dry_run=dry_run)
            totals["floors"] += 1
            for offset, room in enumerate(seed_floor.rooms):
                index = SEED_SORT_BASE + offset
                space = spaces.get(room.space_code) if room.space_code else None
                if room.space_code and space is None:
                    unmatched.append(f"{seed_floor.name}: {room.label}")
                if space is not None:
                    totals["bound"] += 1
                x, y, w, h = seed_floor.percent_box(room)
                values: dict[str, Any] = {
                    "shape": MapHotspot.Shape.REGION,
                    "kind": room.kind if space is not None else self._facility_kind(room.kind, MapHotspot),
                    "space": space,
                    "label": "" if space is not None else room.label,
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                }
                created = self._sync_hotspot(floor, index, values, MapHotspot, dry_run=dry_run)
                totals["created" if created else "updated"] += 1

        self._report(totals, unmatched, dry_run=dry_run)

    @staticmethod
    def _facility_kind(kind: str, hotspot_model: Any) -> str:
        """Downgrade an unmatched studio/cubby to a plain facility marker.

        A room whose Space has gone (or never arrived) still belongs on the plan — drawing
        it as a labelled facility keeps the floor complete instead of leaving a hole, and it
        offers no "request this" action it could not honour.
        """
        if kind in hotspot_model.REQUESTABLE_KINDS:
            return str(hotspot_model.Kind.FACILITY)
        return kind

    def _sync_floor(self, seed_floor: Any, floorplan_model: Any, *, dry_run: bool) -> Any:
        """Create or refresh this floor's row, leaving its publish flag and underlay alone."""
        floor = floorplan_model.objects.filter(name=seed_floor.name).first()
        if floor is None:
            if dry_run:
                return floorplan_model(name=seed_floor.name)
            floor = floorplan_model.objects.create(
                name=seed_floor.name,
                sort_order=seed_floor.sort_order,
                aspect_ratio=seed_floor.aspect_ratio,
                caption=seed_floor.caption,
                is_published=True,
            )
            return floor
        floor.sort_order = seed_floor.sort_order
        floor.aspect_ratio = seed_floor.aspect_ratio
        floor.caption = seed_floor.caption
        if not dry_run:
            floor.save(update_fields=["sort_order", "aspect_ratio", "caption", "updated_at"])
        return floor

    @staticmethod
    def _sync_hotspot(floor: Any, index: int, values: dict[str, Any], hotspot_model: Any, *, dry_run: bool) -> bool:
        """Write one room's marker. Returns True when it is new to this floor."""
        if floor.pk is None:
            return True
        existing = hotspot_model.objects.filter(floorplan=floor, sort_order=index).first()
        if existing is None:
            if not dry_run:
                hotspot_model.objects.create(floorplan=floor, sort_order=index, **values)
            return True
        for name, value in values.items():
            setattr(existing, name, value)
        if not dry_run:
            existing.save(update_fields=[*values, "updated_at"])
        return False

    def _report(self, totals: dict[str, int], unmatched: list[str], *, dry_run: bool) -> None:
        """Print the run summary, loudly naming every room that found no Space."""
        prefix = "Would seed" if dry_run else "Seeded"
        self.stdout.write(
            f"{prefix} {totals['floors']} floor(s): "
            f"{totals['created']} marker(s) added, {totals['updated']} refreshed, "
            f"{totals['bound']} bound to a Space."
        )
        if unmatched:
            self.stdout.write(
                self.style.WARNING(f"{len(unmatched)} room(s) had no matching Space — drawn as facilities:")
            )
            for line in unmatched:
                self.stdout.write(f"  · {line}")
