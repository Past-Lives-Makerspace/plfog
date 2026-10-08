"""The Spotlight in the top left of the Member Portal (#708, shown to members by #709).

One read model for what the Spotlight says: the open poll and its tally, the Feature Request
Meeting's next date, and the two minimized lines with their fallbacks. The admin page's
preview and the member Spotlight both render it through ``hub/partials/_spotlight.html``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.db.models import Count, IntegerField, Subquery, Value
from django.urls import reverse
from django.utils import timezone

from core.models import SiteConfiguration
from polls.models import MAX_CHOICES, ChoiceResult, Poll, PollCard, PollChoice, PollVote, can_vote, tally

if TYPE_CHECKING:
    from membership.models import CommunityEvent, Member

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


def _row_annotations(member: Member | None, now: datetime) -> dict[str, Any]:
    """The open poll, its answers with counts and this member's vote, as columns on the settings row.

    The open poll does not depend on the settings row, so every value is an uncorrelated scalar
    subquery: the whole Spotlight is one SELECT however many answers the poll has (at most
    ``MAX_CHOICES``), which keeps the hub's one-extra-query budget (#709).
    """
    open_polls = Poll.objects.open_at(now).order_by("-opens_at", "-pk")
    open_pk = Subquery(open_polls.values("pk")[:1])
    columns: dict[str, Any] = {
        "sp_poll_pk": open_pk,
        "sp_poll_question": Subquery(open_polls.values("question")[:1]),
        "sp_poll_opens_at": Subquery(open_polls.values("opens_at")[:1]),
        "sp_poll_closes_at": Subquery(open_polls.values("closes_at")[:1]),
        "sp_my_choice": (
            Subquery(PollVote.objects.filter(poll_id=open_pk, member=member).values("choice_id")[:1])
            if member is not None
            else Value(None, output_field=IntegerField())
        ),
    }
    answers = PollChoice.objects.filter(poll_id=open_pk).order_by("position")
    counted = answers.annotate(sp_votes=Count("votes"))
    for index in range(MAX_CHOICES):
        columns[f"sp_a{index}_pk"] = Subquery(answers.values("pk")[index : index + 1])
        columns[f"sp_a{index}_text"] = Subquery(answers.values("text")[index : index + 1])
        columns[f"sp_a{index}_votes"] = Subquery(counted.values("sp_votes")[index : index + 1])
    return columns


@dataclass(frozen=True)
class Spotlight:
    """Everything the Spotlight shows one member at one moment."""

    poll: Poll | None
    results: list[ChoiceResult]
    meeting: SpotlightMeeting | None
    first_line_text: str
    second_line_text: str
    text_changed_at: datetime | None
    my_choice_pk: int | None = None
    can_vote: bool = False

    @classmethod
    def load(cls, member: Member | None, now: datetime) -> Spotlight:
        """Read the whole Spotlight for ``member`` in one query.

        Args:
            member: Whose vote to mark; None reads the Spotlight as nobody (the admin preview).
            now: The moment to judge the open poll and the next meeting against.
        """
        row = (
            SiteConfiguration.objects.select_related("spotlight_meeting_event")
            .annotate(**_row_annotations(member, now))
            .filter(pk=1)
            .first()
        )
        if row is None:
            # A fresh install has no settings row yet; make it, then read again.
            SiteConfiguration.load()
            return cls.load(member, now)
        values = vars(row)
        poll: Poll | None = None
        results: list[ChoiceResult] = []
        if values["sp_poll_pk"] is not None:
            poll = Poll(
                pk=values["sp_poll_pk"],
                question=values["sp_poll_question"],
                opens_at=values["sp_poll_opens_at"],
                closes_at=values["sp_poll_closes_at"],
            )
            answers = []
            for index in range(MAX_CHOICES):
                if values[f"sp_a{index}_pk"] is None:
                    break
                answer = PollChoice(pk=values[f"sp_a{index}_pk"], text=values[f"sp_a{index}_text"], position=index)
                answer.vote_count = values[f"sp_a{index}_votes"]  # type: ignore[attr-defined]
                answers.append(answer)
            results = tally(answers)
        event = row.spotlight_meeting_event
        return cls(
            poll=poll,
            results=results,
            meeting=SpotlightMeeting.next_for(event, now) if event is not None else None,
            first_line_text=row.spotlight_first_line,
            second_line_text=row.spotlight_second_line,
            text_changed_at=row.spotlight_text_changed_at,
            my_choice_pk=values["sp_my_choice"],
            can_vote=can_vote(member),
        )

    @property
    def card(self) -> PollCard | None:
        """The open poll as this member sees it, for the Spotlight and Expanded; None with no poll."""
        if self.poll is None:
            return None
        return PollCard(
            poll=self.poll, results=self.results, my_choice_pk=self.my_choice_pk, is_open=True, can_vote=self.can_vote
        )

    @property
    def seen_signature(self) -> str:
        """What the member has seen, for the yellow dot: the open poll, the next meeting and the text stamp.

        The browser keeps the last signature the member opened the Spotlight on; a different one
        here means something is new. A monthly meeting rolling to its next date changes it too.
        """
        # Times in UTC, so a value read back from the database in another zone signs the same.
        parts = [
            str(self.poll.pk) if self.poll is not None else "",
            self.meeting.starts_at.astimezone(UTC).isoformat() if self.meeting is not None else "",
            self.text_changed_at.astimezone(UTC).isoformat() if self.text_changed_at is not None else "",
        ]
        return "|".join(parts)

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
