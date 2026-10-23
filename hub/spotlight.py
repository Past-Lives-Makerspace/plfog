"""The Spotlight in the top left of the Member Portal (#708, shown to members by #709).

One read model for what the Spotlight says: the live announcements (#890, in the poll's old
place), the Feature Request Meeting's next date, and the two minimized lines with their
fallbacks. The admin page's preview and the member Spotlight both render it through
``hub/partials/_spotlight.html``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from django.urls import reverse
from django.utils import timezone

from core.models import Announcement, SiteConfiguration

if TYPE_CHECKING:
    from membership.models import CommunityEvent, Member

#: The second minimized line when the admin leaves it empty.
SECOND_LINE_DEFAULT = "Feature Request Meeting"
#: How far ahead the next meeting is looked for; a yearly series still has a date in it.
SEARCH_DAYS = 400


def sees_spotlight(member: Member | None) -> bool:
    """Whether this account sees the Spotlight: a member record that is not a guest (#691) or a former member.

    The rule the removed polls app's ``can_vote`` had (#709), moved here unchanged in #890.
    """
    from membership.models import Member

    return member is not None and member.status not in (Member.Status.GUEST, Member.Status.FORMER)


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
            description=event.description_text,
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
class LatestUpdate:
    """The newest member-facing changelog entry: Expanded shows it with no live announcement, and Minimized falls back to its title."""

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
        """The date without its year, for the quiet Spotlight's one line: ``Oct 9``."""
        day = date.fromisoformat(self.date)
        return f"{day:%b} {day.day}"


@dataclass(frozen=True)
class Spotlight:
    """Everything the Spotlight shows at one moment; it is the same for every member."""

    announcements: list[Announcement]
    meeting: SpotlightMeeting | None
    first_line_text: str
    second_line_text: str
    text_changed_at: datetime | None
    latest_update: LatestUpdate | None = None
    show_when_empty: bool = True
    #: False in Home's phone copy, where the banner just above already shows the announcements (#890).
    shows_announcements: bool = True

    def __bool__(self) -> bool:
        """Whether there is a Spotlight to show at all (#709).

        With no announcement and no meeting it shows only while the admin's "Show the Spotlight
        when there is no announcement and no meeting" is on; off, the hub falls back to the logo
        and the version number, which every template gets by testing ``{% if spotlight %}``.
        """
        return not self.is_empty or self.show_when_empty

    @property
    def shown_announcements(self) -> list[Announcement]:
        """The announcements this copy shows: every live one, or none in Home's phone copy."""
        return self.announcements if self.shows_announcements else []

    @property
    def is_empty(self) -> bool:
        """No announcement to show and no upcoming meeting."""
        return not self.shown_announcements and self.meeting is None

    @property
    def without_announcements(self) -> Spotlight:
        """This Spotlight for the top of Home on a phone: the same, less the announcements (#890).

        Its seen signature is unchanged, so opening it records the announcements as seen: the
        banner above it has just shown them.
        """
        return replace(self, shows_announcements=False)

    @classmethod
    def load(cls, now: datetime) -> Spotlight:
        """Read the whole Spotlight at ``now`` in two queries: the settings row with its meeting, then the live announcements."""
        row = SiteConfiguration.objects.select_related("spotlight_meeting_event").filter(pk=1).first()
        if row is None:
            # A fresh install has no settings row yet; make it, then read again.
            SiteConfiguration.load()
            return cls.load(now)
        event = row.spotlight_meeting_event
        return cls(
            announcements=list(Announcement.objects.live(now)),
            meeting=SpotlightMeeting.next_for(event, now) if event is not None else None,
            first_line_text=row.spotlight_first_line,
            second_line_text=row.spotlight_second_line,
            text_changed_at=row.spotlight_text_changed_at,
            latest_update=LatestUpdate.newest(),
            show_when_empty=row.spotlight_show_when_empty,
        )

    @property
    def seen_signature(self) -> str:
        """What the member has seen, for the yellow dot: the live announcements, the next meeting and the text stamp.

        The browser keeps the last signature the member opened the Spotlight on; a different one
        here means something is new. The announcements sign as their ids and last saves
        (:meth:`core.models.Announcement.signature`, the Home banner's), so a new, edited, ended
        or begun one lights the dot and a new order alone does not. A monthly meeting rolling to
        its next date changes it too; a release never does.
        """
        # Times in UTC, so a value read back from the database in another zone signs the same.
        parts = [
            Announcement.signature(self.announcements),
            self.meeting.starts_at.astimezone(UTC).isoformat() if self.meeting is not None else "",
            self.text_changed_at.astimezone(UTC).isoformat() if self.text_changed_at is not None else "",
        ]
        return "|".join(parts)

    @property
    def first_line_fallback(self) -> str:
        """What the first line says when the admin leaves it empty.

        The first shown announcement's words on one line (the line itself cuts them with an
        ellipsis), else the latest update's title.
        """
        if self.shown_announcements:
            return " ".join(self.shown_announcements[0].text.split())
        return self.latest_update.title if self.latest_update is not None else ""

    @property
    def first_line(self) -> str:
        """Minimized's first line: the admin's text, else :attr:`first_line_fallback`."""
        return self.first_line_text or self.first_line_fallback

    @property
    def second_line(self) -> str:
        """Minimized's second line: the admin's text, else "Feature Request Meeting"."""
        return self.second_line_text or SECOND_LINE_DEFAULT
