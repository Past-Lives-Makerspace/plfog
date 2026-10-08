"""The "Building with you" panel behind the sidebar version pill (#699).

One home for what the panel shows and for the rule that makes the pill loud, so the template
stays dumb. Everything here is computed from a loaded :class:`core.models.SiteConfiguration`
(with its Feature Meeting event joined in) and the composed changelog; nothing queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.urls import reverse
from django.utils import timezone

if TYPE_CHECKING:
    from core.models import SiteConfiguration
    from membership.models import CommunityEvent

#: How far ahead the next Feature Meeting is looked for. A yearly series still has a date in it.
SEARCH_DAYS = 400
#: The pill gets its dot when the next Feature Meeting starts within this window.
MEETING_SOON = timedelta(hours=72)
#: How many changelog entries "Recently shipped" lists.
RECENT_COUNT = 3


@dataclass(frozen=True)
class FeatureMeeting:
    """The next occurrence of the Feature Meeting, with the links the panel offers."""

    title: str
    starts_at: datetime
    video_url: str
    ics_url: str
    detail_url: str

    @classmethod
    def next_after(cls, event: CommunityEvent, now: datetime) -> FeatureMeeting | None:
        """The first occurrence of ``event`` that has not ended by ``now``, or None.

        A meeting in progress stays, so Join online is there while it runs; this is the rule
        :meth:`CommunityEvent.next_occurrence_start` uses, which reads its own clock and falls
        back to the first date, so it is not called here. An unpublished event counts as no
        meeting: its page and its ``.ics`` 404.
        """
        if event.moderation_state != event.ModerationState.PUBLISHED:
            return None
        today = timezone.localdate(now)
        duration = event.ends_at - event.starts_at
        window = event.occurrences_in(today, today + timedelta(days=SEARCH_DAYS))
        upcoming = [start for start in window if start + duration > now]
        if not upcoming:
            return None
        start = upcoming[0]
        query = event.date_query(start)
        return cls(
            title=event.title,
            starts_at=start,
            video_url=event.video_url,
            ics_url=f"{reverse('hub_event_ics', args=[event.pk])}{query}",
            detail_url=f"{reverse('hub_event_detail', args=[event.pk])}{query}",
        )


@dataclass(frozen=True)
class ShippedEntry:
    """One "Recently shipped" line: a member-facing changelog entry's title and date."""

    title: str
    shipped_on: date

    @classmethod
    def from_entry(cls, entry: dict[str, Any]) -> ShippedEntry:
        """Read a composed changelog entry (``plfog.changelog``), whose date is ``YYYY-MM-DD``."""
        return cls(title=entry["title"], shipped_on=date.fromisoformat(entry["date"]))


@dataclass(frozen=True)
class BuildingWithYou:
    """What the panel shows as of one moment, and whether the pill should say "new"."""

    meeting: FeatureMeeting | None
    being_built: list[str]
    recently_shipped: list[ShippedEntry]
    backlog_url: str
    is_loud: bool

    @classmethod
    def build(cls, config: SiteConfiguration, now: datetime, changelog: list[dict[str, Any]]) -> BuildingWithYou:
        """Assemble the panel from the site settings and the member-facing changelog.

        Args:
            config: The settings row, ideally from ``SiteConfiguration.load_with_feature_meeting``.
            now: The moment to judge "next" and "soon" against.
            changelog: Member-facing entries, newest first (``plfog.version.CHANGELOG``).
        """
        event = config.feature_meeting_event
        meeting = FeatureMeeting.next_after(event, now) if event is not None else None
        lines = [line.strip() for line in config.being_built_now.splitlines() if line.strip()]
        return cls(
            meeting=meeting,
            being_built=lines,
            recently_shipped=[ShippedEntry.from_entry(entry) for entry in changelog[:RECENT_COUNT]],
            backlog_url=config.backlog_url,
            is_loud=_meeting_is_soon(meeting, now) or _shipped_lately(changelog, now),
        )


def _meeting_is_soon(meeting: FeatureMeeting | None, now: datetime) -> bool:
    """Whether the next meeting starts within :data:`MEETING_SOON` of ``now``."""
    return meeting is not None and meeting.starts_at - now <= MEETING_SOON


def _shipped_lately(changelog: list[dict[str, Any]], now: datetime) -> bool:
    """Whether the newest member-facing release is dated today or yesterday, Portland time."""
    if not changelog:
        return False
    today = timezone.localdate(now)
    return changelog[0]["date"] in {today.isoformat(), (today - timedelta(days=1)).isoformat()}
