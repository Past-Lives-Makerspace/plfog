"""The Spotlight in the top left of the Member Portal (#708, shown to members by #709).

One read model for what the Spotlight says: the open poll and its tally, the Feature Request
Meeting's next date, and the two minimized lines with their fallbacks. The admin page's
preview and the member Spotlight both render it through ``hub/partials/_spotlight.html``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.urls import reverse
from django.utils import timezone

from polls.models import ChoiceResult, Poll, open_poll_with_results

if TYPE_CHECKING:
    from core.models import SiteConfiguration
    from membership.models import CommunityEvent

#: The second minimized line when the admin leaves it empty.
SECOND_LINE_DEFAULT = "Feature Request Meeting"
#: How far ahead the next meeting is looked for; a yearly series still has a date in it.
SEARCH_DAYS = 400


def _clock(moment: datetime) -> str:
    """``6 PM`` on the hour, ``6:30 PM`` otherwise, in Portland time."""
    local = timezone.localtime(moment)
    hour = local.hour % 12 or 12
    minutes = f":{local.minute:02d}" if local.minute else ""
    return f"{hour}{minutes} {'AM' if local.hour < 12 else 'PM'}"


@dataclass(frozen=True)
class SpotlightMeeting:
    """The meeting's next date, kept until it ends, with the links the Spotlight offers."""

    title: str
    description: str
    starts_at: datetime
    video_url: str
    ics_url: str
    detail_url: str

    @classmethod
    def next_for(cls, event: CommunityEvent, now: datetime) -> SpotlightMeeting | None:
        """The first date of ``event`` that has not ended by ``now``, or None.

        A meeting in progress stays, so Join online is there while it runs: the rule
        ``CommunityEvent.next_occurrence_start`` uses, which reads its own clock and falls back
        to the first date, so it is not called here (#705). An unpublished event counts as no
        meeting, since its page and its ``.ics`` 404.
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
            description=event.description,
            starts_at=start,
            video_url=event.video_url,
            ics_url=f"{reverse('hub_event_ics', args=[event.pk])}{query}",
            detail_url=f"{reverse('hub_event_detail', args=[event.pk])}{query}",
        )

    @property
    def when(self) -> str:
        """Standard's date line: ``Tue, Nov 10 · 6:00 PM``."""
        local = timezone.localtime(self.starts_at)
        return f"{local:%a}, {local:%b} {local.day} · {local.hour % 12 or 12}:{local.minute:02d} {local:%p}"

    @property
    def pill(self) -> str:
        """Minimized's pill: ``Nov 10 @ 6 PM``."""
        local = timezone.localtime(self.starts_at)
        return f"{local:%b} {local.day} @ {_clock(self.starts_at)}"


@dataclass(frozen=True)
class Spotlight:
    """Everything the Spotlight shows at one moment."""

    poll: Poll | None
    results: list[ChoiceResult]
    meeting: SpotlightMeeting | None
    first_line_text: str
    second_line_text: str
    text_changed_at: datetime | None

    @classmethod
    def build(cls, config: SiteConfiguration, now: datetime) -> Spotlight:
        """Read the Spotlight from the settings row (meeting joined in) and the open poll.

        Args:
            config: The settings row, from ``SiteConfiguration.load_with_spotlight_meeting``.
            now: The moment to judge the open poll and the next meeting against.
        """
        poll, results = open_poll_with_results(now)
        event = config.spotlight_meeting_event
        return cls(
            poll=poll,
            results=results,
            meeting=SpotlightMeeting.next_for(event, now) if event is not None else None,
            first_line_text=config.spotlight_first_line,
            second_line_text=config.spotlight_second_line,
            text_changed_at=config.spotlight_text_changed_at,
        )

    @property
    def first_line_fallback(self) -> str:
        """What the first line says when the admin leaves it empty: the poll question, if any."""
        return self.poll.question if self.poll is not None else ""

    @property
    def first_line(self) -> str:
        """Minimized's first line: the admin's text, else this week's poll question."""
        return self.first_line_text or self.first_line_fallback

    @property
    def second_line(self) -> str:
        """Minimized's second line: the admin's text, else "Feature Request Meeting"."""
        return self.second_line_text or SECOND_LINE_DEFAULT

    @property
    def total_votes(self) -> int:
        """Votes cast in the open poll."""
        return sum(result.votes for result in self.results)
