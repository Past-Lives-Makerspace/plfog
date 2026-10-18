"""Seed Lee's Holiday Bizarre HQ from :mod:`hq.holiday_bizarre`: the hub, its nine doors, four goals and 21 shifts.

A command, never a migration (STANDARDS §3 and §10): deploys do not run it, and the calendar
event it organises is made by hand at ``/events/add/`` first. Run it once after that, as a
Render job::

    manage.py seed_holiday_bizarre --event <pk> [--dry-run]

It is idempotent. A re-run refreshes what the module owns: the hub's words and its event,
each goal's kind, target and money flag, and each shift's phase, need, description, permit
flag and order. It never touches what an admin owns once the rows exist: whether the hub is
open, the cards (titles, bullets, audiences, named members, hidden flags, order; a renamed
placeholder never gets a sibling), a typed-in goal figure, and every sign-up, bottle and
logo. ``--dry-run`` runs the whole seed inside a transaction and rolls it back, so the
counts are the real ones and nothing is written.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from hq.holiday_bizarre import CARDS, GOALS, HUB, SHIFTS
from hq.models import EventHub, HubCard, HubGoal, VolunteerShift
from membership.models import CommunityEvent

# How many published upcoming events a refusal lists, so the right pk is a copy away.
HINT_LIMIT = 10


@dataclass
class Tally:
    """Rows of one kind the seed made (``created``) or found already there (``existing``)."""

    created: int = 0
    existing: int = 0

    def count(self, created: bool) -> None:
        if created:
            self.created += 1
        else:
            self.existing += 1


@dataclass
class SeedReport:
    """What one run did, for the command's lines.

    ``blocked_by`` is the other hub that was open when this one was created, which kept the
    new one closed (only one hub can be open); None otherwise. Existing cards are kept as the
    admin left them, so ``cards.existing`` is "kept"; existing goals and shifts are refreshed,
    so theirs is "updated".
    """

    hub: EventHub
    hub_created: bool
    blocked_by: EventHub | None = None
    placeholders_skipped: bool = False
    cards: Tally = field(default_factory=Tally)
    goals: Tally = field(default_factory=Tally)
    shifts: Tally = field(default_factory=Tally)

    def lines(self) -> list[str]:
        """One line per model, in seeding order."""
        if not self.hub_created:
            state = "updated"
        elif self.blocked_by is None:
            state = "created, open"
        else:
            state = f"created, closed ({self.blocked_by.slug} is open; open this one from Edit HQ)"
        skipped = (
            " The six placeholders were already on the hub and were left as edited."
            if self.placeholders_skipped
            else ""
        )
        return [
            f"Hub {self.hub.slug} ({self.hub.title}): {state}.",
            f"Cards: {self.cards.created} created, {self.cards.existing} kept as edited.{skipped}",
            f"Goals: {self.goals.created} created, {self.goals.existing} updated.",
            f"Shifts: {self.shifts.created} created, {self.shifts.existing} updated.",
        ]

    def summary(self, event: CommunityEvent, *, dry_run: bool) -> str:
        """The last line: the counts in one sentence, in the conditional for a dry run."""
        hub = "created" if self.hub_created else "updated"
        counts = (
            f"{self.cards.created} cards, {self.goals.created} goals, {self.shifts.created} shifts created; "
            f"{self.cards.existing} cards kept; {self.goals.existing} goals, {self.shifts.existing} shifts updated."
        )
        if dry_run:
            return f"Dry run for event {event.pk} ({event.title}), rolled back: hub would be {hub}; {counts}"
        return f"Seeded for event {event.pk} ({event.title}): hub {hub}; {counts}"


def published_event(pk: int | None) -> CommunityEvent:
    """The published calendar event at ``pk``, or a :class:`CommandError` that says what to do instead."""
    if pk is None:
        raise CommandError(f"--event <pk> is required: the published calendar event the HQ organises.{_hint()}")
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


def seed(event: CommunityEvent) -> SeedReport:
    """Write Lee's HQ for ``event``, creating or refreshing each row, and say what happened."""
    blocked_by = EventHub.objects.filter(is_open=True).exclude(slug=HUB.slug).first()
    words: dict[str, Any] = {
        "title": HUB.title,
        "eyebrow": HUB.eyebrow,
        "intro": HUB.intro,
        "footer_note": HUB.footer_note,
        "event": event,
    }
    hub, created = EventHub.objects.update_or_create(
        slug=HUB.slug, defaults=words, create_defaults={**words, "is_open": blocked_by is None}
    )
    report = SeedReport(hub=hub, hub_created=created, blocked_by=blocked_by if created else None)
    _seed_cards(hub, report)
    _seed_goals(hub, report.goals)
    _seed_shifts(hub, report.shifts)
    return report


def _seed_cards(hub: EventHub, report: SeedReport) -> None:
    """One card per spec: a live kind by kind, a placeholder by its original title.

    An existing card is left as the admin edited it. Once the hub holds its six placeholders
    none is touched, so a placeholder an admin renamed never gets a sibling.
    """
    wanted = sum(1 for spec in CARDS if spec["kind"] == HubCard.Kind.PLACEHOLDER)
    held = hub.cards.filter(kind=HubCard.Kind.PLACEHOLDER).count()
    if held >= wanted:
        report.placeholders_skipped = True
        report.cards.existing += held
    for position, spec in enumerate(CARDS):
        is_placeholder = spec["kind"] == HubCard.Kind.PLACEHOLDER
        if is_placeholder and report.placeholders_skipped:
            continue
        lookup = {"kind": spec["kind"]}
        if is_placeholder:
            lookup["title"] = spec["title"]
        defaults = {key: value for key, value in spec.items() if key not in lookup}
        _card, created = HubCard.objects.get_or_create(hub=hub, **lookup, defaults={**defaults, "sort_order": position})
        report.cards.count(created)


def _seed_goals(hub: EventHub, tally: Tally) -> None:
    """One goal per spec, keyed on its label; the target, kind and money flag are refreshed, the typed-in figure kept."""
    for position, spec in enumerate(GOALS):
        _goal, created = HubGoal.objects.update_or_create(
            hub=hub,
            label=spec.label,
            defaults={"kind": spec.kind, "target": spec.target, "is_money": spec.is_money, "sort_order": position},
        )
        tally.count(created)


def _seed_shifts(hub: EventHub, tally: Tally) -> None:
    """One shift per spec, keyed on its role and when text, ordered as listed within its phase; sign-ups are untouched."""
    positions: Counter[str] = Counter()
    for spec in SHIFTS:
        position = positions[spec.phase]
        positions[spec.phase] += 1
        _shift, created = VolunteerShift.objects.update_or_create(
            hub=hub,
            role=spec.role,
            when_text=spec.when_text,
            defaults={
                "phase": spec.phase,
                "need": spec.need,
                "description": spec.description,
                "requires_olcc": spec.requires_olcc,
                "sort_order": position,
            },
        )
        tally.count(created)


class Command(BaseCommand):
    help = (
        "Create or refresh the Holiday Bizarre HQ (hub, nine doors, four goals, 21 shifts) for a published "
        "calendar event. Idempotent; --dry-run rolls everything back and writes nothing."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--event",
            type=int,
            default=None,
            help="The pk of the PUBLISHED calendar event the HQ organises (made first at /events/add/).",
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
