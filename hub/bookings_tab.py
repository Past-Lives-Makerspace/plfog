"""What the Bookings tabs on the Orientations page (#626) and the Reservations page (#627) share.

Both tabs are one list whose rows depend on who looks, with chips, filters and a "..." menu
per row. These are the small pieces neither page owns: the pane's own query string (always
``view=bookings`` first, so a link keeps the tab open), the From / To date parse that never
raises, and the late cancellation fee read off ``select_related``.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from django.http import HttpRequest, QueryDict
from django.utils.dateparse import parse_date

if TYPE_CHECKING:
    from billing.models import LateCancellationFee


def pane_query(params: dict[str, str]) -> str:
    """A pane URL's query string: ``view=bookings`` first, then the given non blank params."""
    query = QueryDict(mutable=True)
    query["view"] = "bookings"
    for key, value in params.items():
        if value:
            query[key] = value
    return query.urlencode()


def date_param(request: HttpRequest, key: str) -> date | None:
    """A From or To date from the query string, or None when blank or not a real date."""
    try:
        return parse_date(request.GET.get(key, ""))
    except ValueError:  # well formed but impossible, e.g. 2026-02-30
        return None


def late_fee_of(row: object) -> LateCancellationFee | None:
    """A booking's or reservation's late cancellation fee, read off ``select_related`` (no query), or None.

    The reverse one to one from ``LateCancellationFee`` has no row for most bookings; Django's
    missing related object is an ``AttributeError`` too, so ``getattr`` with a default is the
    lookup that answers None for "no fee".
    """
    fee: LateCancellationFee | None = getattr(row, "late_fee", None)
    return fee
