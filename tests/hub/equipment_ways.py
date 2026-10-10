"""POST data for the equipment form's Ways to Qualify list (#747), shared by the equipment specs."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Any

from membership.models import OrientationType


def ways_data(
    *ways: Sequence[OrientationType | int | str], saved: int = 0, delete: Collection[int] = ()
) -> dict[str, Any]:
    """The ``ways-`` keys a Save posts: one way per argument, each its ticked orientations.

    ``saved`` is how many leading ways were on the page when it loaded (the formset's
    INITIAL_FORMS); ``delete`` lists the indexes whose Delete was pressed.
    """
    data: dict[str, Any] = {
        "ways-TOTAL_FORMS": str(len(ways)),
        "ways-INITIAL_FORMS": str(saved),
        "ways-MIN_NUM_FORMS": "0",
        "ways-MAX_NUM_FORMS": "1000",
    }
    for index, way in enumerate(ways):
        data[f"ways-{index}-orientations"] = [str(getattr(item, "pk", item)) for item in way]
        if index in delete:
            data[f"ways-{index}-DELETE"] = "on"
    return data
