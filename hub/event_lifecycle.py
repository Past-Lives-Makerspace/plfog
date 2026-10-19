"""The chip row on Manage Events (#831, part 2).

Each chip names one ``CommunityEventQuerySet`` method, the same way ``classes.lifecycle``
builds the Manage Classes chips, so the counts and the filtered table can never disagree with
the Status pill. A draft has no chip of its own: it shows under All, and Manage Events lands
on it straight after a Duplicate or an Unpublish.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from classes.lifecycle import ALL_FACET, Facet

if TYPE_CHECKING:
    from membership.models import CommunityEventQuerySet

#: Manage Events: what is coming up, what waits on a reviewer, what is parked, what passed
#: and what was turned down. ``?status=<key>`` selects one.
EVENT_FACETS: tuple[Facet[CommunityEventQuerySet], ...] = (
    ALL_FACET,
    Facet("upcoming", "Upcoming", lambda qs: qs.published().upcoming()),
    Facet("needs_review", "Needs review", lambda qs: qs.awaiting_review()),
    Facet("scheduled", "Scheduled", lambda qs: qs.scheduled()),
    Facet("past", "Past", lambda qs: qs.past()),
    Facet("declined", "Declined", lambda qs: qs.declined()),
)

#: The chip the reviewer queue hands admins and Calendar Administrators to.
NEEDS_REVIEW_KEY = "needs_review"
