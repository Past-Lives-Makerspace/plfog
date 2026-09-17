"""Lifecycle facets: the chip rows on the admin and instructor class lists.

Each facet names one :class:`ClassOfferingQuerySet` method, so the chips, their
counts, and the filtered table all read from the same fat-model queryset
definitions and can never disagree with the badge.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from classes.models import ClassOfferingQuerySet

    _Filter = Callable[[ClassOfferingQuerySet], ClassOfferingQuerySet]


@dataclass(frozen=True)
class Facet:
    """One chip: its query key, its label, and the queryset method that scopes the list."""

    key: str
    label: str
    apply: "_Filter"


@dataclass(frozen=True)
class FacetRow:
    """One rendered chip: link, label, count, and whether it is the selected one."""

    url: str
    label: str
    count: int
    is_selected: bool
    key: str


ALL_FACET = Facet("", "All", lambda qs: qs)

# Admin Classes list: every lifecycle state plus the parallel-review rollup.
#
# "With guild lead" and "Awaiting admin" OVERLAP, deliberately. Both review lanes open at
# submit and neither waits for the other, so a class with both lanes open is genuinely in
# both queues and its count belongs in both chips. Making them exclusive would mean picking
# a reviewer to hide the class from, which is the hand-off this design removed. "Held for
# the room check" is the narrow slice inside "With guild lead" where the admin has already
# said yes and only the guild lead is left.
ADMIN_FACETS: tuple[Facet, ...] = (
    ALL_FACET,
    Facet("needs_review", "Needs review", lambda qs: qs.pending_review()),
    Facet("awaiting_guild_lead", "With guild lead", lambda qs: qs.awaiting_guild_lead_any()),
    Facet("awaiting_admin", "Awaiting admin", lambda qs: qs.awaiting_admin()),
    Facet("awaiting_admin_held", "Held for the room check", lambda qs: qs.awaiting_admin_held()),
    Facet("draft", "Drafts", lambda qs: qs.with_lifecycle_inputs().filter(status="draft", bounced=False)),  # type: ignore[misc]  # django-stubs can't see annotate() aliases
    Facet("changes_requested", "Changes requested", lambda qs: qs.changes_requested()),
    Facet("upcoming", "Upcoming", lambda qs: qs.upcoming_published()),
    Facet("completed", "Completed", lambda qs: qs.completed()),
    Facet("cancelled", "Cancelled", lambda qs: qs.cancelled()),
    Facet("archived", "Archived", lambda qs: qs.filter(status="archived")),
)

# Instructor Classes list: what needs them, what is in review, and where live classes stand.
INSTRUCTOR_FACETS: tuple[Facet, ...] = (
    ALL_FACET,
    Facet("needs_attention", "Needs attention", lambda qs: qs.filter(status="draft")),
    Facet("in_review", "In review", lambda qs: qs.pending_review()),
    Facet("upcoming", "Upcoming", lambda qs: qs.upcoming_published()),
    Facet("completed", "Completed", lambda qs: qs.completed()),
    Facet("cancelled", "Cancelled", lambda qs: qs.cancelled()),
)


def resolve_facet(facets: tuple[Facet, ...], key: str) -> Facet:
    """The facet for ``key``; an unknown or blank key falls back to All (junk is never echoed)."""
    for facet in facets:
        if facet.key == key:
            return facet
    return facets[0]


def facet_rows(
    facets: tuple[Facet, ...],
    base: "ClassOfferingQuerySet",
    selected: Facet,
    url_for: Callable[[str], str],
) -> list[FacetRow]:
    """Render every chip with its count against ``base`` (one count query per chip)."""
    return [
        FacetRow(
            url=url_for(facet.key),
            label=facet.label,
            count=facet.apply(base).count(),
            is_selected=facet.key == selected.key,
            key=facet.key,
        )
        for facet in facets
    ]
