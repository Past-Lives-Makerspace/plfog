"""The self serve tutorial (#1278, #1295): a few short steps, then the confirm that grants a permission.

Give Tours ends in Become a Tour Guide and Host a Class in Become an Instructor. Both pages draw it
with ``hub/partials/_tutorial.html`` from a tuple of :class:`TutorialStep`, written from that
permission's Guide wiki page, so the two never drift into separate components.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TutorialStep:
    """One step: its title (also its pill's label) and the points it makes, one bullet each."""

    title: str
    points: tuple[str, ...]
