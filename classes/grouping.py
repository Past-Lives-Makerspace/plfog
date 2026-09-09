"""Grouping helpers — collapse the same class offered on many dates into one card.

Legacy events imported from classes.pastlives.space post one Drupal node per
date, so a single class (e.g. "Blacksmithing 101 with Glen") shows up as many
ClassOffering rows. We derive a stable ``grouping_key`` from the normalized
title + category; the public catalog renders one card per key while each dated
offering stays independently bookable with its own capacity and registrations.
"""

from __future__ import annotations

from typing import Any

from django.utils.text import slugify

from classes.templatetags.classes_tags import strip_date_suffix


class CatalogGroup:
    """One public catalog card: a class plus every date it is offered on.

    ``representative`` supplies the shared display chrome (title, image, price,
    instructor); ``members`` are the individual dated offerings, each still its
    own bookable unit with its own capacity. Built from offerings already sorted
    by soonest upcoming session, so the first member seen is the representative
    and members stay date-ordered.

    The one-argument constructor is public on purpose: any surface that wants to
    render the real catalog card for a single class (the Teach at Past Lives
    page's worked example) builds a genuine single-member group rather than
    faking one, so ``_class_card.html`` never sees an object the catalog itself
    would not produce.
    """

    def __init__(self, representative: Any) -> None:
        self.representative = representative
        self.members = [representative]

    @property
    def date_count(self) -> int:
        return len(self.members)

    @property
    def is_multi(self) -> bool:
        return len(self.members) > 1


def grouped_catalog(offerings: Any) -> list[CatalogGroup]:
    """Collapse offerings sharing a grouping key into one card, preserving order."""
    groups: dict[str, CatalogGroup] = {}
    order: list[str] = []
    for offering in offerings:
        key = offering.grouping_key or f"solo:{offering.pk}"
        group = groups.get(key)
        if group is None:
            groups[key] = CatalogGroup(offering)
            order.append(key)
        else:
            group.members.append(offering)
    return [groups[key] for key in order]


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
