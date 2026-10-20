"""The Holiday Bizarre HQ: one hub page per event, its door cards and its goal chips.

An :class:`EventHub` is the page Lee's organising hub became: a hero band for the event, a row
of goals, and nine door cards that each open one part of the event (RSVP, volunteer shifts,
the wine wall, and six placeholders that are not shared yet). The cards are rows, not code,
so an admin can retitle them, hide them, reorder them and open each one to the public, to
members, or to named people from the card's edit page. The event itself stays a
``CommunityEvent``: the hub only points at it for the date, the location and the RSVP door.

Part 1 of Track C builds the hub, the cards and the goals. Part 2 adds the volunteer shifts
behind the ``volunteers`` card: a :class:`VolunteerShift` is one job on Lee's sign-up sheet
(a role, when, how many are needed) and a :class:`ShiftSignup` is one person on it, with or
without an account. Part 3 adds the wine wall behind the ``wine_wall`` card: a
:class:`WineBottle` is one bottle (or a few of the same) logged with who gave it and a photo,
and a :class:`WineryLogo` is the logo an organiser uploads for a winery named on those bottles.

Track D turns the six placeholders into live doors, one part at a time; :data:`BUILT_DOORS` is
the set built so far. Part 1 adds the Marketing Tracker behind the ``marketing`` card: a
:class:`MarketingAction` is one thing the team did to get the word out. Part 2 adds the
Sponsorship Tracker behind the ``sponsorship`` card: a :class:`Sponsor` is one business the team
is asking, and a :class:`SponsorLogEntry` one line in its contact log. Part 3 adds Vendor Tables behind the
``vendor_tables`` card: a :class:`VendorTable` is one member artist's table request, and a :class:`VendorFloor`
the space map's capacity for the hub. Part 4 adds the Market Check-in behind the ``market_checkin`` card: a
:class:`MarketReport` is one member artist's sales after the market (one per member), and a
:class:`VolunteerHours` one stretch of prep or event time a member logged. Part 5 adds the event flyer and sharing
page behind the ``flyer`` card: a :class:`HubFlyer` holds this year's and last year's flyer, the website and Eventbrite
links and the message members copy to send friends. Part 6a adds the PL Ledger's budget behind the ``ledger`` card: a
:class:`LedgerSettings` holds the board's approval (and D6b's prices and tickets), a :class:`BudgetLine` one line of the
budget, and a :class:`LedgerEntry` one gift, purchase or refund against a line, with its receipt. Part 6b adds the money
in: a :class:`BarSale` is one sale (or a batch) at the bar, and a :class:`WineDonorThanks` marks a wine donor thanked.
Part 6c adds the team and the tally: a :class:`DoorCount` is one tap on the door clicker, a :class:`LaunchTask` one line of
the launch checklist, a :class:`StaffTime` one stretch of staff time and a :class:`GuildDemo` one guild's demo plans.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
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


class EventHubManager(models.Manager["EventHub"]):
    def open(self) -> EventHub | None:
        """The hub whose page ``/hq/`` opens, with its event, or None while no hub is open.

        The partial unique constraint keeps ``is_open`` to one row, so ``first()`` is exact.
        """
        return self.filter(is_open=True).select_related("event").first()


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
        """Lee's nine doors (:data:`DEFAULT_CARDS`) on this hub: made where missing, adopted, reworded, never doubled.

        A live door is found by kind (unique on a hub, so a renamed one is still found). A door whose page is
        built (:data:`BUILT_DOORS`) that the hub still holds as a placeholder with the door's title is turned
        into the live kind in place, so its pk, audience, named members, hidden flag and order stay; it is made
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
    # Live doors made on a hub that already had cards: admins only, and a renamed placeholder may sit beside them.
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
                f" New and admins only until you open it: {', '.join(self.added_beside)}."
                " If an old Not shared yet card for it is still on the page, delete that one."
            )
        return sentence


def _goals_phrase(labels: list[str]) -> str:
    """ "added the Member tables goal", or "added the RSVPs and Member tables goals" for several."""
    if len(labels) == 1:
        return f"added the {labels[0]} goal"
    return f"added the {', '.join(labels[:-1])} and {labels[-1]} goals"


class HubCard(models.Model):
    """One door on the HQ page: who it is for, what is behind it, and who may open it."""

    class Kind(models.TextChoices):
        RSVP = "rsvp", "RSVP"
        VOLUNTEERS = "volunteers", "Volunteer shifts"
        WINE_WALL = "wine_wall", "Wine wall"
        MARKETING = "marketing", "Marketing Tracker"
        SPONSORSHIP = "sponsorship", "Sponsorship Tracker"
        VENDOR_TABLES = "vendor_tables", "Vendor Tables"
        MARKET_CHECKIN = "market_checkin", "Market Check-in"
        FLYER = "flyer", "Event flyer and sharing"
        LEDGER = "ledger", "PL Ledger"
        PLACEHOLDER = "placeholder", "Not shared yet"

    class Audience(models.TextChoices):
        ADMINS = "admins", "Admins only"
        MEMBERS = "members", "Members"
        PUBLIC = "public", "Everyone, signed in or not"

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
    audience = models.CharField(
        max_length=20,
        choices=Audience.choices,
        default=Audience.ADMINS,
        help_text="Who may see and open the card. Admins and the named members always can.",
    )
    named_members = models.ManyToManyField(
        Member,
        blank=True,
        related_name="hq_cards",
        help_text="People who may open this card whatever its audience, and who organise what is behind it.",
    )
    is_hidden = models.BooleanField(default=False, help_text="Hidden cards show to admins only, with a Hidden chip.")

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

    def is_named(self, member: Member | None) -> bool:
        """Whether ``member`` is on the card's named list (reads a prefetch when the page made one)."""
        return member is not None and any(named.pk == member.pk for named in self.named_members.all())

    def is_open_to(self, member: Member | None, *, is_admin: bool) -> bool:
        """The one gate on a card: admins and named members always; ``public`` everyone; ``members`` an
        ACTIVE member. ``is_admin`` is the viewer's effective role, so a preview sees what the role would.
        Hidden is separate (the page and :func:`hq.access.card_or_404` keep hidden cards to admins)."""
        if is_admin or self.is_named(member):
            return True
        if self.audience == self.Audience.PUBLIC:
            return True
        if self.audience == self.Audience.MEMBERS:
            return member is not None and member.status == Member.Status.ACTIVE
        return False

    def can_organize(self, member: Member | None, *, is_admin: bool) -> bool:
        """Who runs what is behind the door: admins and the named members. ``is_admin`` is the actual role."""
        return is_admin or self.is_named(member)

    @property
    def url(self) -> str:
        """Where the card's button goes, or "" for a card that opens nothing yet (rendered as a plain tile)."""
        if self.kind == self.Kind.RSVP:
            return reverse("hub_event_detail", args=[self.hub.event_id]) if self.hub.event_id is not None else ""
        if self.kind == self.Kind.VOLUNTEERS:
            return reverse("hq:volunteers", args=[self.hub.slug])
        if self.kind == self.Kind.WINE_WALL:
            return reverse("hq:wine", args=[self.hub.slug])
        if self.kind == self.Kind.MARKETING:
            return reverse("hq:marketing", args=[self.hub.slug])
        if self.kind == self.Kind.SPONSORSHIP:
            return reverse("hq:sponsors", args=[self.hub.slug])
        if self.kind == self.Kind.VENDOR_TABLES:
            return reverse("hq:vendors", args=[self.hub.slug])
        if self.kind == self.Kind.MARKET_CHECKIN:
            return reverse("hq:checkin", args=[self.hub.slug])
        if self.kind == self.Kind.FLYER:
            return reverse("hq:flyer", args=[self.hub.slug])
        if self.kind == self.Kind.LEDGER:
            return reverse("hq:ledger", args=[self.hub.slug])
        # A live kind whose page has not shipped yet renders like a placeholder: Not shared yet.
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
    event's RSVPs, the volunteer shifts (C2), the wine wall (C3), the confirmed vendor tables (D3),
    the event cash (D6b: sponsors received, bar sales and wine wall tickets, in whole dollars) and the
    guilds with a demo planned on the ledger (D6c) by :func:`hq.services.goal_rows`. A goal with no target is a plain count.
    """

    class Kind(models.TextChoices):
        MANUAL = "manual", "Typed in by hand"
        RSVPS = "rsvps", "People going (RSVPs)"
        VOLUNTEER_SPOTS = "volunteer_spots", "Volunteer spots filled"
        BOTTLES = "bottles", "Wine wall bottles"
        BOTTLE_VALUE = "bottle_value", "Wine wall value"
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


# The doors whose page is built beyond the first three. The seed turns a placeholder titled as one of
# these into its live kind; each later part of Track D adds its kind here and nowhere else.
BUILT_DOORS: frozenset[str] = frozenset(
    {
        HubCard.Kind.MARKETING,
        HubCard.Kind.SPONSORSHIP,
        HubCard.Kind.VENDOR_TABLES,
        HubCard.Kind.MARKET_CHECKIN,
        HubCard.Kind.FLYER,
        HubCard.Kind.LEDGER,
    }
)

# The three doors that were live from the start of Track C.
_FIRST_DOORS: frozenset[str] = frozenset({HubCard.Kind.RSVP, HubCard.Kind.VOLUNTEERS, HubCard.Kind.WINE_WALL})


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
        """The card's fields for a new row (admins only, the model's default audience)."""
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


# Lee's nine doors, in her order (plan Appendix): her who-for lines and titles, her current bullets
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
        door=HubCard.Kind.WINE_WALL,
        who_for="Wine wall collectors",
        title="Log a wine bottle",
        bullets=(
            "Got a bottle? Log it here\n"
            "Snap a photo of the bottle\n"
            "See the bottle count and every supporter\n"
            "Add winery logos for our website"
        ),
        button_text="Enter a bottle",
        old_bullets="Log each bottle as it comes in\nSnap a photo of it\nSee the count and the supporters\nAdd winery logos",
        old_button_text="Enter a bottle",
    ),
    DoorContent(
        door=HubCard.Kind.LEDGER,
        who_for="Lee and the core team",
        title="PL Ledger",
        bullets=(
            "Running tally and goals\n"
            "$900 drinks budget and receipts\n"
            "Drink sales, wine wall, vendors\n"
            "Guild demos, flyers, board summary"
        ),
        button_text="Enter the ledger",
        old_bullets=(
            "Money in and money out for the night\nSponsorships, the wine wall and the door\nKept by the core team"
        ),
        old_button_text="Open the ledger",
    ),
    DoorContent(
        door=HubCard.Kind.SPONSORSHIP,
        who_for="Sponsorship team",
        title="Sponsorship Tracker",
        bullets=(
            "Every business we're asking\n"
            "Asks, commitments, money received\n"
            "Follow-up dates and contact log\n"
            "Sponsor tiers"
        ),
        button_text="Enter sponsorships",
        old_bullets="Who we asked and where it stands\nLevels and what each sponsor gets\nLogos for the flyer and the wall",
        old_button_text="Track sponsors",
    ),
    DoorContent(
        door=HubCard.Kind.MARKET_CHECKIN,
        who_for="Artists and volunteers",
        title="Market Check-in",
        bullets=("Artists: your market report\nVolunteers: log your prep hours\nVolunteer thank-you board"),
        button_text="Enter check-in",
        old_bullets=(
            "Artists and vendors checking in on the day\nTable numbers and who has arrived\nVolunteers check in here too"
        ),
        old_button_text="Check in",
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
    DoorContent(
        door=HubCard.Kind.FLYER,
        who_for="Members",
        title="Event flyer and sharing",
        bullets=(
            "This year's flyer to view, download and print\n"
            "Copy the link or share on Facebook\n"
            "Website, Eventbrite and member portal links\n"
            "Last year's flyer"
        ),
        button_text="See the flyer",
        old_bullets=(
            "The flyer to print and post\nGraphics to share on Instagram and by email\nWhere to drop flyers around town"
        ),
        old_button_text="Get the flyer",
    ),
    DoorContent(
        door=HubCard.Kind.MARKETING,
        who_for="Marketing team",
        title="Marketing Tracker",
        bullets=(
            "Businesses with flyers\n"
            "Instagram videos and posts\n"
            "Emails and event listings\n"
            "Every action, with views and links"
        ),
        button_text="Enter marketing",
        old_bullets="Posts, emails and press by date\nWho is doing what\nWhat went out and what is next",
        old_button_text="Track marketing",
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


# ---- Wine wall (part 3) ----


def format_money(cents: int) -> str:
    """``cents`` as dollars for the wall: "$25" when whole, "$25.50" otherwise, thousands separated."""
    dollars, rest = divmod(cents, 100)
    return f"${dollars:,}" if rest == 0 else f"${dollars:,}.{rest:02d}"


class WineKind(models.TextChoices):
    """What is in the bottle, in the order the band's chip row shows. ``WineBottle.Kind`` is this."""

    RED = "red", "Red"
    WHITE = "white", "White"
    CHAMPAGNE = "champagne", "Champagne"
    CIDER = "cider", "Cider"
    BEER = "beer", "Beer"


class GiverType(models.TextChoices):
    """Who gave a bottle, for the thank you list. ``WineBottle.GiverType`` is this."""

    PERSON = "person", "Person"
    WINE_SHOP = "wine_shop", "Wine shop"
    RESTAURANT = "restaurant", "Restaurant"
    WINERY = "winery", "Winery"
    DISTRIBUTOR = "distributor", "Distributor"
    OTHER = "other", "Other"


class SponsorListing(models.TextChoices):
    """Whether the giver wants to be listed on the sponsor page. ``WineBottle.Listing`` is this."""

    YES = "yes", "Yes"
    NO = "no", "No"
    NOT_ASKED = "not_asked", "Not asked"


class WineBottle(models.Model):
    """One bottle on the wine wall (``qty`` of the same bottle), with who gave it and who logged it.

    ``winery`` is free text: the Wineries section groups bottles under it whatever the
    capitalisation, and shows the :class:`WineryLogo` an organiser uploaded under that name.
    ``logged_by`` is the member who logged it (a guest has none) and ``credited_name`` the
    volunteer who collected it, which starts as the logger's own name.
    """

    Kind = WineKind
    GiverType = GiverType
    Listing = SponsorListing

    hub = models.ForeignKey(
        EventHub, on_delete=models.CASCADE, related_name="bottles", help_text="The HQ whose wall this bottle is on."
    )
    kind = models.CharField(
        max_length=12,
        choices=WineKind.choices,
        default=WineKind.RED,
        db_default=WineKind.RED,
        help_text="What is in the bottle: red, white, champagne, cider or beer.",
    )
    name = models.CharField(max_length=120, help_text="The bottle as on the label, e.g. 2021 Pinot Noir.")
    winery = models.CharField(
        max_length=120,
        help_text="The winery, cidery or brewery. Bottles group under it, whatever the capitalisation.",
    )
    value_cents = models.PositiveIntegerField(help_text="What one bottle is worth, in cents.")
    qty = models.PositiveSmallIntegerField(
        default=1,
        db_default=1,
        validators=[MinValueValidator(1)],
        help_text="How many of this bottle came in, at least 1.",
    )
    giver_name = models.CharField(max_length=120, help_text="Who gave it: a person or a business.")
    giver_type = models.CharField(
        max_length=12,
        choices=GiverType.choices,
        default=GiverType.PERSON,
        db_default=GiverType.PERSON,
        help_text="What kind of giver, for the thank you list.",
    )
    giver_email = models.EmailField(
        blank=True,
        help_text="The giver's email, lowercased. Private to the organisers, for the thank you note.",
    )
    listing = models.CharField(
        max_length=10,
        choices=SponsorListing.choices,
        default=SponsorListing.NOT_ASKED,
        db_default=SponsorListing.NOT_ASKED,
        help_text="Whether the giver wants to be listed on the sponsor page.",
    )
    credited_name = models.CharField(
        max_length=120, blank=True, help_text="The volunteer who collected it; starts as the logger's name."
    )
    photo = models.ImageField(
        upload_to="hq/bottles/", blank=True, help_text="A photo of the bottle, resized on upload. Optional."
    )
    note = models.TextField(blank=True, help_text="Anything else about the bottle or the giver.")
    logged_by = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wine_bottles",
        help_text="The member who logged it. Blank for a guest.",
    )
    logged_by_name = models.CharField(
        max_length=120, blank=True, help_text="The logger's name as it stood when the bottle was logged."
    )
    logged_by_email = models.EmailField(
        blank=True,
        help_text=(
            "The logger's email, lowercased: the Your Info address for a guest, the primary email for a "
            "member. Who may delete the bottle is keyed on it, as the volunteer sheet keys on its email."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was logged.")

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.name} ({self.winery})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.giver_email = self.giver_email.strip().lower()
        self.logged_by_email = self.logged_by_email.strip().lower()
        normalize_field_if_uploaded(self, "photo", settings.IMAGE_MAX_LONG_EDGE_GALLERY)
        delete_orphan_on_replace(self, "photo")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete the row, then its photo once no other bottle points at the file, and the winery's logo
        (row and file) once no bottle on the hub names that winery, whatever the capitalisation."""
        photo = self.photo.name or ""
        hub_id, winery = self.hub_id, self.winery
        result = super().delete(*args, **kwargs)
        delete_if_unreferenced(WineBottle, "photo", photo)
        if not WineBottle.objects.filter(hub_id=hub_id, winery__iexact=winery).exists():
            for logo in WineryLogo.objects.filter(hub_id=hub_id, name__iexact=winery):
                logo.delete()
        return result

    @property
    def value_total_cents(self) -> int:
        """What this row puts on the wall: the bottle's value times how many came in."""
        return self.value_cents * self.qty

    @property
    def value_display(self) -> str:
        """One bottle's value as dollars, e.g. "$25"."""
        return format_money(self.value_cents)

    def can_be_deleted_by(self, member: Member | None, *, email: str, is_organizer: bool) -> bool:
        """Whether this person may take the bottle off the wall: an organiser, the member who logged it, or,
        for a guest's bottle only, whoever holds the email it was logged under (the volunteer sheet's identity).

        A member's bottle never falls to the email match: typing that member's address into Your Info
        is not being them.
        """
        if is_organizer or (member is not None and self.logged_by_id == member.pk):
            return True
        if self.logged_by_id is not None:
            return False
        key = email.strip().lower()
        return bool(key) and key == self.logged_by_email


class WineryLogo(models.Model):
    """The logo for one winery, cidery or brewery on the wall, keyed by name (one per hub, any capitalisation)."""

    hub = models.ForeignKey(
        EventHub, on_delete=models.CASCADE, related_name="winery_logos", help_text="The HQ whose wall this logo is on."
    )
    name = models.CharField(
        max_length=120,
        help_text="The winery as the bottles name it. One logo per name on a hub, whatever the capitalisation.",
    )
    logo = models.ImageField(
        upload_to="hq/wineries/", help_text="The logo, resized on upload. The page shows it on a white tile."
    )

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(Lower("name"), "hub", name="uq_winerylogo_hub_name"),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        normalize_field_if_uploaded(self, "logo", settings.IMAGE_MAX_LONG_EDGE_PROFILE)
        delete_orphan_on_replace(self, "logo")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete the row, then its file once no other logo points at it."""
        logo = self.logo.name or ""
        result = super().delete(*args, **kwargs)
        delete_if_unreferenced(WineryLogo, "logo", logo)
        return result


# ---- Marketing Tracker (Track D, part 1) ----


class MarketingKind(models.TextChoices):
    """What a marketing action was, in the order of Lee's What list. ``MarketingAction.Kind`` is this."""

    FLYER = "flyer", "Flyer drop-off at a business"
    REEL = "reel", "Instagram video or reel"
    POST = "post", "Instagram post or story"
    EMAIL = "email", "Email or newsletter"
    LISTING = "listing", "Event listing (Eventbrite, calendars)"
    DISCORD = "discord", "Discord or member portal post"
    SOCIAL = "social", "Facebook or other social"
    PRESS = "press", "Press or media outreach"
    PARTNER = "partner", "Partner or business shout-out"
    OTHER = "other", "Other"


class MarketingAction(models.Model):
    """One thing the team did to get the word out: a flyer left at a shop, a reel, an email, a listing.

    The Marketing Tracker's totals count these rows by kind (:func:`hq.services.marketing_totals`);
    its flyer count is the one figure "Businesses with flyers" that every other door reads.
    """

    Kind = MarketingKind

    hub = models.ForeignKey(
        EventHub,
        on_delete=models.CASCADE,
        related_name="marketing_actions",
        help_text="The HQ whose marketing log this is on.",
    )
    kind = models.CharField(
        max_length=10,
        choices=MarketingKind.choices,
        default=MarketingKind.FLYER,
        help_text="What it was: a flyer drop-off, a reel, a post, an email, a listing and so on.",
    )
    date = models.DateField(default=timezone.localdate, help_text="The day it happened, Portland time.")
    who = models.CharField(max_length=120, blank=True, help_text="Who did it, as typed. Optional.")
    reach = models.PositiveIntegerField(default=0, help_text="Views or reach, when known; 0 when not.")
    details = models.CharField(max_length=300, help_text="What it was, e.g. Reel: Metal Guild sparks demo teaser.")
    link = models.URLField(max_length=500, blank=True, help_text="A link to the post, listing or email. Optional.")
    logged_by = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="marketing_actions",
        help_text="The organiser who logged it.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was logged.")

    class Meta:
        ordering = ["-date", "-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.details}"

    @property
    def reach_display(self) -> str:
        """The reach with thousands separated, or a dash when it is not known (0)."""
        return f"{self.reach:,}" if self.reach else "\u2013"

    @property
    def href(self) -> str:
        """The link when it is a web address the list may open (http or https), else ""."""
        return self.link if self.link.lower().startswith(("http://", "https://")) else ""


# ---- Sponsorship Tracker (Track D, part 2) ----


class SponsorTier(models.TextChoices):
    """The tier a sponsor is asked at, one list for the add form and the inline edit. ``Sponsor.Tier`` is this."""

    PRESENTING = "presenting", "Presenting ($5,000)"
    GOLD = "gold", "Gold ($2,500)"
    SILVER = "silver", "Silver ($1,000)"
    BAR = "bar", "Bar sponsor ($900)"
    COMMUNITY = "community", "Community ($500)"
    FRIEND = "friend", "Friend (under $500)"
    IN_KIND = "in_kind", "In-kind"


class SponsorStatus(models.TextChoices):
    """Where a sponsor stands, prospect to received (or declined). ``Sponsor.Status`` is this."""

    PROSPECT = "prospect", "Prospect"
    ASKED = "asked", "Asked"
    COMMITTED = "committed", "Committed"
    RECEIVED = "received", "Received"
    DECLINED = "declined", "Declined"


# The pipeline's order: commitments to chase first, then open asks, prospects, the money in, and the noes last.
_SPONSOR_STATUS_ORDER = Case(
    *(
        When(status=value, then=Value(position))
        for position, value in enumerate(
            (
                SponsorStatus.COMMITTED,
                SponsorStatus.ASKED,
                SponsorStatus.PROSPECT,
                SponsorStatus.RECEIVED,
                SponsorStatus.DECLINED,
            )
        )
    ),
    default=Value(len(SponsorStatus.values)),
    output_field=IntegerField(),
)

# A follow-up date stops counting once the money is in or the answer is no.
SPONSOR_CLOSED: frozenset[str] = frozenset({SponsorStatus.RECEIVED, SponsorStatus.DECLINED})


class Sponsor(models.Model):
    """One business or organisation the sponsorship team is asking, with where the ask stands.

    ``ask_cents`` is what was asked for; ``amount_cents`` what they committed to or paid, read by
    the status: committed money while ``committed``, money in the bank once ``received``.
    :func:`hq.services.sponsor_band` sums them; its received and committed figures are the one
    source every other door (the goals' Raised, the ledger) reads.
    """

    Tier = SponsorTier
    Status = SponsorStatus

    hub = models.ForeignKey(
        EventHub,
        on_delete=models.CASCADE,
        related_name="sponsors",
        help_text="The HQ whose tracker this sponsor is on.",
    )
    organization = models.CharField(
        max_length=160, help_text="The business or organisation, e.g. OnPoint Community Credit Union."
    )
    tier = models.CharField(
        max_length=12,
        choices=SponsorTier.choices,
        blank=True,
        help_text="The tier they are asked at. Blank while not set.",
    )
    ask_cents = models.PositiveIntegerField(default=0, help_text="What was asked for, in cents; 0 while not set.")
    amount_cents = models.PositiveIntegerField(
        default=0, help_text="What they committed to or paid, in cents: committed or received by the status."
    )
    status = models.CharField(
        max_length=10,
        choices=SponsorStatus.choices,
        default=SponsorStatus.PROSPECT,
        help_text="Where the ask stands: prospect, asked, committed, received or declined.",
    )
    owner = models.CharField(max_length=120, blank=True, help_text="Who on the team is asking, as typed.")
    contact_name = models.CharField(max_length=120, blank=True, help_text="Who we talk to there. Organisers only.")
    contact_role = models.CharField(
        max_length=120, blank=True, help_text="Their role, e.g. Branch manager. Organisers only."
    )
    email = models.EmailField(blank=True, help_text="The contact's email, lowercased. Organisers only.")
    phone = models.CharField(max_length=40, blank=True, help_text="The contact's phone. Organisers only.")
    next_step = models.CharField(max_length=200, blank=True, help_text="What happens next, e.g. Send sponsor deck.")
    follow_up_by = models.DateField(
        null=True, blank=True, help_text="When to follow up, Portland time; the Follow up list reads it."
    )
    fit_note = models.CharField(
        max_length=300, blank=True, help_text="Why they're a fit, e.g. PLM and 16 guild accounts bank with them."
    )
    benefits = models.CharField(
        max_length=300, blank=True, help_text="What we promised them, e.g. Logo on flyer, 4 drink tickets."
    )
    note = models.TextField(blank=True, help_text="Anything else the team should know. Organisers only.")
    thanked = models.BooleanField(default=False, help_text="Whether the thank-you has gone out.")
    history = models.TextField(
        blank=True, help_text="What they gave last year (2025, as a donation), as typed. Blank for a new sponsor."
    )
    created_by = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sponsors_added",
        help_text="The organiser who added the sponsor.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the sponsor was added.")

    class Meta:
        ordering = [_SPONSOR_STATUS_ORDER, Lower("organization"), "pk"]

    def __str__(self) -> str:
        return self.organization

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    @property
    def is_following_up(self) -> bool:
        """Whether the sponsor belongs on the Follow up list: a date, and the ask still open."""
        return self.follow_up_by is not None and self.status not in SPONSOR_CLOSED

    @property
    def organiser_line(self) -> str:
        """The small line under the name for organisers: the tier, the contact and their role, and who is asking."""
        contact = (
            f"{self.contact_name}, {self.contact_role}"
            if self.contact_name and self.contact_role
            else self.contact_name
        )
        parts = [self.get_tier_display() if self.tier else "", contact, f"Asking: {self.owner}" if self.owner else ""]
        return " · ".join(part for part in parts if part)

    @property
    def figure_display(self) -> str:
        """The row's figure: what they committed or paid when set, else the ask, else a dash."""
        if self.amount_cents:
            return format_money(self.amount_cents)
        if self.ask_cents:
            return format_money(self.ask_cents)
        return "–"

    @property
    def figure_label(self) -> str:
        """The word under the figure: received or committed for an amount, ask for an ask, else nothing."""
        if self.amount_cents:
            return "received" if self.status == SponsorStatus.RECEIVED else "committed"
        return "ask" if self.ask_cents else ""


class SponsorLogEntry(models.Model):
    """One line in a sponsor's contact log: a call, an email, a status change. Newest first."""

    sponsor = models.ForeignKey(
        Sponsor, on_delete=models.CASCADE, related_name="log_entries", help_text="The sponsor this line is about."
    )
    date = models.DateField(default=timezone.localdate, help_text="The day it happened, Portland time.")
    text = models.CharField(max_length=300, help_text="What happened, e.g. Called, left voicemail.")
    by = models.CharField(max_length=120, blank=True, help_text="Who did it, as typed. Optional.")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the line was logged.")

    class Meta:
        ordering = ["-date", "-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.sponsor.organization}: {self.text}"


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

    def __str__(self) -> str:
        return f"{self.hub.title}: {self.capacity if self.capacity is not None else 'not mapped'}"


# ---- Market Check-in (Track D, part 4) ----


def format_hours(hours: Decimal) -> str:
    """Hours as Lee's page writes them: "2.5", "3", "0.25", with no trailing zeros, thousands separated."""
    whole = f"{hours:,.2f}".rstrip("0").rstrip(".")
    return whole or "0"


class MarketReport(models.Model):
    """One member artist's market report: who they sell as, their guild, and after the market what they sold.

    One per member on a hub (an upsert: Save my report the second time updates it). Private to the member and
    the card's organisers. :func:`hq.services.checkin_band` sums the reports; its sales and item figures are the
    one source the ledger (D6) reads.

    ``member`` is CASCADE: the report is the member's own words and figures, so it goes when the person's row
    does, rather than a PROTECT that would stop an admin erasing someone over a three-line report. Members are
    retired by status, not deleted, so the totals do not move in practice.
    """

    hub = models.ForeignKey(
        EventHub,
        on_delete=models.CASCADE,
        related_name="market_reports",
        help_text="The HQ whose market this report is for.",
    )
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="hq_market_reports",
        help_text="The member who filed it; one report per member on a hub.",
    )
    name = models.CharField(max_length=160, help_text="Their name or shop name, e.g. Riverbend Ceramics.")
    guild = models.ForeignKey(
        Guild,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hq_market_reports",
        help_text="The guild they sell with. Blank means not in a guild.",
    )
    items_sold = models.PositiveIntegerField(
        null=True, blank=True, help_text="How many items they sold; blank until after the market."
    )
    sales_cents = models.PositiveIntegerField(default=0, help_text="Their total sales, in cents; 0 until reported.")
    experience = models.CharField(
        max_length=280, blank=True, help_text="Their experience at the market, in a sentence."
    )
    updated_at = models.DateTimeField(auto_now=True, help_text="When the report was last saved.")

    class Meta:
        ordering = [Lower("name"), "pk"]
        constraints = [
            models.UniqueConstraint(fields=["hub", "member"], name="uq_marketreport_hub_member"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.hub.title})"

    @property
    def sales_display(self) -> str:
        """The sales as dollars, e.g. "$420" or "$420.50"."""
        return format_money(self.sales_cents)

    @property
    def guild_display(self) -> str:
        """The guild's name, or "Not in a guild" for none."""
        return self.guild.name if self.guild is not None else "Not in a guild"


class VolunteerTask(models.TextChoices):
    """What a volunteer worked on, in the order of Lee's list. ``VolunteerHours.Task`` is this."""

    FLYERING = "flyering", "Flyering"
    SPONSOR_OUTREACH = "sponsor_outreach", "Sponsor outreach"
    WINE_WALL = "wine_wall", "Wine wall"
    GUILD_OUTREACH = "guild_outreach", "Guild outreach"
    SETUP = "setup", "Setup and decor"
    ADMIN = "admin", "Admin and planning"
    SOCIAL = "social", "Social media"
    EVENT_NIGHT = "event_night", "Event night"
    TEARDOWN = "teardown", "Teardown"
    OTHER = "other", "Other"


# The bounds of one hours entry: a quarter hour up to a whole day, in quarter hours.
HOURS_MIN = Decimal("0.25")
HOURS_MAX = Decimal("24")
HOURS_STEP = Decimal("0.25")


class VolunteerHours(models.Model):
    """One stretch of time a member gave the event: a date, how long, what on and a note.

    Not linked to the volunteer shifts (a shift is a sign-up for the night; these are hours given, before or on
    it). :func:`hq.services.checkin_band` sums them for the hero, the thank-you board and the ledger. ``member``
    is CASCADE for the reason :class:`MarketReport` gives.
    """

    Task = VolunteerTask

    hub = models.ForeignKey(
        EventHub,
        on_delete=models.CASCADE,
        related_name="volunteer_hours",
        help_text="The HQ these hours were given to.",
    )
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="hq_hours",
        help_text="The member who gave the time and logged it.",
    )
    date = models.DateField(default=timezone.localdate, help_text="The day the work was done, Portland time.")
    hours = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        validators=[MinValueValidator(HOURS_MIN), MaxValueValidator(HOURS_MAX)],
        help_text="How long, from 0.25 to 24 in quarter hours.",
    )
    task = models.CharField(
        max_length=20,
        choices=VolunteerTask.choices,
        default=VolunteerTask.FLYERING,
        help_text="What they worked on, from Lee's list.",
    )
    note = models.CharField(max_length=200, blank=True, help_text="A note, e.g. Dropped flyers on Division St.")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was logged.")

    class Meta:
        ordering = ["-date", "-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(hours__gte=HOURS_MIN) & Q(hours__lte=HOURS_MAX), name="ck_volunteerhours_range"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.member} {format_hours(self.hours)} h on {self.date}"

    @property
    def hours_display(self) -> str:
        return format_hours(self.hours)


# ---- Event flyer and sharing (Track D, part 5) ----


class FlyerSlot(models.TextChoices):
    """Which flyer an upload or a Remove is for: this year's or last year's. The values are the field names."""

    THIS_YEAR = "flyer", "2026 flyer"
    LAST_YEAR = "last_year_flyer", "2025 flyer"


def default_share_message() -> str:
    """Lee's "copy a message to send friends" text, the default for a new :class:`HubFlyer`.

    A callable so the migration names it rather than freezing the words, and a lazy import because
    :mod:`hq.holiday_bizarre` imports this module.
    """
    from hq.holiday_bizarre import FLYER_SHARE_MESSAGE

    return FLYER_SHARE_MESSAGE


def is_pdf_name(name: str) -> bool:
    """Whether a stored flyer is a PDF, by its name: the upload form names a PDF ``.pdf`` and an image is
    re-encoded to ``.jpg`` on save, so the extension is the type the content was checked as."""
    return name.lower().endswith(".pdf")


class HubFlyer(models.Model):
    """The event flyer and sharing page's content for a hub (OneToOne, made on the first save).

    This year's and last year's flyer are each an image (JPG, PNG or WEBP, re-encoded to a JPEG at the hero's long
    edge on save) or a PDF (stored as it came); the upload form checks the content, not the name. A replaced or
    cleared file is deleted from storage once the save that replaced it has committed, and both files once a delete
    of the row commits (``hq.signals``, so a hub deleted with everything on it takes them too). The RSVP figure and the businesses with flyers are never stored here:
    the page reads them live from the event and the Marketing Tracker.
    """

    Slot = FlyerSlot

    hub = models.OneToOneField(
        EventHub,
        on_delete=models.CASCADE,
        related_name="hub_flyer",
        help_text="The HQ whose flyer and sharing page this is.",
    )
    flyer = models.FileField(
        upload_to="hq/flyers/",
        blank=True,
        help_text="This year's flyer: a JPG, PNG or WEBP image (resized on upload) or a PDF. Blank shows Coming soon.",
    )
    last_year_flyer = models.FileField(
        upload_to="hq/flyers/",
        blank=True,
        help_text="Last year's flyer, shown under Last year: an image or a PDF. Optional.",
    )
    website_url = models.URLField(blank=True, help_text="The event page on the Past Lives website. Optional.")
    eventbrite_url = models.URLField(blank=True, help_text="The event on Eventbrite. Optional.")
    share_message = models.TextField(
        max_length=600,
        default=default_share_message,
        help_text="The message members copy to send friends; the share link is added after it.",
    )
    updated_at = models.DateTimeField(auto_now=True, help_text="When the page was last saved.")

    def __str__(self) -> str:
        return f"Flyer for {self.hub.title}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Normalise a fresh image, store the row and its new file, and only then delete the file each slot held.

        The old names are read before the save and deleted once the transaction commits, so a storage write or a
        database error that fails the save leaves the old flyer in place rather than a row pointing at nothing.
        """
        stored = HubFlyer.objects.filter(pk=self.pk).values(*FlyerSlot.values).first() if self.pk else None
        for slot in FlyerSlot.values:
            if not is_pdf_name(getattr(self, slot).name or ""):
                normalize_field_if_uploaded(self, slot, settings.IMAGE_MAX_LONG_EDGE_HERO)
        super().save(*args, **kwargs)
        for slot, old_name in (stored or {}).items():
            if old_name and old_name != (getattr(self, slot).name or ""):
                transaction.on_commit(partial(delete_if_unreferenced, HubFlyer, slot, old_name, exclude_pk=self.pk))

    @property
    def flyer_is_pdf(self) -> bool:
        return is_pdf_name(self.flyer.name or "")

    @property
    def last_year_is_pdf(self) -> bool:
        return is_pdf_name(self.last_year_flyer.name or "")

    @property
    def website_host(self) -> str:
        """The website link's host for its card under Find the event, e.g. "pastlives.space" (``www.`` dropped)."""
        host = urlsplit(self.website_url).hostname or ""
        return host.removeprefix("www.")


# ---- PL Ledger (Track D, part 6a) ----

# Lee's board approval for the drinks budget and its note, the defaults of a new :class:`LedgerSettings`.
LEDGER_APPROVED_CENTS = 90_000
LEDGER_APPROVED_NOTE = "Drinks budget, Oct 14 meeting"


class LedgerSettings(models.Model):
    """The ledger's figures for a hub that are typed once rather than logged (OneToOne, made on the first save).

    D6a edits the board approval and its note. The drink prices, last year's bar sales, the wine wall's ticket
    prices and the tickets sold are D6b's (Set drink prices, Set ticket prices and the tickets form, each writing only
    its own fields). ``hq.services.ledger_settings`` hands back an unsaved one with the defaults, so a GET never
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
    ticket_cents = models.PositiveIntegerField(default=2_500, help_text="One wine wall ticket, in cents (D6b).")
    pair_cents = models.PositiveIntegerField(default=4_000, help_text="The two ticket deal, in cents (D6b).")
    singles_sold = models.PositiveIntegerField(default=0, help_text="Single wine wall tickets sold (D6b).")
    pairs_sold = models.PositiveIntegerField(default=0, help_text="Two ticket deals sold (D6b).")
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


def donor_key(name: str) -> str:
    """The key a wine donor is known by: their name trimmed and lowercased, as the donors table groups them."""
    return name.strip().lower()


class WineDonorThanks(models.Model):
    """A wine donor marked thanked on the Wine Donors to Thank table. Present means thanked; Unmark deletes it.

    Keyed by the giver's name as the table groups it (:func:`donor_key`), one per hub whatever the capitalisation, so a
    thank-you follows the donor across every bottle they gave.
    """

    hub = models.ForeignKey(
        EventHub,
        on_delete=models.CASCADE,
        related_name="wine_thanks",
        help_text="The HQ whose wine wall the donor gave to.",
    )
    giver_key = models.CharField(
        max_length=120, help_text="The donor's name, trimmed and lowercased, as the donors table groups it."
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the donor was marked thanked.")

    class Meta:
        ordering = ["giver_key"]
        constraints = [
            models.UniqueConstraint(Lower("giver_key"), "hub", name="uq_winedonorthanks_hub_key"),
        ]

    def __str__(self) -> str:
        return f"Thanked {self.giver_key}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.giver_key = donor_key(self.giver_key)
        super().save(*args, **kwargs)


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
    ``member`` is set when it names one of the ledger card's named members, and is cleared, not the row, if that
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
