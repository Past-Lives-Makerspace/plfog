"""The live class edit form's facts as a browser posts them, for specs that save that form (#1067)."""

from __future__ import annotations

from classes.models import ClassOffering


def live_facts(offering: ClassOffering, **overrides: str) -> dict[str, str]:
    """The title, class type, price, capacity, location and age minimum ``offering`` holds, as POST values.

    The live class edit page renders these filled in, so a save that changes none of them posts
    them back unchanged. Capacity is left out for a flexible class, whose form has no such field.

    Args:
        offering: The live class the form edits.
        **overrides: Values to post in place of the stored ones.
    """
    facts = {
        "title": offering.title,
        "category": str(offering.category_id),
        "price_cents": f"{offering.price_cents / 100:.2f}",
        "area": str(offering.area_id or ""),
        "age_minimum": "" if offering.age_minimum is None else str(offering.age_minimum),
    }
    if not offering.is_flexible:
        facts["capacity"] = str(offering.capacity)
    facts.update(overrides)
    return facts
