"""The Holiday Bizarre HQ: one hub page per event, its door cards and its goal chips.

An :class:`EventHub` is the page Lee's organising hub became: a hero band for the event, a row of goals, and the door
cards that each open one part of the event. Since #1307 those are four: RSVP, Volunteer Sign-up, Vendor Tables and the
PL Ledger. The cards are rows, not code, so whoever runs the HQ (admins and guild officers, ``hq.access``) can retitle,
hide and reorder them. The event itself stays a ``CommunityEvent``: the hub only points at it for the date, the
location and the RSVP door.

Behind the ``volunteers`` card a :class:`VolunteerShift` is one job on Lee's sign-up sheet and a :class:`ShiftSignup`
one person on it. Behind ``vendor_tables`` a :class:`VendorTable` is one member artist's table request and a
:class:`VendorFloor` the space map's capacity. Behind ``ledger``: :class:`LedgerSettings` holds the board's approval and
the drink prices, a :class:`BudgetLine` is one line of the budget and a :class:`LedgerEntry` one gift, purchase or
refund against it with its receipt; a :class:`BarSale` is one sale at the bar; a :class:`DoorCount` one tap on the door
clicker, a :class:`LaunchTask` one line of the launch checklist, a :class:`StaffTime` one stretch of staff time and a
:class:`GuildDemo` one guild's demo plans. #1307 removed the wine wall, the Marketing and Sponsorship Trackers, the
Market Check-in and the flyer page, and their models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.db.models.functions import Lower
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import lazy

from core.files import delete_if_unreferenced, delete_orphan_on_replace
from core.images import normalize_field_if_uploaded
from membership.models import CommunityEvent, Guild, Member


@dataclass(frozen=True)
class Countdown:
    """The hero band's figure: ``big`` is the number (or the word when there is none), ``small`` its label."""

    big: str
    small: str

    @property
    def label(self) -> str:
        return f"{self.big} {self.small}".strip()


#: The ``from`` value the RSVP card puts on its link to the event page (``?from=hq``). The event page reads it
#: to show a way back to the hub; nothing else carries it, so the page's canonical link, QR and share links
#: (``CommunityEvent.public_url``) never do.
HQ_MARKER = "hq"


class EventHubManager(models.Manager["EventHub"]):
    def open(self) -> EventHub | None:
        """The hub whose page ``/hq/`` opens, with its event, or None while no hub is open.

        The partial unique constraint keeps ``is_open`` to one row, so ``first()`` is exact.
        """
        return self.filter(is_open=True).select_related("event").first()

    def back_from_event(self, event: CommunityEvent, *, admin: bool) -> EventHub | None:
        """The hub an event page links back to when the RSVP card opened it (``?from=hq``).

        Args:
            event: The event the page shows.
            admin: The viewer's effective admin role. An admin may open any hub, so any hub linked to the event
                answers, the open one first; anyone else only the open hub, as ``hq.access.hub_or_404`` allows.

        Returns:
            The hub, or None when no hub the viewer may open is linked to ``event``.
        """
        hubs = self.filter(event=event)
        if not admin:
            hubs = hubs.filter(is_open=True)
        return hubs.order_by("-is_open", "-created_at", "-pk").first()


class EventHub(models.Model):
    """One event's HQ page. The ``is_open`` hub is the one the sidebar entry and ``/hq/`` land on."""

    slug = models.SlugField(
        max_length=60,
        unique=True,
        help_text="The page's address under /hq/, e.g. holiday-bizarre-2026. Letters, numbers and hyphens.",
    )
    title = models.CharField(max_length=120, help_text="The page title in the hero band, e.g. Holiday Bizarre HQ.")
    eyebrow = models.CharField(
        max_length=120,
        blank=True,
        help_text="The small gold line over the title, e.g. Past Lives Makerspace · 2nd Annual.",
    )
    event = models.ForeignKey(
        CommunityEvent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hubs",
        help_text="The calendar event this HQ organises: its date, location and RSVP page. The page renders without one.",
    )
    hero_image = models.ImageField(
        upload_to="hq/hero/",
        blank=True,
        help_text="A wide photo behind the hero band, resized on upload. Optional.",
    )
    intro = models.TextField(blank=True, help_text="A few lines under the hero band, shown as paragraphs.")
    footer_note = models.TextField(
        blank=True,
        help_text="Small print at the foot of the page, e.g. the nonprofit line about gifts and sponsorships.",
    )
    is_open = models.BooleanField(
        default=False,
        help_text="Whether this is the HQ members reach from the sidebar. Only one hub can be open at a time.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When this hub was made.")

    objects = EventHubManager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["is_open"], condition=Q(is_open=True), name="uq_eventhub_one_open"),
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args: Any, **kwargs: Any) -> None:
        normalize_field_if_uploaded(self, "hero_image", settings.IMAGE_MAX_LONG_EDGE_HERO)
        delete_orphan_on_replace(self, "hero_image")
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("hq:page", args=[self.slug])

    def remove_hero(self) -> None:
        """Clear the hero image; ``save`` deletes the stored file once nothing points at it."""
        if not self.hero_image:
            return
        self.hero_image = ""  # type: ignore[assignment]  # a FieldFile accepts the empty name
        self.save(update_fields=["hero_image"])

    def countdown_parts(self, now: datetime) -> Countdown:
        """The hero band's figure, by Portland days until the event starts, as the number and its label.

        ``("N", "days to go")`` and ``("1", "day to go")`` before, ``("Today", "")`` on the day,
        ``("Thank you", "")`` after. Both blank while the hub has no event, so the band renders
        without the figure.
        """
        if self.event is None:
            return Countdown("", "")
        days = (timezone.localdate(self.event.starts_at) - timezone.localdate(now)).days
        if days > 1:
            return Countdown(str(days), "days to go")
        if days == 1:
            return Countdown("1", "day to go")
        if days == 0:
            return Countdown("Today", "")
        return Countdown("Thank you", "")

    def countdown_label(self, now: datetime) -> str:
        """:meth:`countdown_parts` as one line: "N days to go", "1 day to go", "Today", "Thank you", or blank."""
        return self.countdown_parts(now).label

    @property
    def date_line(self) -> str:
        """The event's day and hours in the band, with its location when it has one; blank with no event."""
        if self.event is None:
            return ""
        when = self.event.when_display
        return f"{when} · {self.event.location}" if self.event.location else when

    def seed_default_cards(self) -> CardSeed:
        """Lee's doors (:data:`DEFAULT_CARDS`) on this hub: made where missing, adopted, reworded, never doubled.

        A live door is found by kind (unique on a hub, so a renamed one is still found). A door whose page is
        built (:data:`BUILT_DOORS`) that the hub still holds as a placeholder with the door's title is turned
        into the live kind in place, so its pk, hidden flag and order stay; it is made
        only when neither the kind nor that placeholder exists. A placeholder has no stable key, so
        placeholders are made only on a hub with no cards at all. On every card found, a field still carrying the
        door's previous default words takes Lee's current ones; a field an admin edited is kept.
        """
        result = CardSeed()
        # Placeholders are made only on a hub with no cards at all: a hub whose admin deleted them keeps
        # them deleted, however often Update the Doors is pressed.
        fresh = not self.cards.exists()
        result.placeholders_skipped = not fresh
        for position, door in enumerate(DEFAULT_CARDS):
            card = self.cards.filter(kind=door.kind).first() if door.is_live else None
            if card is None:
                card = self.cards.filter(kind=HubCard.Kind.PLACEHOLDER, title=door.title).first()
            if card is None:
                if door.is_live or fresh:
                    HubCard.objects.create(hub=self, sort_order=position, **door.fields())
                    result.created += 1
                    if not fresh and door.door in BUILT_DOORS:
                        result.added_beside.append(door.title)
                continue
            changed = door.refresh_words(card)
            if card.kind != door.kind:
                card.kind = door.kind
                changed.append("kind")
                result.adopted.append(card.title)
            elif door.is_live:
                result.kept += 1
            if changed:
                card.save(update_fields=changed)
                result.reworded += "bullets" in changed or "button_text" in changed
        if not fresh:
            # Every placeholder the hub held and still holds, renamed ones too (an adopted one counts as adopted).
            result.kept += self.cards.filter(kind=HubCard.Kind.PLACEHOLDER).count()
        return result


@dataclass
class CardSeed:
    """What :meth:`EventHub.seed_default_cards` did: cards made, kept as edited, adopted (by title) and reworded.

    ``placeholders_skipped`` is set when the hub already held placeholders, so none was made.
    """

    created: int = 0
    kept: int = 0
    adopted: list[str] = field(default_factory=list)
    reworded: int = 0
    placeholders_skipped: bool = False
    # Live doors made on a hub that already had cards: a renamed placeholder may sit beside them.
    added_beside: list[str] = field(default_factory=list)
    # The labels of Lee's goals the same press added (``hq.services.seed_goals``), in the order they were added.
    goals_added: list[str] = field(default_factory=list)
    # The label of the typed in goal the same press turned into the counted event cash, Lee's Raised (blank when none;
    # the sentence names it Raised whatever its label, since that is what it now counts).
    goals_adopted: str = ""

    @property
    def summary(self) -> str:
        """One sentence for the admin who pressed Update the Doors."""
        parts: list[str] = []
        if self.adopted:
            parts.append(f"opened {', '.join(self.adopted)}")
        if self.created:
            parts.append(f"added {self.created} door{'' if self.created == 1 else 's'}")
        if self.reworded:
            parts.append(f"gave {self.reworded} card{'' if self.reworded == 1 else 's'} Lee's latest words")
        if self.goals_added:
            parts.append(_goals_phrase(self.goals_added))
        if self.goals_adopted:
            parts.append("Raised now counts itself")
        if not parts:
            return "The doors are up to date."
        sentence = f"Doors updated: {'; '.join(parts)}."
        if self.added_beside:
            sentence += (
                f" New: {', '.join(self.added_beside)}."
                " If an old Not shared yet card for it is still on the page, delete that one."
            )
        return sentence


def _goals_phrase(labels: list[str]) -> str:
    """ "added the Member tables goal", or "added the RSVPs and Member tables goals" for several."""
    if len(labels) == 1:
        return f"added the {labels[0]} goal"
    return f"added the {', '.join(labels[:-1])} and {labels[-1]} goals"


class HubCard(models.Model):
    """One door on the HQ page: who it is for and what is behind it.

    Every card opens to whoever runs the HQ (admins and ACTIVE guild officers, ``hq.access.can_run_hq``); a hidden
    one to admins alone.
    """

    class Kind(models.TextChoices):
        RSVP = "rsvp", "RSVP"
        VOLUNTEERS = "volunteers", "Volunteer shifts"
        VENDOR_TABLES = "vendor_tables", "Vendor Tables"
        LEDGER = "ledger", "PL Ledger"
        PLACEHOLDER = "placeholder", "Not shared yet"

    class Direction(models.TextChoices):
        UP = "up", "Up"
        DOWN = "down", "Down"

    # How many bullets a card shows; the edit form refuses more.
    MAX_BULLETS = 4

    hub = models.ForeignKey(
        EventHub, on_delete=models.CASCADE, related_name="cards", help_text="The HQ this card is on."
    )
    kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        default=Kind.PLACEHOLDER,
        help_text="Which page the card opens. A placeholder opens nothing and reads Not shared yet.",
    )
    title = models.CharField(max_length=120, help_text="The card's title, e.g. Volunteer Sign-up.")
    who_for = models.CharField(
        max_length=120, blank=True, help_text="The small line over the title: who this door is for."
    )
    bullets = models.TextField(blank=True, help_text="What is behind the door, one line per bullet, up to four.")
    button_text = models.CharField(max_length=60, default="Enter", help_text="The button at the foot of a live card.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Position on the page; lower comes first.")
    is_hidden = models.BooleanField(
        default=False,
        help_text="Marks a card still being set up: it shows with a Hidden chip to everyone who runs the HQ.",
    )

    class Meta:
        ordering = ["sort_order", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["hub", "kind"],
                condition=~Q(kind="placeholder"),
                name="uq_hubcard_live_kind",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def bullet_lines(self) -> list[str]:
        """The bullets as a list, blank lines dropped."""
        return [line.strip() for line in self.bullets.splitlines() if line.strip()]

    @property
    def url(self) -> str:
        """Where the card's button goes, or "" for a card that opens nothing yet (rendered as a plain tile)."""
        if self.kind == self.Kind.RSVP:
            # The marker tells the event page it was opened from the HQ, so it shows a way back (HQ_MARKER).
            event_page = reverse("hub_event_detail", args=[self.hub.event_id]) if self.hub.event_id is not None else ""
            return f"{event_page}?from={HQ_MARKER}" if event_page else ""
        if self.kind == self.Kind.VOLUNTEERS:
            return reverse("hq:volunteers", args=[self.hub.slug])
        if self.kind == self.Kind.VENDOR_TABLES:
            return reverse("hq:vendors", args=[self.hub.slug])
        if self.kind == self.Kind.LEDGER:
            return reverse("hq:ledger", args=[self.hub.slug])
        # A placeholder opens nothing and reads Not shared yet.
        return ""

    def move(self, direction: str) -> None:
        """Swap places with the neighbour above or below; at either end nothing moves.

        Every sibling is renumbered from zero afterwards, so two cards seeded with the same
        ``sort_order`` cannot keep trading places without visibly moving.
        """
        if direction not in self.Direction.values:
            raise ValueError(f"Unknown direction '{direction}'.")
        siblings = list(self.hub.cards.order_by("sort_order", "pk"))
        index = next(position for position, card in enumerate(siblings) if card.pk == self.pk)
        other = index - 1 if direction == self.Direction.UP else index + 1
        if other < 0 or other >= len(siblings):
            return
        siblings[index], siblings[other] = siblings[other], siblings[index]
        for position, card in enumerate(siblings):
            if card.sort_order != position:
                HubCard.objects.filter(pk=card.pk).update(sort_order=position)
        self.sort_order = other


class HubGoal(models.Model):
    """One chip in the goal band: a label, a target and where it stands.

    A ``manual`` goal's figure is typed in by an admin; the live kinds are counted from the
    event's RSVPs, the volunteer shifts (C2), the confirmed vendor tables (D3), the event cash (the bar's
    sales, in whole dollars, #1307) and the
    guilds with a demo planned on the ledger (D6c) by :func:`hq.services.goal_rows`. A goal with no target is a plain count.
    """

    class Kind(models.TextChoices):
        MANUAL = "manual", "Typed in by hand"
        RSVPS = "rsvps", "People going (RSVPs)"
        VOLUNTEER_SPOTS = "volunteer_spots", "Volunteer spots filled"
        MEMBER_TABLES = "member_tables", "Vendor tables confirmed"
        EVENT_CASH = "event_cash", "Event cash"
        GUILDS = "guilds", "Guild demos planned"

    hub = models.ForeignKey(
        EventHub, on_delete=models.CASCADE, related_name="goals", help_text="The HQ this goal is on."
    )
    label = models.CharField(max_length=80, help_text="The chip's label, e.g. Raised or Bottles.")
    kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        default=Kind.MANUAL,
        help_text="Where the figure comes from: typed in, or counted live from one part of the event.",
    )
    target = models.PositiveIntegerField(
        default=0, help_text="The goal, a whole number (dollars for a money goal). 0 shows a plain count."
    )
    current_value = models.PositiveIntegerField(
        default=0,
        help_text="Where a typed in goal stands. Ignored for the counted kinds.",
    )
    is_money = models.BooleanField(default=False, help_text="Show the figures as dollars.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Position in the band; lower comes first.")

    class Meta:
        ordering = ["sort_order", "pk"]

    def __str__(self) -> str:
        return f"{self.label} ({self.target})"


# The doors whose page was built in Track D. The seed turns a placeholder titled as one of these into its live kind.
# #1307 removed the Marketing and Sponsorship Trackers, the Market Check-in and the flyer page.
BUILT_DOORS: frozenset[str] = frozenset({HubCard.Kind.VENDOR_TABLES, HubCard.Kind.LEDGER})

# The doors that were live from the start of Track C (#1307 removed the wine wall).
_FIRST_DOORS: frozenset[str] = frozenset({HubCard.Kind.RSVP, HubCard.Kind.VOLUNTEERS})


@dataclass(frozen=True)
class DoorContent:
    """One of Lee's doors as the seed writes it.

    ``door`` is the kind the card is once its page is built; until then it is seeded as a placeholder
    with the same words. ``old_bullets`` and ``old_button_text`` are the words the card was seeded with
    before Lee's update: a card still carrying them takes the current ones.
    """

    door: str
    who_for: str
    title: str
    bullets: str
    button_text: str
    old_bullets: str
    old_button_text: str

    @property
    def is_live(self) -> bool:
        return self.door in _FIRST_DOORS or self.door in BUILT_DOORS

    @property
    def kind(self) -> str:
        """The kind the seed writes: the door's own once its page is built, else a placeholder."""
        return self.door if self.is_live else HubCard.Kind.PLACEHOLDER

    def fields(self) -> dict[str, str]:
        """The card's fields for a new row."""
        return {
            "kind": self.kind,
            "who_for": self.who_for,
            "title": self.title,
            "bullets": self.bullets,
            "button_text": self.button_text,
        }

    def refresh_words(self, card: HubCard) -> list[str]:
        """Give ``card`` the current bullets and button where it still carries the old defaults; the fields changed."""
        changed: list[str] = []
        if card.bullet_lines == _lines(self.old_bullets) and card.bullet_lines != _lines(self.bullets):
            card.bullets = self.bullets
            changed.append("bullets")
        if card.button_text == self.old_button_text and card.button_text != self.button_text:
            card.button_text = self.button_text
            changed.append("button_text")
        return changed


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


# Lee's doors in her order (plan Appendix), the four #1307 kept: her who-for lines and titles, her current bullets
# and buttons (her HQ as she shared it in October), and the words each was first seeded with.
DEFAULT_CARDS: tuple[DoorContent, ...] = (
    DoorContent(
        door=HubCard.Kind.RSVP,
        who_for="Members and guests",
        title="Going to the Bizarre?",
        bullets=(
            "Tell us you're coming\n"
            "Get event updates by email\n"
            "Eventbrite RSVPs counted too\n"
            "Staff can add guests and check people in"
        ),
        button_text="I'm going",
        old_bullets=(
            "Tell us you're coming\n"
            "Get email updates before the night\n"
            "Eventbrite RSVPs are counted too\n"
            "Staff can add guests and check people in"
        ),
        old_button_text="I'm going",
    ),
    DoorContent(
        door=HubCard.Kind.VOLUNTEERS,
        who_for="Volunteers",
        title="Volunteer Sign-up",
        bullets=(
            "Bartend, door, security\n"
            "Setup, teardown and cleanup\n"
            "Wine wall, tour guides, guild demos\n"
            "Flyer runs and decorating"
        ),
        button_text="Sign up to volunteer",
        old_bullets=(
            "Bartend, work the door or keep an eye on things\n"
            "Setup, teardown and cleanup crews\n"
            "Wine wall, tour guides and guild demos\n"
            "Flyer runs and decorating"
        ),
        old_button_text="Sign up to volunteer",
    ),
    DoorContent(
        door=HubCard.Kind.LEDGER,
        who_for="Lee and the core team",
        title="PL Ledger",
        bullets=(
            "Running tally and goals\n"
            "$900 drinks budget and receipts\n"
            "Drink sales, the door count, vendors\n"
            "Guild demos, staff time, board summary"
        ),
        button_text="Enter the ledger",
        # The bullets before #1307 removed the wine wall and the flyer page: a card still carrying them updates.
        old_bullets=(
            "Running tally and goals\n"
            "$900 drinks budget and receipts\n"
            "Drink sales, wine wall, vendors\n"
            "Guild demos, flyers, board summary"
        ),
        old_button_text="Open the ledger",
    ),
    DoorContent(
        door=HubCard.Kind.VENDOR_TABLES,
        who_for="Kate and the vendor team",
        title="Vendor Tables",
        bullets=(
            "Every table request in one place\n"
            "Requested, confirmed or waitlist\n"
            "Table numbers and setup days\n"
            "Capacity from the space map"
        ),
        button_text="Enter vendor tables",
        old_bullets="Every table and who is at it\nLoad in times and what each vendor needs\nA map of the floor",
        old_button_text="See the tables",
    ),
)


class ShiftPhase(models.TextChoices):
    """When a volunteer shift happens, in page order. ``VolunteerShift.Phase`` is this."""

    BEFORE = "before", "Before the event"
    SETUP = "setup", "Setup, Friday"
    DURING = "during", "During the event"
    AFTER = "after", "After"


# Shifts sort by phase in the order above, then by their own sort order. Built here so the
# model's Meta can carry it; a nested Meta cannot see the class body it sits in.
_PHASE_ORDER = Case(
    *(When(phase=value, then=Value(position)) for position, value in enumerate(ShiftPhase.values)),
    default=Value(len(ShiftPhase.values)),
    output_field=IntegerField(),
)


class VolunteerShiftQuerySet(models.QuerySet["VolunteerShift"]):
    def with_counts(self) -> VolunteerShiftQuerySet:
        """Each shift with ``filled``, how many people are signed up, in one query."""
        return self.annotate(filled=Count("signups"))


class VolunteerShift(models.Model):
    """One job on the volunteer sign-up sheet: a role, when it happens and how many are needed."""

    Phase = ShiftPhase

    hub = models.ForeignKey(
        EventHub, on_delete=models.CASCADE, related_name="shifts", help_text="The HQ this shift belongs to."
    )
    phase = models.CharField(
        max_length=10,
        choices=ShiftPhase.choices,
        default=ShiftPhase.DURING,
        help_text="When the shift happens; the page groups shifts by it, before to after.",
    )
    role = models.CharField(max_length=120, help_text="The job, e.g. Bartender, first half.")
    when_text = models.CharField(
        max_length=120, blank=True, help_text="When, in words, e.g. Fri Dec 4, 5 to 7:30 PM or November, your schedule."
    )
    need = models.PositiveSmallIntegerField(default=1, help_text="How many people this shift needs.")
    description = models.TextField(blank=True, help_text="What the job involves, a line or two.")
    requires_olcc = models.BooleanField(
        default=False, help_text="Whether the shift needs an Oregon OLCC service permit (bartending does)."
    )
    sort_order = models.PositiveIntegerField(default=0, help_text="Position within its phase; lower comes first.")

    objects = VolunteerShiftQuerySet.as_manager()

    class Meta:
        ordering = [_PHASE_ORDER, "sort_order", "pk"]

    def __str__(self) -> str:
        return self.role

    @property
    def filled_count(self) -> int:
        """How many are signed up: the ``with_counts`` annotation when the row carries it, else a count."""
        if "filled" in self.__dict__:
            filled: int = self.__dict__["filled"]
            return filled
        return self.signups.count()

    @property
    def spots_open(self) -> int:
        """Spots still open: ``need`` less the sign-ups, never below zero."""
        return max(0, self.need - self.filled_count)

    @property
    def is_full(self) -> bool:
        return self.spots_open == 0


class ShiftSignup(models.Model):
    """One person on one shift, with the details they gave on the Your Info step.

    ``member`` is set when the person was signed in; everyone is keyed by ``email``, lowercased
    on save, so a guest and a member with the same address are one person and one shift holds
    an address once (``uq_shiftsignup_shift_email``).
    """

    # The longest note Your Info accepts; the column is sized to it.
    MAX_NOTE_LENGTH = 500

    shift = models.ForeignKey(
        VolunteerShift, on_delete=models.CASCADE, related_name="signups", help_text="The shift signed up for."
    )
    member = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shift_signups",
        help_text="The member, when the person was signed in. Blank for a guest.",
    )
    name = models.CharField(max_length=120, help_text="The person's name as they gave it.")
    email = models.EmailField(help_text="The person's email, lowercased; one address per shift.")
    phone = models.CharField(max_length=40, blank=True, help_text="A phone number, optional.")
    has_olcc = models.BooleanField(default=False, help_text="Whether they hold an Oregon OLCC service permit.")
    note = models.CharField(
        max_length=MAX_NOTE_LENGTH,
        blank=True,
        help_text="Anything they wanted the organisers to know, up to 500 characters.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When they signed up.")

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["shift", "email"], name="uq_shiftsignup_shift_email"),
        ]

    def __str__(self) -> str:
        return f"{self.name} on {self.shift.role}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


# ---- Shared formatting ----


def format_money(cents: int) -> str:
    """``cents`` as dollars for the wall: "$25" when whole, "$25.50" otherwise, thousands separated."""
    dollars, rest = divmod(cents, 100)
    return f"${dollars:,}" if rest == 0 else f"${dollars:,}.{rest:02d}"


# ---- Vendor Tables (Track D, part 3) ----


class VendorTableSize(models.TextChoices):
    """The table a vendor asks for, in the order of Lee's list. ``VendorTable.Size`` is this."""

    SIX_FT = "six_ft", "6 ft"
    EIGHT_FT = "eight_ft", "8 ft"
    FOUR_FT = "four_ft", "4 ft"
    HALF = "half", "Half table (share)"
    OWN = "own", "Bringing their own"
    UNSURE = "unsure", "Not sure"


def _setup_window(key: str) -> str:
    """One setup window's words from :data:`hq.holiday_bizarre.VENDOR_SETUP_WINDOWS`, the labels' one source.

    Read when the label renders, not at import: ``hq.holiday_bizarre`` imports this module.
    """
    from hq.holiday_bizarre import VENDOR_SETUP_WINDOWS

    return VENDOR_SETUP_WINDOWS[key]


_setup_label = lazy(_setup_window, str)


class VendorSetup(models.TextChoices):
    """The setup window a vendor picked, blank while not chosen. ``VendorTable.Setup`` is this."""

    NOT_CHOSEN = "", "Not chosen"
    THU = "thu", _setup_label("thu")
    FRI = "fri", _setup_label("fri")


class VendorTableStatus(models.TextChoices):
    """Where a table request stands, in the list's order. ``VendorTable.Status`` is this."""

    REQUESTED = "requested", "Requested"
    CONFIRMED = "confirmed", "Confirmed"
    WAITLIST = "waitlist", "Waitlist"
    CANCELLED = "cancelled", "Cancelled"


# The list's order: requests waiting on the vendor team first, then the confirmed tables, the waitlist, the cancelled.
_VENDOR_STATUS_ORDER = Case(
    *(When(status=value, then=Value(position)) for position, value in enumerate(VendorTableStatus.values)),
    default=Value(len(VendorTableStatus.values)),
    output_field=IntegerField(),
)


class VendorTable(models.Model):
    """One member artist's (or group's) table request for the market, entered by the vendor team.

    :func:`hq.services.vendor_band` counts these by status; its ``confirmed`` is the one source of the
    Member tables goal and the ledger's vendor figure.
    """

    Size = VendorTableSize
    Setup = VendorSetup
    Status = VendorTableStatus

    hub = models.ForeignKey(
        EventHub,
        on_delete=models.CASCADE,
        related_name="vendor_tables",
        help_text="The HQ whose market this table request is for.",
    )
    name = models.CharField(max_length=160, help_text="The artist or group, e.g. Riverbend Ceramics.")
    guild = models.ForeignKey(
        Guild,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hq_vendor_tables",
        help_text="The guild they sell with. Blank means not in a guild.",
    )
    sells = models.CharField(max_length=200, blank=True, help_text="What they sell, e.g. Mugs and planters.")
    size = models.CharField(
        max_length=10,
        choices=VendorTableSize.choices,
        default=VendorTableSize.SIX_FT,
        help_text="The table they asked for.",
    )
    email = models.EmailField(blank=True, help_text="Their email, lowercased. Organisers only.")
    phone = models.CharField(max_length=40, blank=True, help_text="Their phone. Organisers only.")
    setup = models.CharField(
        max_length=3,
        choices=VendorSetup.choices,
        blank=True,
        default=VendorSetup.NOT_CHOSEN,
        help_text="The setup window they picked; blank while not chosen.",
    )
    needs = models.CharField(max_length=200, blank=True, help_text="What they need, e.g. Power, near a wall.")
    status = models.CharField(
        max_length=10,
        choices=VendorTableStatus.choices,
        default=VendorTableStatus.REQUESTED,
        help_text="Where the request stands: requested, confirmed, waitlist or cancelled.",
    )
    table_no = models.CharField(max_length=10, blank=True, help_text="The table number on the floor, once given.")
    note = models.CharField(
        max_length=500,
        blank=True,
        help_text="The vendor team's note on where the request came from (the sheet's Source column).",
    )
    requested_on = models.DateField(
        null=True, blank=True, help_text="The day the vendor asked for a table; blank when nobody recorded it."
    )
    request_source = models.CharField(
        max_length=60, blank=True, help_text="How the request came in, e.g. Discord, Asked Lee, Kate's sign-up doc."
    )
    discord_handle = models.CharField(max_length=100, blank=True, help_text="Their Discord name, as they gave it.")
    created_by = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="vendor_tables_added",
        help_text="The organiser who entered the request.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the request was entered.")
    updated_at = models.DateTimeField(
        auto_now=True, help_text="When the request was last saved; a row's Save older than this is refused."
    )

    class Meta:
        ordering = [_VENDOR_STATUS_ORDER, Lower("name"), "pk"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    @property
    def guild_display(self) -> str:
        """The guild's name, or "Not in a guild" for none."""
        return self.guild.name if self.guild is not None else "Not in a guild"


class VendorFloor(models.Model):
    """How many tables the space map fits on a hub, and the mapper's note. One per hub, made on first save."""

    hub = models.OneToOneField(
        EventHub,
        on_delete=models.CASCADE,
        related_name="vendor_floor",
        help_text="The HQ whose market floor this is.",
    )
    capacity = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="How many tables fit, from the space map. Blank while the map is not ready.",
    )
    map_note = models.CharField(
        max_length=200, blank=True, help_text="The mapper's note, e.g. 18 in the gallery, 6 in the shop."
    )
    discord_channel_id = models.CharField(
        max_length=32,
        blank=True,
        help_text="The Discord channel where members ask for tables, by its id (digits); blank hides Check Discord.",
    )

    def __str__(self) -> str:
        return f"{self.hub.title}: {self.capacity if self.capacity is not None else 'not mapped'}"


class VendorDiscordAction(models.TextChoices):
    """What the vendor team did with a Discord post on Check Discord. ``VendorDiscordDecision.Action`` is this."""

    ADDED = "added", "Added as a request"
    SKIPPED = "skipped", "Skipped"


class VendorDiscordDecision(models.Model):
    """One Discord post the vendor team has dealt with on Check Discord (#1014): added as a request or skipped.

    Check Discord leaves out every post with a decision, so a skipped poster comes back only with a new post.
    One row per post per hub (``uq_vendordiscord_hub_msg``).
    """

    Action = VendorDiscordAction

    hub = models.ForeignKey(
        EventHub,
        on_delete=models.CASCADE,
        related_name="vendor_discord_decisions",
        help_text="The HQ whose Vendor Tables the post was checked on.",
    )
    message_id = models.CharField(max_length=32, help_text="The Discord message's id.")
    author_id = models.CharField(max_length=32, help_text="The Discord id of who posted it.")
    action = models.CharField(
        max_length=10, choices=VendorDiscordAction.choices, help_text="Added as a request, or skipped."
    )
    decided_by = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who pressed Add as Request or Skip.",
    )
    decided_at = models.DateTimeField(auto_now_add=True, help_text="When they pressed it.")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["hub", "message_id"], name="uq_vendordiscord_hub_msg")]

    def __str__(self) -> str:
        return f"{self.message_id}: {self.get_action_display()}"


def format_hours(hours: Decimal) -> str:
    """Hours as Lee's page writes them: "2.5", "3", "0.25", with no trailing zeros, thousands separated."""
    whole = f"{hours:,.2f}".rstrip("0").rstrip(".")
    return whole or "0"


def hours_words(hours: Decimal) -> str:
    """Hours with the word in the right number: "1 hour", "1.5 hours", "2 hours", "0.25 hours"."""
    return f"{format_hours(hours)} {'hour' if hours == 1 else 'hours'}"


# The bounds of one hours entry: a quarter hour up to a whole day, in quarter hours.
HOURS_MIN = Decimal("0.25")
HOURS_MAX = Decimal("24")
HOURS_STEP = Decimal("0.25")


def is_pdf_name(name: str) -> bool:
    """Whether a stored receipt is a PDF, by its name: the upload form names a PDF ``.pdf`` and an image is
    re-encoded to ``.jpg`` on save, so the extension is the type the content was checked as."""
    return name.lower().endswith(".pdf")


# ---- PL Ledger (Track D, part 6a) ----

# Lee's board approval for the drinks budget and its note, the defaults of a new :class:`LedgerSettings`.
LEDGER_APPROVED_CENTS = 90_000
LEDGER_APPROVED_NOTE = "Drinks budget, Oct 14 meeting"


class LedgerSettings(models.Model):
    """The ledger's figures for a hub that are typed once rather than logged (OneToOne, made on the first save).

    D6a edits the board approval and its note. The drink prices and last year's bar sales are D6b's (Set drink
    prices, writing only its own fields). ``hq.services.ledger_settings`` hands back an unsaved one with the defaults, so a GET never
    writes.
    """

    hub = models.OneToOneField(
        EventHub,
        on_delete=models.CASCADE,
        related_name="ledger_settings",
        help_text="The HQ whose ledger this is.",
    )
    approved_cents = models.PositiveIntegerField(
        default=LEDGER_APPROVED_CENTS, help_text="The board's approval for the drinks budget, in cents."
    )
    approved_note = models.CharField(
        max_length=120,
        blank=True,
        default=LEDGER_APPROVED_NOTE,
        help_text="The line under Board approval, e.g. Drinks budget, Oct 14 meeting.",
    )
    ipa_cents = models.PositiveIntegerField(default=600, help_text="The bar's IPA price, in cents (D6b).")
    pils_cents = models.PositiveIntegerField(default=600, help_text="The bar's Pilsner price, in cents (D6b).")
    wine_cents = models.PositiveIntegerField(default=800, help_text="The bar's wine price, in cents (D6b).")
    na_cents = models.PositiveIntegerField(default=300, help_text="The bar's N/A drink price, in cents (D6b).")
    last_year_bar_cents = models.PositiveIntegerField(
        default=50_000, help_text="What the bar sold in 2025, in cents, to beat (D6b)."
    )
    updated_at = models.DateTimeField(auto_now=True, help_text="When the settings were last saved.")

    def __str__(self) -> str:
        return f"Ledger settings for {self.hub.title}"

    @property
    def approval_words(self) -> str:
        """The approval as the ledger's labels say it, e.g. "$900" in "Paid from board $900"."""
        return format_money(self.approved_cents)


class BudgetCategory(models.TextChoices):
    """A budget line's category, Lee's five. Only Drinks lines count toward the bar's cost (D6b reads it)."""

    DRINKS = "drinks", "Drinks"
    EVENT_SUPPLIES = "event_supplies", "Event supplies"
    MARKETING = "marketing", "Marketing"
    STAFFING = "staffing", "Staffing"
    DECOR = "decor", "Decor"


class BudgetLine(models.Model):
    """One line of the event's budget: what it is for, its category and what was budgeted.

    How each line was covered (sponsored, paid from the approval, paid from other funds) is never stored: the
    ledger's entries are summed per line by :func:`hq.services.ledger_band`. A line with entries cannot be deleted
    (``LedgerEntry.line`` is RESTRICT).
    """

    Category = BudgetCategory

    hub = models.ForeignKey(
        EventHub,
        on_delete=models.CASCADE,
        related_name="budget_lines",
        help_text="The HQ whose budget this line is on.",
    )
    name = models.CharField(max_length=120, help_text="The line's name, e.g. Kegs or Wine wall tags.")
    category = models.CharField(
        max_length=20,
        choices=BudgetCategory.choices,
        default=BudgetCategory.DRINKS,
        help_text="Which part of the budget the line is in. Drinks lines are the bar's cost.",
    )
    budget_cents = models.PositiveIntegerField(default=0, help_text="What was budgeted for the line, in cents.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Position in the table; lower comes first.")
    updated_at = models.DateTimeField(
        auto_now=True, help_text="When the line was last saved; a Save older than this is refused."
    )

    class Meta:
        ordering = ["sort_order", Lower("name"), "pk"]

    def __str__(self) -> str:
        return self.name

    @property
    def budget_display(self) -> str:
        return format_money(self.budget_cents)


class LedgerKind(models.TextChoices):
    """What a ledger entry is, Lee's four. ``LedgerEntry.Kind`` is this.

    The labels name Lee's $900; the page and the CSV say the hub's approval instead
    (:func:`hq.services.ledger_kind_label`).
    """

    SPONSORED = "sponsored", "Sponsored or donated"
    PAID_APPROVAL = "paid_approval", "Paid from board $900"
    PAID_OTHER = "paid_other", "Paid from other PLM funds"
    REFUND = "refund", "Refund back to $900"


# The kinds that are money spent or returned, and so take a receipt: everything but a sponsored gift.
RECEIPT_KINDS: frozenset[str] = frozenset({LedgerKind.PAID_APPROVAL, LedgerKind.PAID_OTHER, LedgerKind.REFUND})


def receipt_upload_to(instance: LedgerEntry, filename: str) -> str:
    """Where a receipt is stored: a random name under ``hq/receipts/`` with the checked file's extension, so a
    receipt's address cannot be guessed from what the phone called the photo."""
    return f"hq/receipts/{uuid4().hex}{Path(filename).suffix.lower()}"


class LedgerEntry(models.Model):
    """One line in the ledger: a gift sponsored or donated, a purchase paid from the approval or from other funds,
    or a refund back to the approval, against a budget line.

    ``amount_cents`` is always above zero; a refund counts against what was used. A receipt (an image, re-encoded to
    a JPEG at the hero's long edge on save, or a PDF as it came) is taken by every kind but a sponsored gift; the
    file a replace, a clear or a delete leaves behind is deleted from storage once the database change commits.
    """

    Kind = LedgerKind

    hub = models.ForeignKey(
        EventHub,
        on_delete=models.CASCADE,
        related_name="ledger_entries",
        help_text="The HQ whose ledger this entry is in.",
    )
    line = models.ForeignKey(
        BudgetLine,
        # RESTRICT, not PROTECT: a line with entries cannot be deleted on its own, but deleting the hub takes the lines
        # and the entries together (PROTECT would make a hub with a ledger undeletable).
        on_delete=models.RESTRICT,
        related_name="entries",
        help_text="The budget line it covers. A line with entries cannot be deleted.",
    )
    kind = models.CharField(
        max_length=20,
        choices=LedgerKind.choices,
        default=LedgerKind.SPONSORED,
        help_text="Sponsored or donated, paid from the approval, paid from other funds, or refunded to the approval.",
    )
    source = models.CharField(
        max_length=160, help_text="The sponsor, the vendor, or who refunded it, e.g. Cascade Brewing."
    )
    amount_cents = models.PositiveIntegerField(
        help_text="The value or the amount, in cents, above zero. A refund counts back against what was used."
    )
    date = models.DateField(default=timezone.localdate, help_text="The day it happened, Portland time.")
    note = models.CharField(max_length=200, blank=True, help_text="Anything else, e.g. 1 half-barrel IPA, in-kind.")
    receipt = models.FileField(
        upload_to=receipt_upload_to,
        blank=True,
        help_text="The receipt: a photo (resized on upload) or a PDF. Not taken by a sponsored gift.",
    )
    created_by = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ledger_entries_added",
        help_text="The organiser who logged it.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was logged.")
    updated_at = models.DateTimeField(auto_now=True, help_text="When it was last saved.")

    class Meta:
        ordering = ["-date", "-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount_cents__gt=0), name="ck_ledgerentry_amount"),
            models.CheckConstraint(
                condition=~Q(kind=LedgerKind.SPONSORED) | Q(receipt=""),
                name="ck_ledgerentry_receipt",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source} ({self.get_kind_display()})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Normalise a fresh photo, store the row and its new file, and only then delete the file it replaced.

        The old name is read before the save and deleted once the transaction commits (``HubFlyer.save``'s order), so a
        storage write or a database error that fails the save leaves the old receipt in place.
        """
        old = LedgerEntry.objects.filter(pk=self.pk).values_list("receipt", flat=True).first() if self.pk else None
        if not is_pdf_name(self.receipt.name or ""):
            normalize_field_if_uploaded(self, "receipt", settings.IMAGE_MAX_LONG_EDGE_HERO)
        super().save(*args, **kwargs)
        if old and old != (self.receipt.name or ""):
            transaction.on_commit(partial(delete_if_unreferenced, LedgerEntry, "receipt", old, exclude_pk=self.pk))

    @property
    def takes_receipt(self) -> bool:
        """Whether this kind of entry takes a receipt: money spent or returned, not a gift."""
        return self.kind in RECEIPT_KINDS

    @property
    def needs_receipt(self) -> bool:
        """Lee's Needs receipt chip: an entry that takes a receipt and has none yet."""
        return self.takes_receipt and not self.receipt

    @property
    def receipt_is_pdf(self) -> bool:
        return is_pdf_name(self.receipt.name or "")

    @property
    def amount_display(self) -> str:
        """The amount as the ledger shows it: a refund with a minus in front, e.g. "\u2212$40"."""
        money = format_money(self.amount_cents)
        return f"\u2212{money}" if self.kind == LedgerKind.REFUND else money

    @property
    def signed_cents(self) -> int:
        """The amount as the ledger counts it: a refund negative, everything else as logged."""
        return -self.amount_cents if self.kind == LedgerKind.REFUND else self.amount_cents


# ---- PL Ledger, money in (Track D, part 6b) ----

# The most drinks one bar sale or batch records. A night's bar is a few hundred; the cap keeps a typo (or a pasted
# number) a form error rather than a Postgres integer overflow.
BAR_QTY_MAX = 10_000
# The most one bar sale or batch takes in, in cents ($1,000,000, the ledger's cap).
BAR_AMOUNT_MAX_CENTS = 100_000_000


class BarDrink(models.TextChoices):
    """What was sold at the bar, Lee's four drinks and her total-only line. ``BarSale.Drink`` is this."""

    IPA = "ipa", "IPA"
    PILS = "pils", "Pilsner"
    WINE = "wine", "Wine"
    NA = "na", "N/A drink"
    MIXED = "mixed", "Mixed / total only"


# The four drinks with a price, in Lee's order: the quick-add buttons, the price form and the price fields' names.
PRICED_DRINKS: tuple[str, ...] = (BarDrink.IPA, BarDrink.PILS, BarDrink.WINE, BarDrink.NA)


class BarPay(models.TextChoices):
    """How a bar sale was paid, Lee's four. ``BarSale.Pay`` is this."""

    CARD = "card", "Card"
    CASH = "cash", "Cash"
    RSVP = "rsvp", "$5 RSVP credit"
    OTHER = "other", "Other"


class BarSaleSource(models.TextChoices):
    """Where a sale was logged: a +1 tap on the night, or a batch (end of shift, tip jar, cash box)."""

    QUICK = "quick", "Quick add"
    BATCH = "batch", "Batch"


class BarSale(models.Model):
    """One sale at the event's bar: a +1 tap (one drink at its price) or a batch with its total.

    A batch may be a tip jar or a cash box count with no drinks (``qty`` 0 and a total), or drinks given away (a total
    of 0), but never both zero. The bar's profit is these sales less the Drinks lines' cost
    (:func:`hq.services.bar_cost_cents`).
    """

    Drink = BarDrink
    Pay = BarPay
    Source = BarSaleSource

    hub = models.ForeignKey(
        EventHub,
        on_delete=models.CASCADE,
        related_name="bar_sales",
        help_text="The HQ whose bar this sale was at.",
    )
    drink = models.CharField(
        max_length=10,
        choices=BarDrink.choices,
        default=BarDrink.IPA,
        help_text="What was sold: IPA, Pilsner, wine, an N/A drink, or mixed (a total only).",
    )
    qty = models.PositiveIntegerField(
        default=1,
        validators=[MaxValueValidator(BAR_QTY_MAX)],
        help_text="How many drinks; 0 for a total with no count, such as the tip jar.",
    )
    amount_cents = models.PositiveIntegerField(
        validators=[MaxValueValidator(BAR_AMOUNT_MAX_CENTS)],
        help_text="What was taken in, in cents.",
    )
    pay = models.CharField(
        max_length=10,
        choices=BarPay.choices,
        default=BarPay.CARD,
        help_text="How it was paid: card, cash, the $5 RSVP credit, or other.",
    )
    note = models.CharField(max_length=200, blank=True, help_text="Anything else, e.g. 6 to 8 PM shift, Augustin.")
    source = models.CharField(
        max_length=10,
        choices=BarSaleSource.choices,
        default=BarSaleSource.QUICK,
        help_text="A +1 tap on the night, or a batch logged after.",
    )
    created_by = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bar_sales_added",
        help_text="The organiser who logged it. Undo last takes back only your own quick sales.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was logged.")

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(qty__gt=0) | Q(amount_cents__gt=0), name="ck_barsale_qty_or_amount"),
            models.CheckConstraint(
                condition=Q(qty__lte=BAR_QTY_MAX) & Q(amount_cents__lte=BAR_AMOUNT_MAX_CENTS), name="ck_barsale_caps"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.qty} {self.get_drink_display()} ({self.amount_display})"

    @property
    def amount_display(self) -> str:
        return format_money(self.amount_cents)

    @property
    def summary(self) -> str:
        """The sale as a toast says it, e.g. "1 IPA (Card)"."""
        return f"{self.qty} {self.get_drink_display()} ({self.get_pay_display()})"


# ---- PL Ledger, team and tally (Track D, part 6c) ----

# The door clicker's buttons, in Lee's order: +1, +5, +10, and Undo 1, which appends a -1.
DOOR_STEPS: tuple[int, ...] = (1, 5, 10, -1)


class DoorCount(models.Model):
    """One tap on the ledger's door clicker: walk-ins counted at the door (+1, +5 or +10), or Undo 1 (-1).

    Append-only, so Undo 1 adds a -1 rather than deleting a row, and :func:`hq.services.count_at_door` never lets the
    walk-ins go below zero. The attendance is the event's checked-in RSVPs plus the sum of these.
    """

    hub = models.ForeignKey(
        EventHub,
        on_delete=models.CASCADE,
        related_name="door_counts",
        help_text="The HQ whose door these people came through.",
    )
    delta = models.SmallIntegerField(help_text="How many came in: 1, 5 or 10, or -1 for Undo 1.")
    created_by = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hq_door_counts",
        help_text="The organiser who tapped.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was tapped.")

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(delta__in=DOOR_STEPS), name="ck_doorcount_delta"),
        ]

    def __str__(self) -> str:
        return f"{self.delta:+d} at the door"


class LaunchStatus(models.TextChoices):
    """Where a launch checklist task stands, Lee's three. ``LaunchTask.Status`` is this."""

    NOT_STARTED = "not_started", "Not started"
    IN_PROGRESS = "in_progress", "In progress"
    DONE = "done", "Done"


class LaunchTask(models.Model):
    """One line of the launch checklist: a task, who owns it, where it stands, when it is due and its link once live."""

    Status = LaunchStatus

    hub = models.ForeignKey(
        EventHub,
        on_delete=models.CASCADE,
        related_name="launch_tasks",
        help_text="The HQ whose launch checklist this task is on.",
    )
    task = models.CharField(max_length=200, help_text="What needs doing, e.g. Post on Nextdoor.")
    owner = models.CharField(max_length=120, blank=True, help_text="Who owns it, as typed. Optional.")
    status = models.CharField(
        max_length=12,
        choices=LaunchStatus.choices,
        default=LaunchStatus.NOT_STARTED,
        help_text="Not started, in progress or done.",
    )
    due = models.DateField(null=True, blank=True, help_text="When it is due, Portland time. Optional.")
    link = models.URLField(max_length=500, blank=True, help_text="A link to it once it is live. Optional.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Position on the list; a new task goes last.")
    updated_at = models.DateTimeField(
        auto_now=True, help_text="When the task was last saved; a row's Save older than this is refused."
    )

    class Meta:
        ordering = ["sort_order", "pk"]

    def __str__(self) -> str:
        return self.task

    def is_late(self, today: date) -> bool:
        """Lee's red due date: a due date before ``today`` (the Portland date) on a task not done."""
        return self.due is not None and self.due < today and self.status != LaunchStatus.DONE

    @property
    def href(self) -> str:
        """The link when it is a web address the list may open (http or https), else ""."""
        return self.link if self.link.lower().startswith(("http://", "https://")) else ""


class StaffArea(models.TextChoices):
    """What a stretch of staff time went to, Lee's twelve work areas in her order. ``StaffTime.Area`` is this."""

    PLANNING = "planning", "Planning and admin"
    SPONSORSHIPS = "sponsorships", "Sponsorships and donors"
    BOARD = "board", "Board approvals and reporting"
    VENDORS = "vendors", "Vendors and artists"
    VOLUNTEERS = "volunteers", "Volunteers"
    MARKETING = "marketing", "Marketing, flyer and website"
    GUILD_DEMOS = "guild_demos", "Guild demos"
    BAR_WINE = "bar_wine", "Bar and wine wall"
    TOOLS = "tools", "Building tracking tools"
    SETUP = "setup", "Setup and teardown"
    EVENT_NIGHT = "event_night", "Event night"
    OTHER = "other", "Other"


class StaffTime(models.Model):
    """One stretch of staff time the event took: who, when, how long, the work area and what they did.

    For the board's "what the event took, and what it returned" (:func:`hq.services.staff_totals`). ``who`` is as typed;
    ``member`` is set when it names one of the guild officers (``hq.access.officers``), and is cleared, not the row, if that
    member's record goes. The hours are held to the volunteer hours' bounds, in quarter hours.
    """

    Area = StaffArea

    hub = models.ForeignKey(
        EventHub,
        on_delete=models.CASCADE,
        related_name="staff_time",
        help_text="The HQ whose event this time went to.",
    )
    who = models.CharField(max_length=120, help_text="Who did the work, as typed, e.g. Lee.")
    member = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hq_staff_time",
        help_text="The ledger's named member Who names, when it names one.",
    )
    date = models.DateField(default=timezone.localdate, help_text="The day the work was done, Portland time.")
    hours = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        validators=[MinValueValidator(HOURS_MIN), MaxValueValidator(HOURS_MAX)],
        help_text="How long, from 0.25 to 24 in quarter hours.",
    )
    area = models.CharField(
        max_length=20,
        choices=StaffArea.choices,
        default=StaffArea.PLANNING,
        help_text="The work area, from Lee's list.",
    )
    note = models.CharField(max_length=200, blank=True, help_text="What they did, e.g. Built the sponsor tracker.")
    created_by = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hq_staff_time_logged",
        help_text="The organiser who logged it.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was logged.")

    class Meta:
        ordering = ["-date", "-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(hours__gte=HOURS_MIN) & Q(hours__lte=HOURS_MAX), name="ck_stafftime_range"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.who} {format_hours(self.hours)} h on {self.date}"

    @property
    def hours_display(self) -> str:
        return format_hours(self.hours)

    @property
    def hours_words(self) -> str:
        return hours_words(self.hours)


class GuildDemo(models.Model):
    """One guild on the ledger's Guild demos list: its lead, whether someone reached out and who, and whether it demos.

    ``guild`` is PROTECT: a demo row means nothing without its guild, and SET_NULL would leave nameless rows that the
    one-per-guild constraint no longer holds apart. Guilds are retired by turning them inactive (or the soft delete), so
    the protection only stops a hard delete from the Django admin, which names the rows in the way.
    """

    hub = models.ForeignKey(
        EventHub,
        on_delete=models.CASCADE,
        related_name="guild_demos",
        help_text="The HQ whose event the guild may demo at.",
    )
    guild = models.ForeignKey(
        Guild,
        on_delete=models.PROTECT,
        related_name="hq_guild_demos",
        help_text="The guild; one row per guild on a hub.",
    )
    lead = models.CharField(max_length=120, blank=True, help_text="The guild lead, as typed. Optional.")
    contacted = models.BooleanField(default=False, help_text="Whether someone has reached out to the guild.")
    contacted_by = models.CharField(max_length=120, blank=True, help_text="Who reached out, as typed. Optional.")
    demo = models.BooleanField(default=False, help_text="Whether the guild has a demo planned on the night.")
    note = models.CharField(max_length=200, blank=True, help_text="What they'll demo, e.g. Glass flameworking.")
    updated_at = models.DateTimeField(
        auto_now=True, help_text="When the row was last saved; a row's Save older than this is refused."
    )

    class Meta:
        ordering = ["guild__name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["hub", "guild"], name="uq_guilddemo_hub_guild"),
        ]

    def __str__(self) -> str:
        return f"{self.guild.name} ({self.hub.title})"
