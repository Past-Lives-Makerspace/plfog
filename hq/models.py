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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db import models
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.db.models.functions import Lower
from django.urls import reverse
from django.utils import timezone

from core.files import delete_if_unreferenced, delete_orphan_on_replace
from core.images import normalize_field_if_uploaded
from membership.models import CommunityEvent, Member


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

    def seed_default_cards(self) -> list[HubCard]:
        """The nine door cards a hub starts with (:data:`DEFAULT_CARDS`), all admins only.

        Idempotent: a live kind the hub already has, or a placeholder with that title, is left
        as the admin edited it, so a second call adds nothing.
        """
        cards: list[HubCard] = []
        for position, spec in enumerate(DEFAULT_CARDS):
            lookup = {"kind": spec["kind"]}
            if spec["kind"] == HubCard.Kind.PLACEHOLDER:
                lookup["title"] = spec["title"]
            defaults = {key: value for key, value in spec.items() if key not in lookup}
            card, _ = HubCard.objects.get_or_create(hub=self, **lookup, defaults={**defaults, "sort_order": position})
            cards.append(card)
        return cards


class HubCard(models.Model):
    """One door on the HQ page: who it is for, what is behind it, and who may open it."""

    class Kind(models.TextChoices):
        RSVP = "rsvp", "RSVP"
        VOLUNTEERS = "volunteers", "Volunteer shifts"
        WINE_WALL = "wine_wall", "Wine wall"
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
    event's RSVPs, the volunteer shifts (C2) and the wine wall (C3) by :func:`hq.services.goal_rows`.
    """

    class Kind(models.TextChoices):
        MANUAL = "manual", "Typed in by hand"
        RSVPS = "rsvps", "People going (RSVPs)"
        VOLUNTEER_SPOTS = "volunteer_spots", "Volunteer spots filled"
        BOTTLES = "bottles", "Wine wall bottles"
        BOTTLE_VALUE = "bottle_value", "Wine wall value"

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
    target = models.PositiveIntegerField(default=0, help_text="The goal, a whole number (dollars for a money goal).")
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


# Lee's nine doors, in her order (plan Appendix). The three live kinds come first; the six
# placeholders keep her who-for and titles so the page already reads as the whole event.
DEFAULT_CARDS: tuple[dict[str, str], ...] = (
    {
        "kind": HubCard.Kind.RSVP,
        "who_for": "Members and guests",
        "title": "Going to the Bizarre?",
        "bullets": (
            "Tell us you're coming\n"
            "Get email updates before the night\n"
            "Eventbrite RSVPs are counted too\n"
            "Staff can add guests and check people in"
        ),
        "button_text": "I'm going",
    },
    {
        "kind": HubCard.Kind.VOLUNTEERS,
        "who_for": "Volunteers",
        "title": "Volunteer Sign-up",
        "bullets": (
            "Bartend, work the door or keep an eye on things\n"
            "Setup, teardown and cleanup crews\n"
            "Wine wall, tour guides and guild demos\n"
            "Flyer runs and decorating"
        ),
        "button_text": "Sign up to volunteer",
    },
    {
        "kind": HubCard.Kind.WINE_WALL,
        "who_for": "Wine wall collectors",
        "title": "Log a wine bottle",
        "bullets": (
            "Log each bottle as it comes in\nSnap a photo of it\nSee the count and the supporters\nAdd winery logos"
        ),
        "button_text": "Enter a bottle",
    },
    {
        "kind": HubCard.Kind.PLACEHOLDER,
        "who_for": "Lee and the core team",
        "title": "PL Ledger",
        "bullets": "Money in and money out for the night\nSponsorships, the wine wall and the door\nKept by the core team",
        "button_text": "Open the ledger",
    },
    {
        "kind": HubCard.Kind.PLACEHOLDER,
        "who_for": "Sponsorship team",
        "title": "Sponsorship Tracker",
        "bullets": "Who we asked and where it stands\nLevels and what each sponsor gets\nLogos for the flyer and the wall",
        "button_text": "Track sponsors",
    },
    {
        "kind": HubCard.Kind.PLACEHOLDER,
        "who_for": "Artists and volunteers",
        "title": "Market Check-in",
        "bullets": "Artists and vendors checking in on the day\nTable numbers and who has arrived\nVolunteers check in here too",
        "button_text": "Check in",
    },
    {
        "kind": HubCard.Kind.PLACEHOLDER,
        "who_for": "Kate and the vendor team",
        "title": "Vendor Tables",
        "bullets": "Every table and who is at it\nLoad in times and what each vendor needs\nA map of the floor",
        "button_text": "See the tables",
    },
    {
        "kind": HubCard.Kind.PLACEHOLDER,
        "who_for": "Members",
        "title": "Event flyer and sharing",
        "bullets": "The flyer to print and post\nGraphics to share on Instagram and by email\nWhere to drop flyers around town",
        "button_text": "Get the flyer",
    },
    {
        "kind": HubCard.Kind.PLACEHOLDER,
        "who_for": "Marketing team",
        "title": "Marketing Tracker",
        "bullets": "Posts, emails and press by date\nWho is doing what\nWhat went out and what is next",
        "button_text": "Track marketing",
    },
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
    note = models.TextField(blank=True, help_text="Anything they wanted the organisers to know.")
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
    qty = models.PositiveSmallIntegerField(default=1, db_default=1, help_text="How many of this bottle came in.")
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
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was logged.")

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.name} ({self.winery})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.giver_email = self.giver_email.strip().lower()
        normalize_field_if_uploaded(self, "photo", settings.IMAGE_MAX_LONG_EDGE_GALLERY)
        delete_orphan_on_replace(self, "photo")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete the row, then its photo once no other bottle points at the file."""
        photo = self.photo.name or ""
        result = super().delete(*args, **kwargs)
        delete_if_unreferenced(WineBottle, "photo", photo)
        return result

    @property
    def value_total_cents(self) -> int:
        """What this row puts on the wall: the bottle's value times how many came in."""
        return self.value_cents * self.qty

    @property
    def value_display(self) -> str:
        """One bottle's value as dollars, e.g. "$25"."""
        return format_money(self.value_cents)

    def can_be_deleted_by(self, member: Member | None, *, is_organizer: bool) -> bool:
        """Whether this person may take the bottle off the wall: an organiser, or the member who logged it.

        A guest's bottle has no ``logged_by``, so only an organiser can remove it.
        """
        return is_organizer or (member is not None and self.logged_by_id == member.pk)


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
