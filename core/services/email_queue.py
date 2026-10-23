"""The Email Queue tab under Manage > Site Activity (#894 part 2): what the email cron will send.

Read only. Three answers, each from the same rules the senders use, so the screen cannot
drift from what goes out:

* :meth:`DigestQueue.load` — what waits for the next 8 AM staff email, one person at a time,
  with each request's state from the event's still-open check and the newest line per link
  (:func:`core.events.channels.digest_context`'s rule).
* :func:`upcoming_timed_emails` — the timed emails in the next 24 hours, found by walking the
  scheduler sources tick by tick exactly as the cron would, minus what the
  :class:`core.models.EventDelivery` ledger already holds.
* :func:`sent_digests` — the staff emails sent in the last 7 days, with the counts each one
  recorded when it went out.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, TypeAlias

from core.events.channels import DIGEST_HOUR, DIGEST_TRIGGER_KIND, PACIFIC
from core.events.registry import Channel, get_event
from core.events.scheduling import DEFAULT_WINDOW
from core.models import EventDelivery

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from django.contrib.auth.models import User

    from core.events.scheduler import ScheduledOccurrence

#: How far ahead "Coming up" looks, and how far back "Sent" looks.
UPCOMING_HOURS = 24
SENT_DAYS = 7


def next_digest_at(now: datetime) -> datetime:
    """The next 8:00 AM Pacific at or after ``now``: when the queued lines go out."""
    local = now.astimezone(PACIFIC)
    today = local.replace(hour=DIGEST_HOUR, minute=0, second=0, microsecond=0)
    if local <= today:
        return today
    return (local + timedelta(days=1)).replace(hour=DIGEST_HOUR, minute=0, second=0, microsecond=0)


def _person(user: User) -> tuple[str, str]:
    """A recipient's display name and email, for a row on the tab."""
    member = getattr(user, "member", None)
    name = member.display_name if member is not None else user.get_full_name() or user.email
    return name, user.email


# --- Waiting for the next digest ----------------------------------------------


@dataclass(frozen=True)
class QueuedItem:
    """One request on a person's next 8 AM email, as the send will see it."""

    label: str
    title: str
    queued_at: datetime
    url: str
    is_open: bool
    reminded_by: str

    @property
    def pill_label(self) -> str:
        if not self.is_open:
            return "Handled, will be left out"
        if self.reminded_by:
            return f"Reminded by {self.reminded_by}"
        return "Still waiting"

    @property
    def pill_tone(self) -> str:
        """The ``hub-pill--*`` modifier for :attr:`pill_label`."""
        if not self.is_open:
            return "neutral"
        return "warn" if self.reminded_by else "ok"


@dataclass(frozen=True)
class QueuedPerson:
    """Everyone with something queued: their name, address and requests, oldest first."""

    name: str
    email: str
    items: list[QueuedItem]

    @property
    def waiting_count(self) -> int:
        return sum(1 for item in self.items if item.is_open)


@dataclass(frozen=True)
class DigestQueue:
    """The top card and per-person rows of "Waiting for the next digest"."""

    next_send: datetime
    people: list[QueuedPerson]

    @property
    def waiting_count(self) -> int:
        return sum(person.waiting_count for person in self.people)

    @property
    def handled_count(self) -> int:
        return sum(len(person.items) - person.waiting_count for person in self.people)

    @classmethod
    def load(cls, now: datetime) -> DigestQueue:
        """Read every pending digest line, in three queries plus one per digest event."""
        from django.contrib.auth.models import User

        from core.events.resolvers import open_request_urls

        pending = list(
            EventDelivery.objects.filter(channel=Channel.DIGEST.value, status=EventDelivery.Status.PENDING).order_by(
                "created_at"
            )
        )
        urls_by_event: dict[str, set[str]] = {}
        for row in pending:
            urls_by_event.setdefault(row.event_key, set()).add(row.url)
        open_urls = {key: open_request_urls(key, sorted(urls)) for key, urls in urls_by_event.items()}
        users = User.objects.select_related("member").in_bulk({row.recipient_user_id for row in pending})
        newest: dict[int, dict[str, EventDelivery]] = {}
        for row in pending:
            newest.setdefault(row.recipient_user_id, {})[row.url] = row
        people = []
        for user_pk, rows in newest.items():
            if user_pk not in users:
                continue  # the account is gone; the send drops these lines too
            name, email = _person(users[user_pk])
            items = [
                QueuedItem(
                    label=get_event(row.event_key).label,
                    title=row.title,
                    queued_at=row.created_at,
                    url=row.url,
                    is_open=row.url in open_urls[row.event_key],
                    reminded_by=row.reminded_by,
                )
                for row in sorted(rows.values(), key=lambda row: row.created_at)
            ]
            people.append(QueuedPerson(name=name, email=email, items=items))
        people.sort(key=lambda person: person.name.lower())
        return cls(next_send=next_digest_at(now), people=people)


# --- Sent digests, last 7 days -------------------------------------------------


@dataclass(frozen=True)
class SentDigest:
    """One staff email that went out: the Pacific date, who, and what it carried."""

    sent_on: date
    name: str
    email: str
    included: int
    left_out: int


def sent_digests(now: datetime) -> list[SentDigest]:
    """The staff emails sent in the last 7 days, newest first, in two queries.

    Only rows that recorded their counts at send (#894 part 2) are listed: a day's ledger
    row kept after two failed sends holds no counts and is not a sent email.
    """
    from django.contrib.auth.models import User

    rows = list(
        EventDelivery.objects.filter(
            event_key=DIGEST_TRIGGER_KIND,
            channel=Channel.EMAIL.value,
            created_at__gte=now - timedelta(days=SENT_DAYS),
        )
        .exclude(body="")
        .order_by("-created_at")
    )
    users = User.objects.select_related("member").in_bulk({row.recipient_user_id for row in rows})
    sent = []
    for row in rows:
        if row.recipient_user_id not in users:
            continue
        included, left_out = row.digest_counts
        name, email = _person(users[row.recipient_user_id])
        sent.append(
            SentDigest(
                sent_on=date.fromisoformat(row.period.removeprefix("digest:")),
                name=name,
                email=email,
                included=included,
                left_out=left_out,
            )
        )
    return sent


# --- Coming up in the next 24 hours --------------------------------------------


@dataclass(frozen=True)
class UpcomingEmail:
    """One timed email the cron will send: when, which, about what, to how many people."""

    send_at: datetime
    label: str
    about: str
    reach: int


#: A scheduler source and how far ahead one call of it reveals sends, so the walk calls it
#: once per stride rather than every tick and still sees every send in the window.
Source: TypeAlias = "tuple[Callable[[datetime], Iterable[ScheduledOccurrence]], timedelta]"


def timed_email_sources() -> list[Source]:
    """The scheduler sources the cron's email jobs run, each with its reach per call.

    * Class reminders select sessions in a one hour band, so one call shows an hour of sends.
    * Event reminders and "starting now" look one tick ahead, so they are called every tick.
    * The voting reminders fire at one instant per cycle (the close less the lead days), so
      two calls a day apart see it, across a month change too.

    The Discord-only voting reminder is not listed: it sends no email.
    """
    from classes.tasks import class_reminder_occurrences
    from membership.events import event_happening_now_occurrences, event_reminder_occurrences
    from membership.voting import closing_soon_occurrences, officers_closing_soon_occurrences, vote_soon_occurrences

    whole_window = timedelta(hours=UPCOMING_HOURS) - DEFAULT_WINDOW
    return [
        (class_reminder_occurrences, timedelta(hours=1)),
        (event_reminder_occurrences, DEFAULT_WINDOW),
        (event_happening_now_occurrences, DEFAULT_WINDOW),
        (closing_soon_occurrences, whole_window),
        (vote_soon_occurrences, whole_window),
        (officers_closing_soon_occurrences, whole_window),
    ]


def _due_emails(now: datetime, sources: list[Source]) -> list[ScheduledOccurrence]:
    """Every email occurrence whose send lands in ``[now, now + 24h)``, the cron's ticks walked.

    Each source is called at ``now`` and then once per its stride, as the cron would call it
    at those ticks, and keeps what it would fire in the window: the same half open rule as
    :meth:`ScheduledOccurrence.is_due`, applied to the whole day at once.
    """
    end = now + timedelta(hours=UPCOMING_HOURS)
    due = []
    for source, stride in sources:
        tick = now
        while tick < end:
            for occurrence in source(tick):
                sends_at = occurrence.anchor + occurrence.offset
                event = get_event(occurrence.event_key)
                sends_email = event.has_channel(Channel.EMAIL) or bool(occurrence.email_to)
                if sends_email and now <= sends_at < end:
                    due.append(occurrence)
            tick += stride
    return due


def _target_refs(occurrence: ScheduledOccurrence) -> list[str]:
    """The ledger ``target_ref`` of everyone the occurrence's email reaches."""
    from core.events import resolvers

    if occurrence.email_to:
        addresses = [occurrence.email_to] if isinstance(occurrence.email_to, str) else occurrence.email_to
        return [f"email:{address.strip().lower()}" for address in addresses if address.strip()]
    recipients = resolvers.resolve(get_event(occurrence.event_key).recipient, occurrence.context)
    return [f"user:{user.pk}" for user, _reason in recipients]


def _subject_key(occurrence: ScheduledOccurrence) -> tuple[str, Any]:
    """What an occurrence is about: its class, its event or its voting cycle.

    Fails loudly for a target no timed email source produces today.
    """
    from classes.models import Registration
    from membership.models import CommunityEvent

    target = occurrence.target
    if target is None:
        return ("cycle", occurrence.context["cycle_label"])
    if isinstance(target, Registration):
        return ("class", target.class_offering_id)
    if isinstance(target, CommunityEvent):
        return ("event", target.title)
    raise ValueError(f"No Email Queue subject for a {type(target).__name__} target")


def upcoming_timed_emails(now: datetime) -> list[UpcomingEmail]:
    """The timed emails due in the next 24 hours, grouped by event, subject and send time.

    Read only: the sources are called as the cron calls them, but nothing is emitted. An
    occurrence whose every recipient the ledger already holds is left out, and the reach
    counts only the people still owed it.
    """
    from classes.models import ClassOffering

    unique: dict[tuple[str, str, tuple[str, ...]], ScheduledOccurrence] = {}
    for occurrence in _due_emails(now, timed_email_sources()):
        # Two calls a day apart see a voting reminder twice; one row per person and request.
        unique[(occurrence.event_key, occurrence.period, tuple(_target_refs(occurrence)))] = occurrence
    due = list(unique.values())
    refs = {id(occurrence): list(key[2]) for key, occurrence in unique.items()}
    delivered = set(
        EventDelivery.objects.filter(
            channel=Channel.EMAIL.value,
            event_key__in={occurrence.event_key for occurrence in due},
            period__in={occurrence.period for occurrence in due},
        ).values_list("event_key", "target_ref", "period")
    )
    reach: Counter[tuple[str, tuple[str, Any], datetime]] = Counter()
    for occurrence in due:
        owed = [ref for ref in refs[id(occurrence)] if (occurrence.event_key, ref, occurrence.period) not in delivered]
        if owed:
            key = (occurrence.event_key, _subject_key(occurrence), occurrence.anchor + occurrence.offset)
            reach[key] += len(owed)
    class_titles = dict(
        ClassOffering.objects.filter(
            pk__in=[subject[1] for _key, subject, _at in reach if subject[0] == "class"]
        ).values_list("pk", "title")
    )
    upcoming = [
        UpcomingEmail(
            send_at=send_at,
            label=get_event(event_key).label,
            about=class_titles[subject[1]] if subject[0] == "class" else str(subject[1]),
            reach=count,
        )
        for (event_key, subject, send_at), count in reach.items()
    ]
    return sorted(upcoming, key=lambda email: (email.send_at, email.label))
