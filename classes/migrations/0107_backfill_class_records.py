"""Give every class run its class (#1260): one ClassRecord per grouping key, every run attached.

Data only. A run's own row changes in one column, ``class_record``, written with ``update()`` so
``updated_at``, ``grouping_key``, its registrations and its payouts stay exactly as they were.

The class takes its title and Class Type from its newest run (the highest pk). Its address is the
title part of the grouping key; when another class or another class's run already answers to it,
the Class Type's slug is added, then a number. A run of the same class may hold the address: that
old link simply becomes the class page. Groups go oldest first, so the class that has used a title
longest keeps the bare address.

Only runs with no class are read, so a second run changes nothing. The reverse detaches every run
and deletes every class, the state 0106 leaves.
"""

from __future__ import annotations

import itertools
from typing import Any

from django.db import migrations

# Room left under the 255 character slug column for a Class Type and a number.
SLUG_BASE_LENGTH = 180


def free_slug(base: str, category_slug: str, taken: set[str], other_run_slugs: set[str]) -> str:
    """The first of ``base``, ``base-<class type>``, ``base-<class type>-2``... free of ``taken`` and other runs."""
    candidates = itertools.chain(
        [base, f"{base}-{category_slug}"],
        (f"{base}-{category_slug}-{n}" for n in itertools.count(2)),
    )
    return next(c for c in candidates if c not in taken and c not in other_run_slugs)


def attach_runs(apps: Any, schema_editor: Any = None) -> int:
    """Create one class per grouping key and attach its runs; returns how many classes were made."""
    ClassOffering = apps.get_model("classes", "ClassOffering")
    ClassRecord = apps.get_model("classes", "ClassRecord")
    Category = apps.get_model("classes", "Category")

    groups: dict[str, list[dict[str, Any]]] = {}
    for run in (
        ClassOffering.objects.filter(class_record__isnull=True)
        .order_by("pk")
        .values("pk", "slug", "title", "grouping_key", "category_id")
    ):
        groups.setdefault(run["grouping_key"] or f"solo:{run['pk']}", []).append(run)

    category_slugs = dict(Category.objects.values_list("pk", "slug"))
    taken = set(ClassRecord.objects.values_list("slug", flat=True))
    all_run_slugs = dict(ClassOffering.objects.values_list("slug", "pk"))
    made = 0
    for key, runs in groups.items():
        newest = runs[-1]
        own = {run["slug"] for run in runs}
        base = "" if key.startswith("solo:") else key.split(":", 1)[0][:SLUG_BASE_LENGTH].strip("-")
        slug = free_slug(
            base or "class",
            category_slugs[newest["category_id"]],
            taken,
            set(all_run_slugs) - own,
        )
        record = ClassRecord.objects.create(slug=slug, title=newest["title"], category_id=newest["category_id"])
        taken.add(slug)
        ClassOffering.objects.filter(pk__in=[run["pk"] for run in runs]).update(class_record=record)
        made += 1
    return made


def detach_runs(apps: Any, schema_editor: Any = None) -> None:
    """Undo :func:`attach_runs`: every run loses its class and every class is deleted."""
    apps.get_model("classes", "ClassOffering").objects.update(class_record=None)
    apps.get_model("classes", "ClassRecord").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0106_class_record"),
    ]

    operations = [
        migrations.RunPython(attach_runs, detach_runs),
    ]
