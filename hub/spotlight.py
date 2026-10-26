"""The Spotlight in the top left of the Member Portal (#708, shown to members by #709).

Since #935 it is a static version card: the version pill, which opens the changelog, the newest
update's date and Details. The Spotlight messages crawl across the top of Home instead
(``hub.home.HomeBanner``). This module keeps who sees the card and the newest update it names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from membership.models import Member


def sees_spotlight(member: Member | None) -> bool:
    """Whether this account sees the Spotlight: a member record that is not a guest (#691) or a former member.

    The rule the removed polls app's ``can_vote`` had (#709), moved here unchanged in #890.
    """
    from membership.models import Member

    return member is not None and member.status not in (Member.Status.GUEST, Member.Status.FORMER)


@dataclass(frozen=True)
class LatestUpdate:
    """The newest member-facing changelog entry: the card's "Updated X" and the first section of Details."""

    title: str
    date: str
    anchor: str

    @classmethod
    def newest(cls) -> LatestUpdate | None:
        """The first entry of the composed changelog (member-facing only, newest first), or None."""
        from plfog import version

        if not version.CHANGELOG:
            return None
        entry = version.CHANGELOG[0]
        slug = entry.get("slug", "")  # swept history entries have no slug, so no anchor
        return cls(title=entry["title"], date=entry["date"], anchor=f"changelog-{slug}" if slug else "")

    @property
    def short_date(self) -> str:
        """The date without its year, for the card's one line: ``Oct 9``."""
        day = date.fromisoformat(self.date)
        return f"{day:%b} {day.day}"
