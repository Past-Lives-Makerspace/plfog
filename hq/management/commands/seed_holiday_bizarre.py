"""Seed Lee's Holiday Bizarre HQ from :mod:`hq.holiday_bizarre`: the hub, its nine doors, four goals and 21 shifts.

A command, never a migration (STANDARDS §3 and §10): deploys do not run it. An admin loads the
same content from ``/hq/new/`` (the Load Lee's HQ button, :func:`hq.views.setup`), so the command
is for a shell or a Render job::

    manage.py seed_holiday_bizarre [--event <pk>] [--dry-run]

``--event`` is optional: without it the hub is made with no event, and an admin links the
calendar event later from Edit HQ.

The acceptance criterion: it runs twice without duplicates and never touches an admin's
edits. A hub that already exists keeps its title, eyebrow, intro, footer note and open state;
only its event moves, and only when ``--event`` names a different one, which the output says;
with no ``--event`` it is left alone. The doors are :meth:`hq.models.EventHub.seed_default_cards`:
live doors keyed on kind, a built door's placeholder adopted in place, placeholders made only on
a hub that held none, and words still at their old defaults given Lee's current ones. The four
goals and the 21 shifts are each made only on a hub that holds none of that kind of row: both
are editable and have no stable key, so a renamed or deleted row never grows a sibling on the
next run. Sign-ups, bottles, logos and marketing actions are never read.
``--dry-run`` runs the whole seed inside a transaction and rolls it back, so the counts are
the real ones and nothing is written.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from hq.holiday_bizarre import GOALS, HUB, SHIFTS
from hq.models import CardSeed, EventHub, HubGoal, VolunteerShift
from membership.models import CommunityEvent

# How many published upcoming events a refusal lists, so the right pk is a copy away.
HINT_LIMIT = 10


@dataclass
class Tally:
    """Rows of one kind the seed made (``created``) or found already there and left alone (``kept``)."""

    created: int = 0
    kept: int = 0


@dataclass
class SeedReport:
    """What one run did, for the command's lines.

    ``blocked_by`` is the other hub that was open when this one was created, which kept the
    new one closed (only one hub can be open); None otherwise. ``event_changed`` is set when
    an existing hub was pointed at the event ``--event`` named, the one thing a re-run writes
    on a hub.
    """

    hub: EventHub
    hub_created: bool
    blocked_by: EventHub | None = None
    event_changed_to: CommunityEvent | None = None
    cards: CardSeed = field(default_factory=CardSeed)
    goals: Tally = field(default_factory=Tally)
    shifts: Tally = field(default_factory=Tally)

    @property
    def event_changed(self) -> bool:
        return self.event_changed_to is not None

    def _hub_state(self) -> str:
        if self.hub_created:
            if self.blocked_by is None:
                return "created, open"
            return f"created, closed ({self.blocked_by.slug} is open; open this one from Edit HQ)"
        if self.event_changed_to is not None:
            return f"kept as edited, event changed to {self.event_changed_to.pk} ({self.event_changed_to.title})"
        return "kept as edited"

    def lines(self) -> list[str]:
        """One line per model, in seeding order."""
        cards = self.cards
        adopted = f" Opened as live doors: {', '.join(cards.adopted)}." if cards.adopted else ""
        reworded = f" {cards.reworded} took Lee's latest words." if cards.reworded else ""
        skipped = (
            " The hub already held its placeholders, so none was added and they were left as edited."
            if cards.placeholders_skipped
            else ""
        )
        return [
            f"Hub {self.hub.slug} ({self.hub.title}): {self._hub_state()}.",
            f"Cards: {cards.created} created, {cards.kept} kept as edited.{adopted}{reworded}{skipped}",
            f"Goals: {self.goals.created} created, {self.goals.kept} kept as edited.",
            f"Shifts: {self.shifts.created} created, {self.shifts.kept} kept as edited.",
        ]

    def summary(self, event: CommunityEvent | None, *, dry_run: bool) -> str:
        """The last line: the counts in one sentence, in the conditional for a dry run."""
        if self.hub_created:
            hub = "created"
        elif self.event_changed:
            hub = "kept, event changed"
        else:
            hub = "kept"
        counts = (
            f"{self.cards.created} cards, {self.goals.created} goals, {self.shifts.created} shifts created; "
            f"{self.cards.kept} cards, {self.goals.kept} goals, {self.shifts.kept} shifts kept."
        )
        target = f"event {event.pk} ({event.title})" if event is not None else "no event"
        if dry_run:
            return f"Dry run for {target}, rolled back: hub would be {hub}; {counts}"
        return f"Seeded for {target}: hub {hub}; {counts}"


def published_event(pk: int | None) -> CommunityEvent | None:
    """The published calendar event at ``pk`` (None when no pk was given), or a :class:`CommandError` that says what to do instead."""
    if pk is None:
        return None
    event = CommunityEvent.objects.filter(pk=pk).first()
    if event is None:
        raise CommandError(f"No calendar event has pk {pk}.{_hint()}")
    if event.moderation_state != CommunityEvent.ModerationState.PUBLISHED:
        state = event.get_moderation_state_display().lower()
        raise CommandError(
            f"Event {pk} ({event.title}) is {state}, not published. Publish it first, then run this again."
        )
    return event


def _hint() -> str:
    """The published upcoming events, pk first, so the right one is a copy away; or where to make one."""
    upcoming = CommunityEvent.objects.published().filter(starts_at__gte=timezone.now()).order_by("starts_at")
    rows = list(upcoming[:HINT_LIMIT])
    if not rows:
        return " No published upcoming event exists yet: make the Holiday Bizarre at /events/add/ first."
    listed = "; ".join(f"{event.pk} {event.title} ({event.when_display})" for event in rows)
    return f" Published upcoming events: {listed}."


def seed(event: CommunityEvent | None = None) -> SeedReport:
    """Write Lee's HQ, creating what is missing and leaving the rest alone, and say what happened.

    ``event`` links the hub to its calendar event; None makes a new hub with none and leaves an
    existing hub's event as it is.
    """
    hub = EventHub.objects.filter(slug=HUB.slug).first()
    if hub is None:
        blocked_by = EventHub.objects.filter(is_open=True).first()
        hub = EventHub.objects.create(
            slug=HUB.slug,
            title=HUB.title,
            eyebrow=HUB.eyebrow,
            intro=HUB.intro,
            footer_note=HUB.footer_note,
            event=event,
            is_open=blocked_by is None,
        )
        report = SeedReport(hub=hub, hub_created=True, blocked_by=blocked_by)
    else:
        changed = event is not None and hub.event_id != event.pk
        report = SeedReport(hub=hub, hub_created=False, event_changed_to=event if changed else None)
        if changed:
            hub.event = event
            hub.save(update_fields=["event"])
    report.cards = hub.seed_default_cards()
    _seed_goals(hub, report.goals)
    _seed_shifts(hub, report.shifts)
    return report


def _seed_goals(hub: EventHub, tally: Tally) -> None:
    """The four goals, only on a hub that holds none; goals are editable, so an existing set is kept as it is."""
    held = hub.goals.count()
    if held:
        tally.kept = held
        return
    HubGoal.objects.bulk_create(
        HubGoal(
            hub=hub,
            label=spec.label,
            kind=spec.kind,
            target=spec.target,
            is_money=spec.is_money,
            sort_order=position,
        )
        for position, spec in enumerate(GOALS)
    )
    tally.created = len(GOALS)


def _seed_shifts(hub: EventHub, tally: Tally) -> None:
    """The 21 shifts, ordered as listed within their phase, only on a hub that holds none; an existing sheet is kept."""
    held = hub.shifts.count()
    if held:
        tally.kept = held
        return
    positions: Counter[str] = Counter()
    rows: list[VolunteerShift] = []
    for spec in SHIFTS:
        rows.append(
            VolunteerShift(
                hub=hub,
                phase=spec.phase,
                role=spec.role,
                when_text=spec.when_text,
                need=spec.need,
                description=spec.description,
                requires_olcc=spec.requires_olcc,
                sort_order=positions[spec.phase],
            )
        )
        positions[spec.phase] += 1
    VolunteerShift.objects.bulk_create(rows)
    tally.created = len(rows)


class Command(BaseCommand):
    help = (
        "Create the Holiday Bizarre HQ (hub, nine doors, four goals, 21 shifts), linked to a published "
        "calendar event when --event names one. Runs twice without duplicates and never touches an admin's edits; --dry-run rolls everything "
        "back and writes nothing."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--event",
            type=int,
            default=None,
            help="Optional: the pk of the PUBLISHED calendar event the HQ organises. Leave it out and link the event from Edit HQ.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run the whole seed inside a transaction and roll it back: the counts, nothing written.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        event = published_event(options["event"])
        dry_run = bool(options["dry_run"])
        with transaction.atomic():
            report = seed(event)
            if dry_run:
                transaction.set_rollback(True)
        for line in report.lines():
            self.stdout.write(line)
        self.stdout.write(self.style.SUCCESS(report.summary(event, dry_run=dry_run)))
