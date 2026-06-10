"""Grouping helpers — collapse the same class offered on many dates into one card.

Legacy events imported from classes.pastlives.space post one Drupal node per
date, so a single class (e.g. "Blacksmithing 101 with Glen") shows up as many
ClassOffering rows. We derive a stable ``grouping_key`` from the normalized
title + category; the public catalog renders one card per key while each dated
offering stays independently bookable with its own capacity and registrations.
"""

from __future__ import annotations

from django.utils.text import slugify

from classes.templatetags.classes_tags import strip_date_suffix


def grouping_key_for(title: str | None, category_id: int | None) -> str:
    """Stable key grouping the same class across dates.

    Combines the date-stripped, slugified title with the category id so two
    unrelated classes that happen to share a title in different categories do
    not collapse together. Returns ``""`` when the title is blank, which the
    catalog treats as "this offering stands alone".
    """
    base = slugify(strip_date_suffix(title or ""))
    if not base:
        return ""
    return f"{base}:{category_id}" if category_id else base


def regroup_offerings() -> tuple[int, int]:
    """Recompute ``grouping_key`` for every offering. Idempotent.

    Used by the ``regroup_classes`` management command and at the end of a
    legacy sync to sanitize keys for rows created before grouping existed.

    Returns:
        (number of offerings examined, number of distinct catalog groups).
    """
    from classes.models import ClassOffering

    offerings = list(ClassOffering.objects.all().only("id", "title", "category_id", "grouping_key"))
    distinct_keys: set[str] = set()
    to_update: list[ClassOffering] = []
    for offering in offerings:
        key = grouping_key_for(offering.title, offering.category_id)
        distinct_keys.add(key or f"solo:{offering.pk}")
        if offering.grouping_key != key:
            offering.grouping_key = key
            to_update.append(offering)
    if to_update:
        ClassOffering.objects.bulk_update(to_update, ["grouping_key"])
    return len(offerings), len(distinct_keys)
